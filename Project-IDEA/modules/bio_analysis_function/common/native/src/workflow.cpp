/*
 * workflow.cpp —— 工作流的执行：完整处理链、五路分流写出、分卷
 *
 * 逐条处理顺序与每一步的来历见 workflow.h 的文件头，这里只写实现上的三处要点：
 *
 * 1. **三端的累加器是分开的**。``prepare`` 在读取线程里跑、``consume`` 在写出
 *    线程里跑，两者并发，因此各自持一套计数器（``ProducerTally`` /
 *    ``WriterTally``），等流水线把所有线程 join 回来之后再合并。共用一套就是
 *    数据竞争，而且这种竞争不会崩、只会悄悄算错数。
 * 2. **写出器必须声明在 try 内部**。失败时它要随栈展开先析构（关掉文件句柄），
 *    catch 里的 ``remove_file_if_exists`` 才会成功——Windows 上删一个仍被打开
 *    的文件会静默失败。
 * 3. **分卷写出器的产物不止一个文件**，所以它自己记着创建过哪些路径，
 *    失败时把这一批全删掉。
 */

#include "workflow.h"

#include <algorithm>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "dedup.h"
#include "fastq.h"
#include "index_filter.h"
#include "insert_size.h"
#include "normalize.h"
#include "overlap.h"
#include "paired_adapter_trim.h"
#include "paired_base_correction.h"
#include "paired_merge.h"
#include "paired_pipeline.h"
#include "pipeline.h"
#include "poly_trim.h"
#include "quality_trim.h"
#include "read_filter.h"
#include "sequence.h"
#include "umi_process.h"
#include "workflow_pipeline.h"

namespace bio {
namespace {

/* ======================================================================== */
/* 分卷写出                                                                  */
/* ======================================================================== */

/*
 * 给输出路径加一个序号前缀（上游格式：``0001.out.fq``）。
 *
 * 序号加在**文件名**前面而不是路径前面，因此分卷产物落在同一个目录里。
 * ``digits`` 为 0 时不补零。
 */
std::string numbered_name(const std::string& path, int64_t index, int32_t digits) {
    const std::size_t slash = path.find_last_of("/\\");
    const std::string directory =
        slash == std::string::npos ? std::string() : path.substr(0, slash + 1);
    const std::string base =
        slash == std::string::npos ? path : path.substr(slash + 1);

    std::string number = std::to_string(index);
    if (digits > 0 && number.size() < static_cast<std::size_t>(digits)) {
        number.insert(0, static_cast<std::size_t>(digits) - number.size(), '0');
    }
    return directory + number + "." + base;
}

/*
 * 按条数滚动的写出器。
 *
 * ``per_file <= 0`` 时退化成普通写出器（只写一个文件）。分卷时以实际写出的
 * 记录数为准滚动——写出端是单线程且按输入顺序的，所以"每卷多少条"是精确的，
 * 不需要事先估算总条数（上游 ``--split`` 那种估算口径在这里由调用方换算成
 * 确切的条数再传进来，见 workflow.h）。
 */
class SplitWriter {
public:
    SplitWriter(std::string path, bool compress, int64_t per_file, int32_t digits)
        : path_(std::move(path)), compress_(compress), per_file_(per_file),
          digits_(digits) {
        open_next();
    }

    SplitWriter(const SplitWriter&) = delete;
    SplitWriter& operator=(const SplitWriter&) = delete;

    void write(const FastqRecord& record) {
        if (per_file_ > 0 && records_in_file_ >= per_file_) {
            open_next();
        }
        writer_->write(record);
        ++records_in_file_;
    }

    void close() {
        if (writer_ != nullptr) {
            writer_->close();
        }
    }

    int32_t file_count() const { return file_count_; }

    /* 已经创建过的所有路径，供失败时清理。 */
    const std::vector<std::string>& created_paths() const { return created_; }

private:
    void open_next() {
        if (writer_ != nullptr) {
            writer_->close();
        }
        ++file_count_;
        const std::string target =
            per_file_ > 0 ? numbered_name(path_, file_count_, digits_) : path_;
        writer_ = std::make_unique<FastqWriter>(target, compress_);
        created_.push_back(target);
        records_in_file_ = 0;
    }

    std::string path_;
    bool compress_;
    int64_t per_file_;
    int32_t digits_;
    std::unique_ptr<FastqWriter> writer_;
    int64_t records_in_file_ = 0;
    int32_t file_count_ = 0;
    std::vector<std::string> created_;
};

/* 被丢弃的 read 写进失败输出时，在名字后追加原因（格式与上游一致）。 */
FastqRecord tagged_with_verdict(const FastqRecord& record, int32_t verdict) {
    FastqRecord out = record;
    out.name += ' ';
    out.name += pipeline_detail::verdict_label(verdict);
    return out;
}

/* 同上，但用一个自定义标签（上游的 ``paired_read_is_failing``）。 */
FastqRecord tagged_with_text(const FastqRecord& record, const char* tag) {
    FastqRecord out = record;
    out.name += ' ';
    out.name += tag;
    return out;
}

/* ======================================================================== */
/* 累加器                                                                    */
/* ======================================================================== */

/*
 * 读取线程的累加器。
 *
 * 过滤前统计放在这里而不是计算端，是为了对齐上游口径：上游在去重与按 index
 * 过滤**之前**就 `statRead`，所以被判重、被 index 命中的 read 也要计入
 * "before filtering"。放进 worker 的话那些 read 已经不在批次里了。
 */
struct ProducerTally {
    int64_t total_reads = 0;
    int64_t total_bases = 0;
    int64_t index_filtered_reads = 0;
    int64_t duplicate_reads = 0;
};

/* 写出线程的累加器。 */
struct WriterTally {
    int64_t pairs = 0;
    int64_t output_reads = 0;
    int64_t output_bases = 0;
    int64_t unpaired_reads = 0;
    int64_t normalized_reads = 0;
    int64_t umi_tagged_reads = 0;
    int64_t trimmed_reads = 0;
    int64_t poly_trimmed_reads = 0;
    int64_t adapter_trimmed_reads = 0;
    int64_t adapter_dimer_pairs = 0;
    int64_t corrected_pairs = 0;
    int64_t corrected_bases = 0;
    int64_t filtered_reads = 0;
    bio_filter_breakdown_t filter_breakdown{};
    int64_t pairs_merged = 0;
    int64_t gap_overlap_pairs = 0;
    std::vector<int64_t> insert_size_histogram;

