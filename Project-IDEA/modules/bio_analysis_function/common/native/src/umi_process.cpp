#include "umi_process.h"

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

#include "fastq.h"
#include "paired_pipeline.h"
#include "pipeline.h"
#include "read_names.h"

namespace bio {
namespace {

/* ``first_index`` / ``last_index`` 已上移到 ``read_names.h``（两个使用方：
 * 本模块与 index_filter）。下面留下的都是 UMI 专用的东西。 */

/*
 * 从头部剪掉 ``count`` 个碱基（上游 ``Read::trimFront``）。
 *
 * **它最多留 1 个碱基**：上游写作 ``min(length - 1, len)``。长度为 0 时上游算出 -1、
 * 在 C++ 里等于清空一个本就空的串，这里等价地什么都不做。
 */
void trim_front(std::string& sequence, std::string& quality, int count) {
    const int effective =
        std::min(static_cast<int>(sequence.size()) - 1, count);
    if (effective <= 0) {
        return;
    }
    const std::size_t amount = static_cast<std::size_t>(effective);
    sequence.erase(0, amount);
    quality.erase(0, amount);
}

/*
 * 把 UMI 标签挂到 read 名上（上游 ``UmiProcessor::addUmiToName``）。
 *
 * 标签插在名字里**第一个空格之前**——空格之后是测序仪的注释段（含 index）。
 */
std::string add_umi_to_name(const std::string& name,
                            const std::string& umi,
                            const UmiOptions& options) {
    const std::string tag = options.prefix.empty()
                                ? options.delimiter + umi
                                : options.delimiter + options.prefix + "_" + umi;
    const std::size_t space = name.find(' ');
    if (space == std::string::npos) {
        return name + tag;
    }
    return name.substr(0, space) + tag + name.substr(space);
}

}  // namespace

UmiOutcome apply_umi_to_reads(const FastqRecord& read1,
                              const FastqRecord* read2,
                              const UmiOptions& options) {
    UmiOutcome outcome;
    outcome.has_read2 = read2 != nullptr;
    outcome.input_bases = static_cast<int64_t>(read1.length());
    if (read2 != nullptr) {
        outcome.input_bases += static_cast<int64_t>(read2->length());
    }

    std::string name1 = read1.name;
    std::string sequence1 = read1.sequence;
    std::string quality1 = read1.quality;
    std::string name2;
    std::string sequence2;
    std::string quality2;
    if (read2 != nullptr) {
        name2 = read2->name;
        sequence2 = read2->sequence;
        quality2 = read2->quality;
    }

    std::string umi;
    std::string merged;
    bool uses_merged = false;

    switch (options.location) {
        case kUmiLocationIndex1:
            umi = first_index(name1);
            break;
        case kUmiLocationIndex2:
            if (read2 != nullptr) {
                umi = last_index(name2);
            }
            break;
        case kUmiLocationRead1: {
            const int take = std::min(static_cast<int>(sequence1.size()),
                                      options.length);
            umi = sequence1.substr(0, static_cast<std::size_t>(take));
            trim_front(sequence1, quality1, take + options.skip);
            break;
        }
        case kUmiLocationRead2:
            if (read2 != nullptr) {
                const int take = std::min(static_cast<int>(sequence2.size()),
                                          options.length);
                umi = sequence2.substr(0, static_cast<std::size_t>(take));
                trim_front(sequence2, quality2, take + options.skip);
            }
            break;
        case kUmiLocationPerIndex:
            uses_merged = true;
            merged = first_index(name1);
            if (read2 != nullptr) {
                merged = merged + "_" + last_index(name2);
            }
            break;
        case kUmiLocationPerRead: {
            uses_merged = true;
            const int take1 = std::min(static_cast<int>(sequence1.size()),
                                       options.length);
            merged = sequence1.substr(0, static_cast<std::size_t>(take1));
            trim_front(sequence1, quality1, take1 + options.skip);
            if (read2 != nullptr) {
                const int take2 = std::min(static_cast<int>(sequence2.size()),
                                           options.length);
                merged = merged + "_" +
                         sequence2.substr(0, static_cast<std::size_t>(take2));
                trim_front(sequence2, quality2, take2 + options.skip);
            }
            break;
        }
        default:
            break;
    }

    /*
     * ``per_index`` / ``per_read`` 无条件挂标签（上游就是这么写的，即使拼出来是空串
     * 也会留下一个分隔符）；其余四种只在真的取到 UMI 时才挂。
     */
    if (uses_merged) {
        name1 = add_umi_to_name(name1, merged, options);
        outcome.tagged1 = name1 != read1.name;
        if (read2 != nullptr) {
            name2 = add_umi_to_name(name2, merged, options);
            outcome.tagged2 = name2 != read2->name;
        }
    } else if (!umi.empty()) {
        name1 = add_umi_to_name(name1, umi, options);
        outcome.tagged1 = name1 != read1.name;
        if (read2 != nullptr) {
            name2 = add_umi_to_name(name2, umi, options);
            outcome.tagged2 = name2 != read2->name;
        }
    }

    outcome.read1 =
        FastqRecord{std::move(name1), std::move(sequence1), std::move(quality1)};
    if (read2 != nullptr) {
        outcome.read2 =
            FastqRecord{std::move(name2), std::move(sequence2), std::move(quality2)};
    }
    return outcome;
}

UmiStats process_umi_single(const std::string& input_path,
                            const std::string& output_path,
                            bool compress,
                            const UmiOptions& options) {
    /*
     * 单端走通用的单端流水线，它的统计是固定的 `bio_stats_t`（不含 UMI 口径），
     * 所以这里的计数自己用原子量累加——workers 是并发调用 step 的。
     */
    std::atomic<int64_t> total{0};
    std::atomic<int64_t> tagged{0};
    std::atomic<int64_t> trimmed{0};
    std::atomic<int64_t> input_bases{0};
    std::atomic<int64_t> output_bases{0};

    run_pipeline(input_path, output_path, compress, options.threads,
                 AutoThreads::Hardware,
                 [&](FastqRecord& record) -> StepOutcome {
                     const UmiOutcome outcome =
                         apply_umi_to_reads(record, nullptr, options);
                     const int64_t produced =
                         static_cast<int64_t>(outcome.read1.length());
                     total.fetch_add(1, std::memory_order_relaxed);
                     input_bases.fetch_add(outcome.input_bases,
                                           std::memory_order_relaxed);
                     output_bases.fetch_add(produced, std::memory_order_relaxed);
                     trimmed.fetch_add(outcome.input_bases - produced,
                                       std::memory_order_relaxed);
                     if (outcome.tagged1) {
                         tagged.fetch_add(1, std::memory_order_relaxed);
                     }
                     record = std::move(outcome.read1);
                     return StepOutcome{false, true};
                 });

    UmiStats stats;
    stats.total_reads = total.load();
    stats.reads_with_umi = tagged.load();
    stats.trimmed_bases = trimmed.load();
    stats.input_bases = input_bases.load();
    stats.output_bases = output_bases.load();
    return stats;
}

UmiStats process_umi_paired(const std::string& read1_path,
                            const std::string& read2_path,
                            const std::string& output1_path,
                            const std::string& output2_path,
                            bool compress,
                            const UmiOptions& options) {
    UmiStats stats;
    /* 写出方（单线程）顺序累加统计，因此这里不需要原子量。 */
    try {
        FastqWriter writer1(output1_path, compress);
        FastqWriter writer2(output2_path, compress);
        run_paired_pipeline(
            read1_path, read2_path, options.threads, AutoThreads::Hardware,
            [&](const FastqRecord& read1, const FastqRecord& read2) -> UmiOutcome {
                return apply_umi_to_reads(read1, &read2, options);
            },
            [&](const UmiOutcome& outcome) {
                const int64_t produced =
                    static_cast<int64_t>(outcome.read1.length() +
                                         outcome.read2.length());
                stats.total_reads += 2; /* 按 read 条数计，一对算两条 */
                stats.input_bases += outcome.input_bases;
                stats.output_bases += produced;
                stats.trimmed_bases += outcome.input_bases - produced;
                if (outcome.tagged1) {
                    ++stats.reads_with_umi;
                }
                if (outcome.tagged2) {
                    ++stats.reads_with_umi;
                }
                writer1.write(outcome.read1);
                writer2.write(outcome.read2);
            });
        writer1.close();
        writer2.close();
    } catch (...) {
        remove_file_if_exists(output1_path);
        remove_file_if_exists(output2_path);
        throw;
    }
    return stats;
}

}  // namespace bio