    WriterTally() : insert_size_histogram(kWorkflowInsertSizeMax + 1, 0) {}
};

/* 按结果码给"按 read 计"的失败明细加一。 */
void note_filter_failure(bio_filter_breakdown_t& breakdown, int32_t verdict) {
    switch (verdict) {
        case kFailQuality: ++breakdown.failed_quality; break;
        case kFailNBase: ++breakdown.failed_n_base; break;
        case kFailLength: ++breakdown.failed_too_short; break;
        case kFailTooLong: ++breakdown.failed_too_long; break;
        case kFailComplexity: ++breakdown.failed_low_complexity; break;
        default: break;   /* 接头二聚体不计入这五项（它不是判据，是结论） */
    }
}

/* 把"档位 0 = 按模式取默认"解析成实际档位（与 ABI 层的口径一致）。 */
int32_t resolve_dedup_level(const WorkflowOptions& options) {
    if (options.dedup_accuracy_level > 0) {
        return options.dedup_accuracy_level;
    }
    return options.dedup_enabled ? kDefaultAccuracyDedup : kDefaultAccuracyAnalyze;
}

/* 片段长度落桶：判不出与超上限都进最后一个桶（与 insert_size.cpp 同口径）。 */
void note_insert_size(std::vector<int64_t>& histogram, int32_t size) {
    int32_t bucket = kWorkflowInsertSizeMax;
    if (size >= 0 && size <= kWorkflowInsertSizeMax) {
        bucket = size;
    }
    ++histogram[static_cast<std::size_t>(bucket)];
}

int32_t peak_of(const std::vector<int64_t>& histogram) {
    /* 峰值只在上限之内找：溢出桶里混着"判不出"的，算成峰值没有意义。
     * 并列时取较小的长度（严格大于比较，先遇到的胜出）——与上游一致。 */
    int32_t peak = 0;
    int64_t best = 0;
    for (int32_t index = 0; index < kWorkflowInsertSizeMax; ++index) {
        const int64_t count = histogram[static_cast<std::size_t>(index)];
        if (count > best) {
            best = count;
            peak = index;
        }
    }
    return peak;
}

/* ======================================================================== */
/* 单端                                                                      */
/* ======================================================================== */

struct SingleOutcome {
    bool keep = false;
    int32_t verdict = 0;
    FastqRecord record;   /* 修剪后的记录（keep 时写主输出） */
    FastqRecord failed;   /* 原始记录 + 原因标签 */
    bool has_failed = false;

    bool normalized = false;
    bool umi_tagged = false;
    bool trimmed = false;
    bool poly_trimmed = false;
    bool adapter_trimmed = false;
};

/*
 * 单端的逐条处理链。顺序照抄上游 ``SingleEndProcessor::processSingleEnd``。
 *
 * ``original`` 是读入的那条记录（未被任何步骤改过），失败输出要写它而不是
 * 修剪后的记录——上游写的就是原始记录。
 */
SingleOutcome process_single(const FastqRecord& original,
                             const WorkflowOptions& options,
                             bool want_failed) {
    SingleOutcome outcome;
    FastqRecord working = original;

    if (options.normalize_enabled) {
        bool renamed = false;
        bool requantified = false;
        normalize_record(working, options.normalize, renamed, requantified);
        outcome.normalized = renamed || requantified;
    }

    if (options.umi_enabled) {
        const UmiOutcome umi = apply_umi_to_reads(working, nullptr, options.umi);
        working = umi.read1;
        outcome.umi_tagged = umi.tagged1;
    }

    /* 首尾固定修剪 + 滑窗质量剪切。剪切把 read 剪空时上游返回空指针，
     * 后续步骤全部跳过，最终按"过短"丢弃。 */
    const TrimOutcome trim =
        compute_trim(working.sequence, working.quality, options.quality_cut);
    if (trim.dropped) {
        outcome.verdict = kFailLength;
        outcome.has_failed = want_failed;
        if (want_failed) {
            outcome.failed = tagged_with_verdict(original, kFailLength);
        }
        return outcome;
    }
    outcome.trimmed = trim.begin != 0 || trim.length != working.sequence.size();
    apply_trim(working, trim);

    if (options.poly_trim.enabled_poly_g) {
        const PolyTrimOutcome poly =
            trim_poly_g(working.sequence, options.poly_trim.min_length_poly_g);
        if (poly.trimmed_bases > 0) {
            apply_poly_trim(working, poly);
            outcome.poly_trimmed = true;
        }
    }

    if (options.adapter_enabled && !options.adapter.adapters.empty()) {
        const AdapterTrimOutcome adapter = apply_adapter_trim(working, options.adapter);
        if (adapter.trimmed) {
            outcome.adapter_trimmed = true;
        }
    }

    if (options.poly_trim.enabled_poly_x) {
        const PolyTrimOutcome poly =
            trim_poly_x(working.sequence, options.poly_trim.min_length_poly_x);
        if (poly.trimmed_bases > 0) {
            apply_poly_trim(working, poly);
            outcome.poly_trimmed = true;
        }
    }

    if (options.max_length1 > 0 &&
        working.sequence.size() > static_cast<std::size_t>(options.max_length1)) {
        working.sequence.resize(static_cast<std::size_t>(options.max_length1));
        working.quality.resize(static_cast<std::size_t>(options.max_length1));
    }

    outcome.verdict = filter_verdict(working.sequence, working.quality, options.filter);
    if (outcome.verdict != kPassFilter) {
        outcome.has_failed = want_failed;
        if (want_failed) {
            outcome.failed = tagged_with_verdict(original, outcome.verdict);
        }
        return outcome;
    }

    outcome.keep = true;
    outcome.record = std::move(working);
    return outcome;
}

void run_single_end(const WorkflowOptions& options, WorkflowResult& result) {
    /* 分卷生效时只写主输出（理由见 workflow.h；调用方已保证其余路径为空）。 */
    const bool split_enabled = options.split_records > 0;
    const bool want_failed = !options.failed_path.empty() && !split_enabled;

    std::unique_ptr<DuplicateDetector> detector;
    if (options.dedup_evaluate || options.dedup_enabled) {
        DedupOptions dedup_options;
        dedup_options.accuracy_level = resolve_dedup_level(options);
        dedup_options.buffer_bytes = options.dedup_buffer_bytes;
        dedup_options.dedup = options.dedup_enabled;
        detector = std::make_unique<DuplicateDetector>(dedup_options);
    }

    ProducerTally producer;
    WriterTally writer;

    FastqReader reader(options.read1_path);
    auto read_one = [&](FastqRecord& record) { return reader.next(record); };

    auto prepare = [&](const FastqRecord& record) -> bool {
        if (options.stats_enabled && options.stats.pre1 != nullptr) {
            options.stats.pre1->add(record.sequence, record.quality);
        }
        ++producer.total_reads;
        producer.total_bases += static_cast<int64_t>(record.length());

        if (options.index_filter_enabled &&
            is_index_filtered(record.name, nullptr, options.index_filter)) {
            ++producer.index_filtered_reads;
            return false;
        }
        if (detector != nullptr && detector->check_read(record.sequence)) {
            ++producer.duplicate_reads;
            if (options.dedup_enabled) {
                return false;
            }
        }
        return true;
    };

    auto step = [&](const FastqRecord& record) -> SingleOutcome {
        return process_single(record, options, want_failed);
    };

    std::unique_ptr<SplitWriter> main_writer;
    std::unique_ptr<FastqWriter> failed_writer;

    auto consume = [&](const SingleOutcome& outcome) {
        if (outcome.keep) {
            main_writer->write(outcome.record);
            ++writer.output_reads;
            writer.output_bases += static_cast<int64_t>(outcome.record.length());
            if (options.stats_enabled && options.stats.post1 != nullptr) {
                options.stats.post1->add(outcome.record.sequence,
                                         outcome.record.quality);
            }
        } else {
            ++writer.filtered_reads;
            note_filter_failure(writer.filter_breakdown, outcome.verdict);
            if (outcome.has_failed && failed_writer != nullptr) {
                failed_writer->write(outcome.failed);
            }
        }
        /* 以下都是"改过但没丢"的计数，与去留无关。 */
        if (outcome.normalized) ++writer.normalized_reads;
        if (outcome.umi_tagged) ++writer.umi_tagged_reads;
        if (outcome.trimmed) ++writer.trimmed_reads;
        if (outcome.poly_trimmed) ++writer.poly_trimmed_reads;
        if (outcome.adapter_trimmed) ++writer.adapter_trimmed_reads;
    };

    try {
        main_writer = std::make_unique<SplitWriter>(options.output1_path,
                                                    options.compress,
                                                    options.split_records,
                                                    options.split_digits);
        if (want_failed) {
            failed_writer = std::make_unique<FastqWriter>(options.failed_path,
                                                          options.compress);
        }
        run_workflow_pipeline<FastqRecord>(read_one, options.threads,
                                          AutoThreads::Hardware,
                                          options.max_reads, prepare, step, consume);
        main_writer->close();
        if (failed_writer != nullptr) {
            failed_writer->close();
        }
    } catch (...) {
        /* 写出器已经离开作用域（句柄关闭），这时删才删得掉。 */
        for (const std::string& path : main_writer->created_paths()) {
            remove_file_if_exists(path);
        }
        if (failed_writer != nullptr) {
            remove_file_if_exists(options.failed_path);
        }
        throw;
    }

    result.total_reads = producer.total_reads;
    result.total_bases = producer.total_bases;
    result.index_filtered_reads = producer.index_filtered_reads;
    result.duplicate_reads = producer.duplicate_reads;
    result.output_reads = writer.output_reads;
    result.output_bases = writer.output_bases;
    result.unpaired_reads = writer.unpaired_reads;
    result.normalized_reads = writer.normalized_reads;
    result.umi_tagged_reads = writer.umi_tagged_reads;
    result.trimmed_reads = writer.trimmed_reads;
    result.poly_trimmed_reads = writer.poly_trimmed_reads;
    result.adapter_trimmed_reads = writer.adapter_trimmed_reads;
    result.filtered_reads = writer.filtered_reads;
    result.filter_breakdown = writer.filter_breakdown;
    result.split_file_count = main_writer->file_count();
    result.dedup_accuracy_level = detector != nullptr ? detector->accuracy_level() : 0;
}

/* ======================================================================== */
/* 双端                                                                      */
/* ======================================================================== */

struct PairOutcome {
    /* 主输出：两端都通过时各写一条 */
    bool keep1 = false;
    bool keep2 = false;
    FastqRecord read1;
    FastqRecord read2;

    /* 落单 read（只有一端通过时） */
    bool has_unpaired = false;
    FastqRecord unpaired;

    /* 失败输出（写的是**原始**记录 + 原因标签，与上游一致） */
    bool has_failed_first = false;   /* 落在 R1 位置的那条 */
    FastqRecord failed_first;
    bool has_failed_second = false;  /* 落在 R2 位置的那条 */
    FastqRecord failed_second;

    /* 重叠区输出 */
    bool has_overlapped = false;
    FastqRecord overlapped;

    /* 合并模式 */
    bool merged = false;
    bool merged_passed = false;
    FastqRecord merged_record;

    int verdict1 = 0;
    int verdict2 = 0;
    int32_t insert_size = -1;
    bool gap_overlap = false;

    bool normalized = false;
    bool umi_tagged = false;
    bool trimmed = false;
    bool poly_trimmed = false;
    /* 被裁过接头的 **read 条数**（0~2）：上游的统计口径是按条累加，不是按对。 */
    int32_t adapter_trimmed_reads = 0;
    bool adapter_dimer = false;
    int64_t corrected_bases = 0;
};

/*
 * 双端的逐对处理链。顺序照抄上游 ``PairEndProcessor::processPairEnd``。
 *
 * 一个 read 对要同时用到裁接头、碱基校正、插入片段与合并，四件事共用同一次
 * overlap 分析（上游显式缓存了它）——这是双端链路上最贵的一步，算四遍不可接受。
 * 唯一的例外是合并：读到这里 read 已经被前面的修剪改过，必须重算。
 */
PairOutcome process_pair(const ReadPair& pair,
                         const WorkflowOptions& options,
                         bool want_unpaired,
                         bool want_failed,
                         bool want_overlapped) {
    PairOutcome outcome;
    FastqRecord working1 = pair.read1;
    FastqRecord working2 = pair.read2;

    if (options.normalize_enabled) {
        bool renamed = false;
        bool requantified = false;
        normalize_record(working1, options.normalize, renamed, requantified);
        outcome.normalized = renamed || requantified;
        renamed = false;
        requantified = false;
        normalize_record(working2, options.normalize, renamed, requantified);
        outcome.normalized = outcome.normalized || renamed || requantified;
    }

    if (options.umi_enabled) {
        const UmiOutcome umi = apply_umi_to_reads(working1, &working2, options.umi);
        working1 = umi.read1;
        working2 = umi.read2;
        outcome.umi_tagged = umi.tagged1 || umi.tagged2;
    }

    /* 首尾固定修剪 + 滑窗质量剪切。任一端被剪空，整对按"过短"丢弃
     * （上游此时 r1 或 r2 为空指针，后面所有步骤都被跳过）。 */
    const TrimOutcome trim1 =
        compute_trim(working1.sequence, working1.quality, options.quality_cut);
    const TrimOutcome trim2 =
        compute_trim(working2.sequence, working2.quality, options.quality_cut);
    if (trim1.dropped || trim2.dropped) {
        outcome.verdict1 = kFailLength;
        outcome.verdict2 = kFailLength;
        outcome.has_failed_first = want_failed;
        outcome.has_failed_second = want_failed;
        if (want_failed) {
            outcome.failed_first = tagged_with_verdict(pair.read1, kFailLength);
            outcome.failed_second = tagged_with_verdict(pair.read2, kFailLength);
        }
        return outcome;
    }
    outcome.trimmed = trim1.begin != 0 || trim1.length != working1.sequence.size() ||
                      trim2.begin != 0 || trim2.length != working2.sequence.size();
    apply_trim(working1, trim1);
    apply_trim(working2, trim2);
    const int32_t front_trimmed1 = trim1.front_trimmed;
    const int32_t front_trimmed2 = trim2.front_trimmed;

    /* polyG：双端**没有联合判定**——上游就是把两条 read 各自交给同一个
     * 单条函数，互不影响（``PolyX::trimPolyG(r1, r2, ...)`` 内部是两次单条调用）。 */
    if (options.poly_trim.enabled_poly_g) {
        const PolyTrimOutcome poly1 =
            trim_poly_g(working1.sequence, options.poly_trim.min_length_poly_g);
        const PolyTrimOutcome poly2 =
            trim_poly_g(working2.sequence, options.poly_trim.min_length_poly_g);
        if (poly1.trimmed_bases > 0 || poly2.trimmed_bases > 0) {
            apply_poly_trim(working1, poly1);
            apply_poly_trim(working2, poly2);
            outcome.poly_trimmed = true;
        }
    }

    /*
     * overlap 分析一次算好，供插入片段、碱基校正、裁接头共用；合并之前会重算
     * （那时 read 已被改过）。上游在 ``processPairEnd`` 里显式缓存了它——
     * 这是双端链路上最贵的一步，算三遍不可接受。
     */
    const OverlapOutcome overlap =
        analyze_overlap(working1.sequence, working2.sequence, options.overlap);

    {
        const int32_t size =
            insert_size_from(overlap, working1.length(), working2.length());
        outcome.insert_size =
            size < 0 ? size
                     : std::min(size + front_trimmed1 + front_trimmed2,
                                kWorkflowInsertSizeMax);
    }

    if (options.correction_enabled) {
        const PairedCorrectionOutcome corrected =
            correct_pair_bases(working1, working2, overlap);
        if (corrected.corrected > 0) {
            working1 = corrected.read1;
            working2 = corrected.read2;
            outcome.corrected_bases = corrected.corrected;
        }
    }

    if (options.adapter_enabled) {
        /*
         * 允许缺口时的重叠要**另算一次**：缺口路径的结论与无缺口的不同，
         * 而上游同样是单独算一份 ``ovForAdapter``。
         */
        OverlapOutcome adapter_overlap = overlap;
        if (options.adapter.allow_one_gap) {
            OverlapOptions gap_options = options.overlap;
            gap_options.allow_gap = true;
            adapter_overlap =
                analyze_overlap(working1.sequence, working2.sequence, gap_options);
        }
        PairedAdapterTrimOptions trim_options;
        trim_options.front_trimmed1 = front_trimmed1;
        trim_options.front_trimmed2 = front_trimmed2;
        const PairedAdapterTrimOutcome by_overlap =
            trim_adapter_pair(working1, working2, adapter_overlap, trim_options);

        bool trimmed1 = false;
        bool trimmed2 = false;
        if (by_overlap.trimmed) {
            working1 = by_overlap.read1;
            working2 = by_overlap.read2;
            trimmed1 = true;
            trimmed2 = true;
        } else {
            /*
             * 片段长于读长时两条 read 不重叠，按 overlap 找不到接头——这时要
             * 退化为**按给定接头序列**逐条匹配。上游在同一个 if 里做了这件事，
             * 不是可选项：长插入片段的文库全靠这一路。
             */
            if (!options.adapter.adapters.empty()) {
                trimmed1 = apply_adapter_trim(working1, options.adapter).trimmed;
            }
            if (!options.adapter_r2.adapters.empty()) {
                trimmed2 = apply_adapter_trim(working2, options.adapter_r2).trimmed;
            }
        }

        if (trimmed1 || trimmed2) {
            outcome.adapter_trimmed_reads = (trimmed1 ? 1 : 0) + (trimmed2 ? 1 : 0);

            /* 接头二聚体：有接头证据、且两端都短到只剩接头本身。 */
            if (options.adapter_dimer_enabled &&
                working1.length() <= static_cast<std::size_t>(options.adapter_dimer_max_len) &&
                working2.length() <= static_cast<std::size_t>(options.adapter_dimer_max_len)) {
                outcome.adapter_dimer = true;
            }
        }
    }

    /* 重叠区输出使用的仍是"刚裁完接头"的 read（上游在 polyX 之前写它），
     * 且要求重叠区一个错配都没有，所以要按错配比例 0 单独判一次。 */
    if (want_overlapped) {
        OverlapOptions strict = options.overlap;
        strict.diff_percent_limit = 0.0;
        const OverlapOutcome strict_overlap =
            analyze_overlap(working1.sequence, working2.sequence, strict);
        if (strict_overlap.overlapped) {
            const std::size_t begin = static_cast<std::size_t>(
                std::max(0, strict_overlap.offset));
            if (begin < working1.sequence.size()) {
                const std::size_t length = std::min(
                    static_cast<std::size_t>(strict_overlap.overlap_len),
                    working1.sequence.size() - begin);
                outcome.overlapped.name = working1.name;
                outcome.overlapped.sequence = working1.sequence.substr(begin, length);
                outcome.overlapped.quality = working1.quality.substr(begin, length);
                outcome.has_overlapped = true;
            }
        }
    }

    if (options.poly_trim.enabled_poly_x) {
        const PolyTrimOutcome poly1 =
            trim_poly_x(working1.sequence, options.poly_trim.min_length_poly_x);
        const PolyTrimOutcome poly2 =
            trim_poly_x(working2.sequence, options.poly_trim.min_length_poly_x);
        if (poly1.trimmed_bases > 0 || poly2.trimmed_bases > 0) {
            apply_poly_trim(working1, poly1);
            apply_poly_trim(working2, poly2);
            outcome.poly_trimmed = true;
        }
    }

    if (options.max_length1 > 0 &&
        working1.sequence.size() > static_cast<std::size_t>(options.max_length1)) {
        working1.sequence.resize(static_cast<std::size_t>(options.max_length1));
        working1.quality.resize(static_cast<std::size_t>(options.max_length1));
    }
    if (options.max_length2 > 0 &&
        working2.sequence.size() > static_cast<std::size_t>(options.max_length2)) {
        working2.sequence.resize(static_cast<std::size_t>(options.max_length2));
        working2.quality.resize(static_cast<std::size_t>(options.max_length2));
    }

    /* --- 合并模式 --- */
    bool merge_processed = false;
    if (options.merge_enabled) {
        /* read 已经被前面的步骤改过，重叠必须重算（上游为 issue #675 做的修正）。 */
        const OverlapOutcome merge_overlap =
            analyze_overlap(working1.sequence, working2.sequence, options.overlap);
        const PairedMergeOutcome merged =
            merge_read_pair(working1, working2, merge_overlap);
        if (merged.merged) {
            const int32_t verdict =
                filter_verdict(merged.record.sequence, merged.record.quality,
                               options.filter);
            outcome.verdict1 = verdict;
            outcome.merged = true;
            outcome.gap_overlap = merged.gap;
            if (verdict == kPassFilter) {
                outcome.merged_passed = true;
                outcome.merged_record = merged.record;
            }
            merge_processed = true;
        } else if (options.merge_include_unmerged) {
            /* 未合并又要求保留：两条各自过滤、各自写进合并输出。 */
            outcome.verdict1 = filter_verdict(working1.sequence, working1.quality,
                                              options.filter);
            outcome.verdict2 = filter_verdict(working2.sequence, working2.quality,
                                              options.filter);
            outcome.read1 = working1;
            outcome.read2 = working2;
            outcome.keep1 = outcome.verdict1 == kPassFilter;
            outcome.keep2 = outcome.verdict2 == kPassFilter;
            merge_processed = true;
        }
    }

    if (!merge_processed) {
        outcome.verdict1 = filter_verdict(working1.sequence, working1.quality,
                                          options.filter);
        outcome.verdict2 = filter_verdict(working2.sequence, working2.quality,
                                          options.filter);

        if (outcome.adapter_dimer) {
            outcome.verdict1 = kFailAdapterDimer;
            outcome.verdict2 = kFailAdapterDimer;
        }

        outcome.read1 = std::move(working1);
        outcome.read2 = std::move(working2);
        outcome.keep1 = outcome.verdict1 == kPassFilter;
        outcome.keep2 = outcome.verdict2 == kPassFilter;

        /* 落单 read 的去向与失败输出：照抄上游的三分支。
         * 注意上游**没有**"两端都失败"的分支——那种 read 对不进任何输出。 */
        if (outcome.keep1 && !outcome.keep2) {
            if (want_unpaired) {
                outcome.unpaired = outcome.read1;
                outcome.has_unpaired = true;
                outcome.has_failed_second = want_failed;
                if (want_failed) {
                    outcome.failed_second = tagged_with_verdict(pair.read2, outcome.verdict2);
                }
            } else if (want_failed) {
                outcome.has_failed_first = true;
                outcome.failed_first =
                    tagged_with_text(pair.read1, "paired_read_is_failing");
                outcome.has_failed_second = true;
                outcome.failed_second = tagged_with_verdict(pair.read2, outcome.verdict2);
            }
        } else if (!outcome.keep1 && outcome.keep2) {
            if (want_unpaired) {
                outcome.unpaired = outcome.read2;
                outcome.has_unpaired = true;
                outcome.has_failed_first = want_failed;
                if (want_failed) {
                    outcome.failed_first = tagged_with_verdict(pair.read1, outcome.verdict1);
                }
            } else if (want_failed) {
                outcome.has_failed_first = true;
                outcome.failed_first = tagged_with_verdict(pair.read1, outcome.verdict1);
                outcome.has_failed_second = true;
                outcome.failed_second =
                    tagged_with_text(pair.read2, "paired_read_is_failing");
            }
        }
    }

    return outcome;
}

void run_paired_end(const WorkflowOptions& options, WorkflowResult& result) {
    const bool split_enabled = options.split_records > 0;
    const bool want_unpaired = !options.unpaired_path.empty() && !split_enabled;
    const bool want_failed = !options.failed_path.empty() && !split_enabled;
    const bool want_merged = options.merge_enabled && !split_enabled;
    const bool want_overlapped = !options.overlapped_path.empty() && !split_enabled;

    std::unique_ptr<DuplicateDetector> detector;
    if (options.dedup_evaluate || options.dedup_enabled) {
        DedupOptions dedup_options;
        dedup_options.accuracy_level = resolve_dedup_level(options);
        dedup_options.buffer_bytes = options.dedup_buffer_bytes;
        dedup_options.dedup = options.dedup_enabled;
        detector = std::make_unique<DuplicateDetector>(dedup_options);
    }

    ProducerTally producer;
    WriterTally writer;

    FastqReader reader1(options.read1_path);
    FastqReader reader2(options.read2_path);
    auto read_one = [&](ReadPair& pair) {
        const bool has_first = reader1.next(pair.read1);
        const bool has_second = reader2.next(pair.read2);
        if (has_first != has_second) {
            throw FastqFormatError("两份配对 FASTQ 的记录数不一致。");
        }
        return has_first;
    };

    auto prepare = [&](const ReadPair& pair) -> bool {
        if (options.stats_enabled) {
            if (options.stats.pre1 != nullptr) {
                options.stats.pre1->add(pair.read1.sequence, pair.read1.quality);
            }
            if (options.stats.pre2 != nullptr) {
                options.stats.pre2->add(pair.read2.sequence, pair.read2.quality);
            }
        }
        producer.total_reads += 2;
        producer.total_bases += static_cast<int64_t>(pair.read1.length()) +
                                static_cast<int64_t>(pair.read2.length());

        if (options.index_filter_enabled &&
            is_index_filtered(pair.read1.name, &pair.read2.name,
                              options.index_filter)) {
            producer.index_filtered_reads += 2;
            return false;
        }
        if (detector != nullptr &&
            detector->check_pair(pair.read1.sequence, pair.read2.sequence)) {
            ++producer.duplicate_reads;
            if (options.dedup_enabled) {
                return false;
            }
        }
        return true;
    };

    auto step = [&](const ReadPair& pair) -> PairOutcome {
        return process_pair(pair, options, want_unpaired, want_failed, want_overlapped);
    };

    std::unique_ptr<SplitWriter> writer1;
    std::unique_ptr<SplitWriter> writer2;
    std::unique_ptr<FastqWriter> unpaired_writer;
    std::unique_ptr<FastqWriter> failed_writer;
    std::unique_ptr<FastqWriter> merged_writer;
    std::unique_ptr<FastqWriter> overlapped_writer;

    auto consume = [&](const PairOutcome& outcome) {
        ++writer.pairs;
        if (outcome.normalized) ++writer.normalized_reads;
        if (outcome.umi_tagged) ++writer.umi_tagged_reads;
        if (outcome.trimmed) ++writer.trimmed_reads;
        if (outcome.poly_trimmed) ++writer.poly_trimmed_reads;
        if (outcome.adapter_trimmed_reads > 0) {
            writer.adapter_trimmed_reads += outcome.adapter_trimmed_reads;
        }
        if (outcome.adapter_dimer) ++writer.adapter_dimer_pairs;
        if (outcome.corrected_bases > 0) {
            ++writer.corrected_pairs;
            writer.corrected_bases += outcome.corrected_bases;
        }
        if (outcome.gap_overlap) ++writer.gap_overlap_pairs;
        if (outcome.insert_size < 0) {
            ++writer.insert_size_histogram[static_cast<std::size_t>(kWorkflowInsertSizeMax)];
        } else {
            note_insert_size(writer.insert_size_histogram, outcome.insert_size);
        }

        if (outcome.has_overlapped && overlapped_writer != nullptr) {
            overlapped_writer->write(outcome.overlapped);
        }

        if (options.merge_enabled) {
            if (outcome.merged) {
                ++writer.pairs_merged;
                if (outcome.merged_passed) {
                    if (merged_writer != nullptr) {
                        merged_writer->write(outcome.merged_record);
                    }
                    ++writer.output_reads;
                    writer.output_bases +=
                        static_cast<int64_t>(outcome.merged_record.length());
                    if (options.stats_enabled && options.stats.post1 != nullptr) {
                        options.stats.post1->add(outcome.merged_record.sequence,
                                                 outcome.merged_record.quality);
                    }
                } else {
                    ++writer.filtered_reads;
                    note_filter_failure(writer.filter_breakdown, outcome.verdict1);
                }
                return;
            }
            if (!options.merge_include_unmerged) {
                ++writer.filtered_reads;
                return;
            }
            /* 未合并又要求保留：两条各自判定、各自写进合并输出。 */
            if (outcome.keep1) {
                if (merged_writer != nullptr) {
                    merged_writer->write(outcome.read1);
                }
                ++writer.output_reads;
                writer.output_bases += static_cast<int64_t>(outcome.read1.length());
                if (options.stats_enabled && options.stats.post1 != nullptr) {
                    options.stats.post1->add(outcome.read1.sequence,
                                             outcome.read1.quality);
                }
            } else {
                ++writer.filtered_reads;
                note_filter_failure(writer.filter_breakdown, outcome.verdict1);
            }
            if (outcome.keep2) {
                if (merged_writer != nullptr) {
                    merged_writer->write(outcome.read2);
                }
                ++writer.output_reads;
                writer.output_bases += static_cast<int64_t>(outcome.read2.length());
                if (options.stats_enabled && options.stats.post1 != nullptr) {
                    options.stats.post1->add(outcome.read2.sequence,
                                             outcome.read2.quality);
                }
            } else {
                ++writer.filtered_reads;
                note_filter_failure(writer.filter_breakdown, outcome.verdict2);
            }
            return;
        }

        /* 非合并模式：照抄上游的三分支，注意"两端都失败"不进任何输出。 */
        if (outcome.keep1 && outcome.keep2) {
            writer1->write(outcome.read1);
            writer2->write(outcome.read2);
            writer.output_reads += 2;
            writer.output_bases += static_cast<int64_t>(outcome.read1.length()) +
                                   static_cast<int64_t>(outcome.read2.length());
            if (options.stats_enabled) {
                if (options.stats.post1 != nullptr) {
                    options.stats.post1->add(outcome.read1.sequence,
                                             outcome.read1.quality);
                }
                if (options.stats.post2 != nullptr) {
                    options.stats.post2->add(outcome.read2.sequence,
                                             outcome.read2.quality);
                }
            }
            return;
        }
        if (outcome.keep1 || outcome.keep2) {
            if (outcome.has_unpaired && unpaired_writer != nullptr) {
                unpaired_writer->write(outcome.unpaired);
                ++writer.unpaired_reads;
                ++writer.output_reads;
                writer.output_bases +=
                    static_cast<int64_t>(outcome.unpaired.length());
            }
            /* 落单的那条不算进"过滤后统计"——上游同样如此。 */
        }
        if (outcome.keep1) {
            ++writer.filtered_reads;
            note_filter_failure(writer.filter_breakdown, outcome.verdict2);
        } else if (outcome.keep2) {
            ++writer.filtered_reads;
            note_filter_failure(writer.filter_breakdown, outcome.verdict1);
        } else {
            ++writer.filtered_reads;
            ++writer.filtered_reads;
            note_filter_failure(writer.filter_breakdown, outcome.verdict1);
            note_filter_failure(writer.filter_breakdown, outcome.verdict2);
        }
        if (failed_writer != nullptr) {
            if (outcome.has_failed_first) {
                failed_writer->write(outcome.failed_first);
            }
            if (outcome.has_failed_second) {
                failed_writer->write(outcome.failed_second);
            }
        }
    };

    try {
        writer1 = std::make_unique<SplitWriter>(options.output1_path, options.compress,
                                                options.split_records,
                                                options.split_digits);
        writer2 = std::make_unique<SplitWriter>(options.output2_path, options.compress,
                                                options.split_records,
                                                options.split_digits);
        if (want_unpaired) {
            unpaired_writer =
                std::make_unique<FastqWriter>(options.unpaired_path, options.compress);
        }
        if (want_failed) {
            failed_writer =
                std::make_unique<FastqWriter>(options.failed_path, options.compress);
        }
        if (want_merged) {
            merged_writer =
                std::make_unique<FastqWriter>(options.merged_path, options.compress);
        }
        if (want_overlapped) {
            overlapped_writer =
                std::make_unique<FastqWriter>(options.overlapped_path, options.compress);
        }
        run_workflow_pipeline<ReadPair>(read_one, options.threads,
                                       AutoThreads::Hardware, options.max_reads,
                                       prepare, step, consume);
        writer1->close();
        writer2->close();
        if (unpaired_writer != nullptr) {
            unpaired_writer->close();
        }
        if (failed_writer != nullptr) {
            failed_writer->close();
        }
        if (merged_writer != nullptr) {
            merged_writer->close();
        }
        if (overlapped_writer != nullptr) {
            overlapped_writer->close();
        }
    } catch (...) {
        for (const std::string& path : writer1->created_paths()) {
            remove_file_if_exists(path);
        }
        for (const std::string& path : writer2->created_paths()) {
            remove_file_if_exists(path);
        }
        if (unpaired_writer != nullptr) {
            remove_file_if_exists(options.unpaired_path);
        }
        if (failed_writer != nullptr) {
            remove_file_if_exists(options.failed_path);
        }
        if (merged_writer != nullptr) {
            remove_file_if_exists(options.merged_path);
        }
        if (overlapped_writer != nullptr) {
            remove_file_if_exists(options.overlapped_path);
        }
        throw;
    }

    result.total_reads = producer.total_reads;
    result.total_bases = producer.total_bases;
    result.index_filtered_reads = producer.index_filtered_reads;
    result.duplicate_reads = producer.duplicate_reads;
    result.output_reads = writer.output_reads;
    result.output_bases = writer.output_bases;
    result.unpaired_reads = writer.unpaired_reads;
    result.pairs_total = writer.pairs;
    result.normalized_reads = writer.normalized_reads;
    result.umi_tagged_reads = writer.umi_tagged_reads;
    result.trimmed_reads = writer.trimmed_reads;
    result.poly_trimmed_reads = writer.poly_trimmed_reads;
    result.adapter_trimmed_reads = writer.adapter_trimmed_reads;
    result.adapter_dimer_pairs = writer.adapter_dimer_pairs;
    result.corrected_pairs = writer.corrected_pairs;
    result.corrected_bases = writer.corrected_bases;
    result.filtered_reads = writer.filtered_reads;
    result.filter_breakdown = writer.filter_breakdown;
    result.pairs_merged = writer.pairs_merged;
    result.gap_overlap_pairs = writer.gap_overlap_pairs;
    result.insert_size_histogram = writer.insert_size_histogram;
    result.insert_size_peak = peak_of(writer.insert_size_histogram);
    result.insert_size_unknown =
        writer.insert_size_histogram[static_cast<std::size_t>(kWorkflowInsertSizeMax)];
    result.split_file_count =
        std::max(writer1->file_count(), writer2->file_count());
    result.dedup_accuracy_level = detector != nullptr ? detector->accuracy_level() : 0;
}

}  // namespace

WorkflowResult run_workflow(const WorkflowOptions& options) {
    const bool paired = !options.read2_path.empty();

    if (options.read1_path.empty()) {
        throw std::invalid_argument("工作流需要 read1 输入路径。");
    }
    if (options.output1_path.empty()) {
        throw std::invalid_argument("工作流需要 read1 输出路径。");
    }
    if (paired && options.output2_path.empty()) {
        throw std::invalid_argument("双端输入时 read2 输出路径不能为空。");
    }
    if (!paired && !options.output2_path.empty()) {
        throw std::invalid_argument("单端输入不应该给 read2 输出路径。");
    }
    if (options.merge_enabled && !paired) {
        throw std::invalid_argument("合并模式只适用于双端输入。");
    }
    if (options.merge_enabled && options.merged_path.empty()) {
        throw std::invalid_argument("合并模式需要给合并输出路径。");
    }
    if (!options.merge_enabled && !options.merged_path.empty()) {
        throw std::invalid_argument("没有开启合并模式，不应该给合并输出路径。");
    }
    if (!paired && (!options.unpaired_path.empty() ||
                    !options.overlapped_path.empty())) {
        throw std::invalid_argument("落单与重叠区输出只适用于双端输入。");
    }
    if (!paired && options.correction_enabled) {
        throw std::invalid_argument("碱基校正只适用于双端输入。");
    }
    /*
     * 分卷模式下辅助输出无处安放（上游也是直接不创建那几个写入器，并且明确
     * 警告 unpaired 不受支持）。本层选择**报错而不是静默丢弃**——用户明确
     * 要求过的东西不该无声消失。
     */
    if (options.split_records > 0 &&
        (!options.unpaired_path.empty() || !options.failed_path.empty() ||
         !options.merged_path.empty() || !options.overlapped_path.empty())) {
        throw std::invalid_argument(
            "分卷模式只写主输出，不能同时要求落单 / 失败 / 合并 / 重叠区输出。");
    }
    if (options.max_reads < 0) {
        throw std::invalid_argument("处理条数上限不能为负数。");
    }

    WorkflowResult result;
    /* 直方图先备成定长全零：单端没有插入片段这回事，但调用方拿到的形状应当一致
     * （桶数固定），不该按模式给两种长度。 */
    result.insert_size_histogram.assign(kWorkflowInsertSizeMax + 1, 0);
    if (paired) {
        run_paired_end(options, result);
    } else {
        run_single_end(options, result);
    }

    /* 四个统计器都汇总好再交出去——调用方拿到句柄就能直接查曲线。 */
    const auto finalize = [](ReadStats* stats) {
        if (stats != nullptr) {
            stats->summarize();
        }
    };
    finalize(options.stats.pre1);
    finalize(options.stats.pre2);
    finalize(options.stats.post1);
    finalize(options.stats.post2);

    return result;
}

}  // namespace bio
