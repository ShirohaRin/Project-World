/*
 * api.cpp —— C ABI 边界的实现
 *
 * 这一层只做四件事：
 *   1. 参数校验（越界、自相矛盾）；
 *   2. 异常翻译（C++ 异常 → bio_status_t + 消息）；
 *   3. 把参数翻译成算法层的结构体，交给流水线执行；
 *   4. 统计与失败清理。
 *
 * **算法逻辑不在这里**。各算法的判定都落在 quality_trim / poly_trim /
 * read_filter 里，**流水线也不在这里**（线程编排落在 pipeline.h）——本文件只把
 * 两者接起来，算法若各写一份循环，统计口径迟早会漂移。
 */

#include "bio_native.h"

#include <algorithm>
#include <cstring>
#include <exception>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "adapter_detect.h"
#include "adapter_trim.h"
#include "dedup.h"
#include "fastq.h"
#include "index_filter.h"
#include "insert_size.h"
#include "normalize.h"
#include "overlap.h"
#include "overrep.h"
#include "paired_adapter_trim.h"
#include "paired_base_correction.h"
#include "paired_merge.h"
#include "pipeline.h"
#include "poly_trim.h"
#include "quality_trim.h"
#include "read_filter.h"
#include "read_stats.h"
#include "two_color.h"
#include "umi_process.h"
#include "workflow.h"

namespace {

constexpr const char* kNativeVersion = "0.17.0";

/* 把字符串写进固定长度的缓冲区，保证结尾有 NUL。 */
void copy_to_buffer(char* destination, std::size_t capacity, const std::string& text) {
    std::strncpy(destination, text.c_str(), capacity - 1);
    destination[capacity - 1] = '\0';
}

/*
 * 记录失败并把状态码返回给调用方，便于用 return set_failure(...) 一行完成。
 *
 * 做成模板是因为现在有几种结果结构体（bio_result_t、bio_filter_result_t、
 * bio_adapter_detect_result_t），它们的前两个字段同名同型；把状态与消息的写法
 * 收在一处，避免多套措辞各自漂移。
 */
template <typename Result>
int32_t set_failure(Result* result, int32_t status, const std::string& message) {
    result->status = status;
    copy_to_buffer(result->message, BIO_MESSAGE_CAPACITY, message);
    return status;
}

/* 三态压缩策略：显式指定优先，否则跟随输入（不看扩展名）。 */
bool resolve_compress(int32_t mode, const std::string& input_path) {
    if (mode == BIO_COMPRESS_ON) {
        return true;
    }
    if (mode == BIO_COMPRESS_OFF) {
        return false;
    }
    return bio::is_gzip_file(input_path);
}

/* C ABI 用 NULL 表示"没给这个路径"，内部统一按空串判断。 */
std::string optional_path(const char* path) {
    return path == nullptr ? std::string() : std::string(path);
}

/* 把当前正在传播的异常翻译成状态码。必须在 catch 块内调用。 */
template <typename Result>
int32_t translate_exception(Result* result) {
    try {
        throw;
    } catch (const bio::InputFileError& error) {
        return set_failure(result, BIO_ERR_INPUT_NOT_FOUND, error.what());
    } catch (const bio::FastqFormatError& error) {
        return set_failure(result, BIO_ERR_INPUT_FORMAT, error.what());
    } catch (const bio::OutputFileError& error) {
        return set_failure(result, BIO_ERR_OUTPUT, error.what());
    } catch (const std::invalid_argument& error) {
        return set_failure(result, BIO_ERR_ARGUMENT, error.what());
    } catch (const std::exception& error) {
        return set_failure(result, BIO_ERR_INTERNAL, error.what());
    } catch (...) {
        return set_failure(result, BIO_ERR_INTERNAL, "未知的内部错误。");
    }
}

/* 每次调用开始时重置结果结构体，并做最基础的空指针检查。 */
template <typename Result>
bool prepare_result(const char* input_path, const char* output_path, Result* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    std::memset(&result->stats, 0, sizeof(result->stats));

    if (input_path == nullptr || output_path == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "输入与输出路径不能为空。");
        return false;
    }
    return true;
}

/* 过滤结果多一块分类明细，重置时一并清零。 */
bool prepare_filter_result(const char* input_path,
                           const char* output_path,
                           bio_filter_result_t* result) {
    if (!prepare_result(input_path, output_path, result)) {
        return false;
    }
    std::memset(&result->breakdown, 0, sizeof(result->breakdown));
    return true;
}

/* 去重结果多两个标量；而且**输出可以留空**（只评估），因此不能套
 * prepare_result——它会因为 output_path 为空直接判失败。 */
bool prepare_dedup_result(const char* read1_path, bio_dedup_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    std::memset(&result->stats, 0, sizeof(result->stats));
    result->duplicate_reads = 0;
    result->accuracy_level = 0;

    if (read1_path == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "输入与输出路径不能为空。");
        return false;
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* 滑窗质量剪切                                                               */
/* ------------------------------------------------------------------------- */

bio::QualityCutOptions convert_quality_options(const bio_quality_trim_options_t* raw) {
    bio::QualityCutOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：不做质量剪切，也不做固定修剪
    }
    options.enabled_front = raw->enabled_front != 0;
    options.enabled_right = raw->enabled_right != 0;
    options.enabled_tail = raw->enabled_tail != 0;
    options.window_size_front = raw->window_size_front;
    options.window_size_right = raw->window_size_right;
    options.window_size_tail = raw->window_size_tail;
    options.quality_front = raw->quality_front;
    options.quality_right = raw->quality_right;
    options.quality_tail = raw->quality_tail;
    options.trim_front = raw->trim_front;
    options.trim_tail = raw->trim_tail;
    return options;
}

void validate_quality_options(const bio::QualityCutOptions& options) {
    const auto check_window = [](int32_t value, const char* name) {
        if (value < 1) {
            throw std::invalid_argument(std::string(name) + " 必须不小于 1，当前为 " +
                                        std::to_string(value) + "。");
        }
    };
    check_window(options.window_size_front, "window_size_front");
    check_window(options.window_size_right, "window_size_right");
    check_window(options.window_size_tail, "window_size_tail");

    // Phred+33 的字符上限是 '~'(126)，因此 Q 的上限是 93。
    const auto check_quality = [](int32_t value, const char* name) {
        if (value < 0 || value > 93) {
            throw std::invalid_argument(std::string(name) +
                                        " 必须在 0 到 93 之间，当前为 " +
                                        std::to_string(value) + "。");
        }
    };
    check_quality(options.quality_front, "quality_front");
    check_quality(options.quality_right, "quality_right");
    check_quality(options.quality_tail, "quality_tail");

    if (options.trim_front < 0 || options.trim_tail < 0) {
        throw std::invalid_argument("trim_front 与 trim_tail 不能为负数。");
    }
}

/* ------------------------------------------------------------------------- */
/* polyG / polyX 修剪                                                         */
/* ------------------------------------------------------------------------- */

bio::PolyTrimOptions convert_poly_options(const bio_poly_trim_options_t* raw) {
    bio::PolyTrimOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：两步都不做
    }
    options.enabled_poly_g = raw->enabled_poly_g != 0;
    options.enabled_poly_x = raw->enabled_poly_x != 0;
    options.min_length_poly_g = raw->min_length_poly_g;
    options.min_length_poly_x = raw->min_length_poly_x;
    return options;
}

void validate_poly_options(const bio::PolyTrimOptions& options) {
    if (options.min_length_poly_g < 1) {
        throw std::invalid_argument("min_length_poly_g 必须不小于 1，当前为 " +
                                    std::to_string(options.min_length_poly_g) + "。");
    }
    if (options.min_length_poly_x < 1) {
        throw std::invalid_argument("min_length_poly_x 必须不小于 1，当前为 " +
                                    std::to_string(options.min_length_poly_x) + "。");
    }
}

/* ------------------------------------------------------------------------- */
/* reads 过滤                                                                 */
/* ------------------------------------------------------------------------- */

bio::ReadFilterOptions convert_filter_options(const bio_read_filter_options_t* raw) {
    bio::ReadFilterOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：与 fastp 命令行默认一致
    }
    options.enabled_quality = raw->enabled_quality != 0;
    options.qualified_quality_phred = raw->qualified_quality_phred;
    options.unqualified_limit_bp = raw->unqualified_limit_bp;
    options.n_base_limit = raw->n_base_limit;
    options.average_qual = raw->average_qual;
    options.enabled_length = raw->enabled_length != 0;
    options.required_length = raw->required_length;
    options.max_length = raw->max_length;
    options.enabled_complexity = raw->enabled_complexity != 0;
    options.complexity_threshold_bp = raw->complexity_threshold_bp;
    return options;
}

void validate_filter_options(const bio::ReadFilterOptions& options) {
    // 校验口径与 Python 侧 ReadFilterConfig.__post_init__ 一致：
    // 越界直接报错，而不是像上游命令行那样把值夹紧。
    if (options.qualified_quality_phred < 0 || options.qualified_quality_phred > 93) {
        throw std::invalid_argument("qualified_quality_phred 必须在 0 到 93 之间，当前为 " +
                                    std::to_string(options.qualified_quality_phred) + "。");
    }
    if (options.unqualified_limit_bp < 0 || options.unqualified_limit_bp > 10000) {
        throw std::invalid_argument("unqualified_limit_bp 必须在 0 到 10000 之间（万分之一），"
                                    "当前为 " +
                                    std::to_string(options.unqualified_limit_bp) + "。");
    }
    if (options.n_base_limit < 0) {
        throw std::invalid_argument("n_base_limit 不能为负数，当前为 " +
                                    std::to_string(options.n_base_limit) + "。");
    }
    if (options.average_qual < 0) {
        throw std::invalid_argument("average_qual 不能为负数，当前为 " +
                                    std::to_string(options.average_qual) + "。");
    }
    if (options.required_length < 0) {
        throw std::invalid_argument("required_length 不能为负数，当前为 " +
                                    std::to_string(options.required_length) + "。");
    }
    if (options.max_length < 0) {
        throw std::invalid_argument("max_length 不能为负数，当前为 " +
                                    std::to_string(options.max_length) + "。");
    }
    if (options.complexity_threshold_bp < 0 || options.complexity_threshold_bp > 10000) {
        throw std::invalid_argument("complexity_threshold_bp 必须在 0 到 10000 之间（万分之一），"
                                    "当前为 " +
                                    std::to_string(options.complexity_threshold_bp) + "。");
    }
}

/* ------------------------------------------------------------------------- */
/* 接头检测                                                                   */
/* ------------------------------------------------------------------------- */

bio::AdapterDetectOptions convert_detect_options(const bio_adapter_detect_options_t* raw) {
    bio::AdapterDetectOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：与 fastp 一致
    }
    options.max_reads = raw->max_reads;
    options.max_bases = raw->max_bases;
    options.min_reads = raw->min_reads;
    options.key_length = raw->key_length;
    options.shift_tail = raw->shift_tail;
    options.max_adapter_length = raw->max_adapter_length;
    return options;
}

void validate_detect_options(const bio::AdapterDetectOptions& options) {
    if (options.max_reads < 1) {
        throw std::invalid_argument("max_reads 必须不小于 1，当前为 " +
                                    std::to_string(options.max_reads) + "。");
    }
    if (options.max_bases < 1) {
        throw std::invalid_argument("max_bases 必须不小于 1，当前为 " +
                                    std::to_string(options.max_bases) + "。");
    }
    if (options.min_reads < 1) {
        throw std::invalid_argument("min_reads 必须不小于 1，当前为 " +
                                    std::to_string(options.min_reads) + "。");
    }
    if (options.key_length < 4 || options.key_length > 12) {
        throw std::invalid_argument("key_length 必须在 4 到 12 之间，当前为 " +
                                    std::to_string(options.key_length) + "。");
    }
    if (options.shift_tail < 0) {
        throw std::invalid_argument("shift_tail 不能为负数，当前为 " +
                                    std::to_string(options.shift_tail) + "。");
    }
    if (options.max_adapter_length < 1) {
        throw std::invalid_argument("max_adapter_length 必须不小于 1，当前为 " +
                                    std::to_string(options.max_adapter_length) + "。");
    }
    if (options.max_adapter_length > BIO_ADAPTER_SEQUENCE_CAPACITY - 1) {
        throw std::invalid_argument(
            "max_adapter_length 超过返回缓冲区上限（" +
            std::to_string(BIO_ADAPTER_SEQUENCE_CAPACITY - 1) + "），当前为 " +
            std::to_string(options.max_adapter_length) + "。");
    }
}

/*
 * 检测结果结构体没有统计字段（它不产出文件），因此单独重置。
 * 与其它算法不同，这里只需要一个输入路径。
 */
bool prepare_detect_result(const char* input_path, bio_adapter_detect_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    result->adapter[0] = '\0';
    result->seed_sequence[0] = '\0';
    result->reason[0] = '\0';
    result->source = BIO_ADAPTER_SOURCE_NONE;
    result->sampled_reads = 0;
    result->sampled_bases = 0;
    result->seed_count = 0;
    result->seed_fold_milli = 0;

    if (input_path == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "输入路径不能为空。");
        return false;
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* 接头裁剪                                                                   */
/* ------------------------------------------------------------------------- */

bio::AdapterTrimOptions convert_adapter_trim_options(const bio_adapter_trim_options_t* raw) {
    bio::AdapterTrimOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：match_required=4、允许 1 个插入/缺失
    }
    // 照抄传进来的值，越界交给 validate 报错——与 Python 侧 AdapterTrimConfig
    // 的口径一致（它也对 <1 直接报错），避免"同一个参数两种含义"。
    options.match_required = raw->match_required;
    options.allow_one_gap = raw->allow_one_gap != 0;
    return options;
}

void validate_adapter_trim_options(const bio::AdapterTrimOptions& options) {
    if (options.match_required < 1) {
        throw std::invalid_argument("match_required 必须不小于 1，当前为 " +
                                    std::to_string(options.match_required) + "。");
    }
}

/* ------------------------------------------------------------------------- */
/* 双端 overlap 分析                                                          */
/* ------------------------------------------------------------------------- */

bio::OverlapOptions convert_overlap_options(const bio_overlap_options_t* raw) {
    bio::OverlapOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：与 fastp 命令行一致
    }
    options.diff_limit = raw->diff_limit;
    options.require = raw->require;
    // 万分之一整数 → 比例。写成 bp / 10000.0 是为了与 Python 侧的浮点字面量
    // 拿到同一个 double（除法正确舍入），从而错配上限的截断结果逐位相同。
    options.diff_percent_limit = raw->diff_percent_bp / 10000.0;
    options.allow_gap = raw->allow_gap != 0;
    return options;
}

void validate_overlap_options(const bio::OverlapOptions& options) {
    // 校验口径与 Python 侧 OverlapConfig.__post_init__ 一致。
    if (options.require < 1) {
        throw std::invalid_argument("require 必须不小于 1，当前为 " +
                                    std::to_string(options.require) + "。");
    }
    if (options.diff_limit < 0) {
        throw std::invalid_argument("diff_limit 不能为负数，当前为 " +
                                    std::to_string(options.diff_limit) + "。");
    }
    if (options.diff_percent_limit < 0.0 || options.diff_percent_limit > 1.0) {
        throw std::invalid_argument(
            "diff_percent_bp 必须在 0 到 10000 之间（万分之一），当前为 " +
            std::to_string(static_cast<int32_t>(options.diff_percent_limit * 10000.0)) +
            "。");
    }
}

/*
 * 本函数不碰文件，因此只在结果结构体上重置字段；两条 read 只做空指针检查，
 * 空串是合法输入（Python 侧同样返回"不重叠"）。
 */
bool prepare_overlap_result(const char* read1,
                            const char* read2,
                            bio_overlap_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    result->overlapped = 0;
    result->offset = 0;
    result->overlap_len = 0;
    result->diff = 0;
    result->has_gap = 0;

    if (read1 == nullptr || read2 == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "两条 read 都不能是空指针。");
        return false;
    }
    return true;
}

bool prepare_paired_merge_result(const char* read1_path,
                                 const char* read2_path,
                                 const char* output_path,
                                 bio_paired_merge_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    std::memset(result, 0, sizeof(*result));
    if (read1_path == nullptr || read2_path == nullptr || output_path == nullptr ||
        read1_path[0] == '\0' || read2_path[0] == '\0' || output_path[0] == '\0') {
        set_failure(result, BIO_ERR_ARGUMENT, "两份输入路径与输出路径不能为空。");
        return false;
    }
    return true;
}

bio::PairedMergeOptions convert_paired_merge_options(const bio_paired_merge_options_t* raw) {
    bio::PairedMergeOptions options;
    if (raw == nullptr) {
        return options;
    }
    options.overlap.diff_limit = raw->diff_limit;
    options.overlap.require = raw->require;
    options.overlap.diff_percent_limit = raw->diff_percent_bp / 10000.0;
    options.overlap.allow_gap = raw->allow_gap != 0;
    options.threads = raw->threads;
    return options;
}

/*
 * 四个路径（两份输入 + 两份输出）的公共校验：空指针、空串。
 * 两个"成对写出两份文件"的算法共用同一份实现——它们的错误措辞也该一致。
 */
template <typename Result>
bool prepare_two_output_result(const char* read1_path,
                               const char* read2_path,
                               const char* output1_path,
                               const char* output2_path,
                               Result* result) {
    if (result == nullptr) {
        return false;
    }
    std::memset(result, 0, sizeof(*result));
    if (read1_path == nullptr || read2_path == nullptr || output1_path == nullptr ||
        output2_path == nullptr || read1_path[0] == '\0' || read2_path[0] == '\0' ||
        output1_path[0] == '\0' || output2_path[0] == '\0') {
        set_failure(result, BIO_ERR_ARGUMENT,
                    "两份输入路径与两份输出路径都不能为空。");
        return false;
    }
    return true;
}

bio::PairedAdapterTrimOptions convert_paired_adapter_trim_options(
    const bio_paired_adapter_trim_options_t* raw) {
    bio::PairedAdapterTrimOptions options;
    if (raw == nullptr) {
        return options;
    }
    options.overlap.diff_limit = raw->diff_limit;
    options.overlap.require = raw->require;
    options.overlap.diff_percent_limit = raw->diff_percent_bp / 10000.0;
    options.overlap.allow_gap = raw->allow_gap != 0;
    options.front_trimmed1 = raw->front_trimmed1;
    options.front_trimmed2 = raw->front_trimmed2;
    options.threads = raw->threads;
    return options;
}

bio::PairedCorrectionOptions convert_paired_correction_options(
    const bio_paired_correction_options_t* raw) {
    bio::PairedCorrectionOptions options;
    if (raw == nullptr) {
        return options;
    }
    options.overlap.diff_limit = raw->diff_limit;
    options.overlap.require = raw->require;
    options.overlap.diff_percent_limit = raw->diff_percent_bp / 10000.0;
    options.overlap.allow_gap = raw->allow_gap != 0;
    options.threads = raw->threads;
    return options;
}

/*
 * UMI 处理的路径校验：R1 与它的输出必须给；R2 与它的输出要么都给、要么都不给。
 */
bool prepare_umi_result(const char* read1_path,
                        const char* read2_path,
                        const char* output1_path,
                        const char* output2_path,
                        bio_umi_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    std::memset(result, 0, sizeof(*result));
    if (read1_path == nullptr || output1_path == nullptr ||
        read1_path[0] == '\0' || output1_path[0] == '\0') {
        set_failure(result, BIO_ERR_ARGUMENT, "R1 的输入与输出路径不能为空。");
        return false;
    }
    const bool has_read2 = read2_path != nullptr && read2_path[0] != '\0';
    const bool has_output2 = output2_path != nullptr && output2_path[0] != '\0';
    if (has_read2 != has_output2) {
        set_failure(result, BIO_ERR_ARGUMENT,
                    "R2 的输入与输出路径必须同时给出或同时留空。");
        return false;
    }
    return true;
}

bio::UmiOptions convert_umi_options(const bio_umi_options_t* raw) {
    bio::UmiOptions options;
    if (raw == nullptr) {
        return options;
    }
    options.location = raw->location;
    options.length = raw->length;
    options.skip = raw->skip;
    if (raw->prefix != nullptr) {
        options.prefix = raw->prefix;
    }
    if (raw->delimiter != nullptr && raw->delimiter[0] != '\0') {
        options.delimiter = raw->delimiter;
    }
    options.threads = raw->threads;
    return options;
}

void validate_umi_options(const bio::UmiOptions& options) {
    if (options.location < bio::kUmiLocationIndex1 ||
        options.location > bio::kUmiLocationPerRead) {
        throw std::invalid_argument("location 必须是 1~6（见 bio_umi_options_t）。");
    }
    if (options.length < 0) {
        throw std::invalid_argument("length 不能为负数。");
    }
    if (options.skip < 0) {
        throw std::invalid_argument("skip 不能为负数。");
    }
    if (options.delimiter.empty()) {
        throw std::invalid_argument("delimiter 不能是空串。");
    }
}

}  // namespace

extern "C" {

const char* bio_native_version(void) {
    return kNativeVersion;
}

int32_t bio_quality_trim_fastq(const char* input_path,
                               const char* output_path,
                               const bio_quality_trim_options_t* options,
                               bio_result_t* result) {
    if (!prepare_result(input_path, output_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input(input_path);
    const std::string output(output_path);

    try {
        const bio::QualityCutOptions cut = convert_quality_options(options);
        validate_quality_options(cut);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        const bool compress = resolve_compress(compress_mode, input);
        const int32_t threads = (options == nullptr) ? 0 : options->threads;

        // 流水线还会回传按结果码分类的计数，本算法不用（只取通用统计）。
        // 自动线程数取单线程：本算法计算量小，实测加线程反而慢（见 AutoThreads）。
        result->stats = bio::run_pipeline(
                            input,
                            output,
                            compress,
                            threads,
                            bio::AutoThreads::Single,
                            [&cut](bio::FastqRecord& record) -> bio::StepOutcome {
                                const bio::TrimOutcome outcome =
                                    bio::compute_trim(record.sequence, record.quality, cut);
                                if (outcome.dropped) {
                                    return bio::StepOutcome{true, false};
                                }
                                const bool changed = outcome.begin != 0 ||
                                                     outcome.length != record.sequence.size();
                                bio::apply_trim(record, outcome);
                                return bio::StepOutcome{false, changed};
                            })
                            .stats;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_poly_trim_fastq(const char* input_path,
                            const char* output_path,
                            const bio_poly_trim_options_t* options,
                            bio_result_t* result) {
    if (!prepare_result(input_path, output_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input(input_path);
    const std::string output(output_path);

    try {
        const bio::PolyTrimOptions poly = convert_poly_options(options);
        validate_poly_options(poly);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        const bool compress = resolve_compress(compress_mode, input);
        const int32_t threads = (options == nullptr) ? 0 : options->threads;

        // 流水线还会回传按结果码分类的计数，本算法不用（只取通用统计）。
        // 自动线程数取单线程，理由同质量剪切。
        result->stats = bio::run_pipeline(
                            input,
                            output,
                            compress,
                            threads,
                            bio::AutoThreads::Single,
                            [&poly](bio::FastqRecord& record) -> bio::StepOutcome {
                                const bio::PolyTrimOutcome outcome =
                                    bio::compute_poly_trim(record.sequence, poly);
                                if (outcome.trimmed_bases == 0) {
                                    return bio::StepOutcome{false, false};
                                }
                                bio::apply_poly_trim(record, outcome);
                                return bio::StepOutcome{false, true};
                            })
                            .stats;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_read_filter_fastq(const char* input_path,
                              const char* output_path,
                              const char* failed_out,
                              const bio_read_filter_options_t* options,
                              bio_filter_result_t* result) {
    if (!prepare_filter_result(input_path, output_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input(input_path);
    const std::string output(output_path);
    /* 失败输出是可选的：NULL 与空串都表示"不要这份存档"。 */
    const std::string failed = (failed_out == nullptr) ? std::string() : std::string(failed_out);

    try {
        const bio::ReadFilterOptions filter = convert_filter_options(options);
        validate_filter_options(filter);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        const bool compress = resolve_compress(compress_mode, input);
        const int32_t threads = (options == nullptr) ? 0 : options->threads;

        // 自动线程数取硬件并发数：本算法逐条要跑四类判据（还可能加一次复杂度扫描），
        // 计算占比明显高于前两个算法，实测未压缩输入上 8 线程约为单线程的 2.0×。
        // 失败输出与主输出同压缩方式；它的写出发生在 writer 线程里，
        // 因此不会打乱"多线程与单线程输出逐字节一致"这条保证。
        const bio::PipelineStats outcome = bio::run_pipeline(
            input,
            output,
            compress,
            threads,
            bio::AutoThreads::Hardware,
            [&filter](bio::FastqRecord& record) -> bio::StepOutcome {
                // 过滤不改序列：命中判据就丢，否则原样写出。
                const int32_t verdict =
                    bio::filter_verdict(record.sequence, record.quality, filter);
                if (verdict != bio::kPassFilter) {
                    return bio::StepOutcome{true, false, verdict};
                }
                return bio::StepOutcome{false, false};
            },
            failed);

        result->stats = outcome.stats;
        result->breakdown.failed_quality =
            outcome.verdict_counts[bio::kFailQuality];
        result->breakdown.failed_n_base =
            outcome.verdict_counts[bio::kFailNBase];
        result->breakdown.failed_too_short =
            outcome.verdict_counts[bio::kFailLength];
        result->breakdown.failed_too_long =
            outcome.verdict_counts[bio::kFailTooLong];
        result->breakdown.failed_low_complexity =
            outcome.verdict_counts[bio::kFailComplexity];
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_detect_adapter(const char* input_path,
                           const char* const* adapters,
                           int32_t adapter_count,
                           const bio_adapter_detect_options_t* options,
                           bio_adapter_detect_result_t* result) {
    if (!prepare_detect_result(input_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::AdapterDetectOptions detect = convert_detect_options(options);
        validate_detect_options(detect);

        // 已知接头表由调用方传入（只有一份来源，见 bio_native.h 的说明）。
        std::vector<std::string> table;
        if (adapters != nullptr && adapter_count > 0) {
            table.reserve(static_cast<std::size_t>(adapter_count));
            for (int32_t index = 0; index < adapter_count; ++index) {
                if (adapters[index] == nullptr) {
                    throw std::invalid_argument("已知接头表的第 " + std::to_string(index) +
                                                " 项是空指针。");
                }
                table.emplace_back(adapters[index]);
            }
        }

        // 采样之后整批留在内存里：检测要多次遍历同一批样本（先数 k-mer，再建两棵树）。
        const std::vector<std::string> reads =
            bio::sample_sequences(std::string(input_path), detect);
        const bio::AdapterDetection outcome = bio::detect_adapter(reads, table, detect);

        result->sampled_reads = outcome.sampled_reads;
        result->sampled_bases = outcome.sampled_bases;
        result->seed_count = outcome.seed_count;
        result->seed_fold_milli = static_cast<int64_t>(outcome.seed_fold * 1000.0);
        if (outcome.detected) {
            result->source = outcome.source;
            copy_to_buffer(result->adapter, BIO_ADAPTER_SEQUENCE_CAPACITY, outcome.adapter);
            copy_to_buffer(result->seed_sequence, BIO_ADAPTER_SEQUENCE_CAPACITY,
                           outcome.seed_sequence);
        } else {
            result->source = BIO_ADAPTER_SOURCE_NONE;
            copy_to_buffer(result->reason, BIO_DETECT_REASON_CAPACITY, outcome.reason);
        }
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_trim_adapter_fastq(const char* input_path,
                               const char* output_path,
                               const char* const* adapters,
                               int32_t adapter_count,
                               const bio_adapter_trim_options_t* options,
                               bio_result_t* result) {
    if (!prepare_result(input_path, output_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input(input_path);
    const std::string output(output_path);

    try {
        bio::AdapterTrimOptions trim = convert_adapter_trim_options(options);
        // 接头表由调用方传入；空表表示不裁（文件按原样写出，统计照常给出）。
        if (adapters != nullptr && adapter_count > 0) {
            trim.adapters.reserve(static_cast<std::size_t>(adapter_count));
            for (int32_t index = 0; index < adapter_count; ++index) {
                if (adapters[index] == nullptr) {
                    throw std::invalid_argument("接头表的第 " + std::to_string(index) +
                                                " 项是空指针。");
                }
                trim.adapters.emplace_back(adapters[index]);
            }
        }
        validate_adapter_trim_options(trim);

        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        const bool compress = resolve_compress(compress_mode, input);
        const int32_t threads = (options == nullptr) ? 0 : options->threads;

        // 本算法不丢 read：保留条数恒等于输入条数，"改动条数"就是被裁过的条数。
        // 自动线程数暂取单线程：裁剪是逐位置比对，多线程是否有收益尚未实测（见 5.4.5）。
        result->stats = bio::run_pipeline(
                            input,
                            output,
                            compress,
                            threads,
                            bio::AutoThreads::Single,
                            [&trim](bio::FastqRecord& record) -> bio::StepOutcome {
                                const bio::AdapterTrimOutcome outcome =
                                    bio::apply_adapter_trim(record, trim);
                                return bio::StepOutcome{false, outcome.trimmed};
                            })
                            .stats;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_merge_paired_fastq(const char* read1_path,
                               const char* read2_path,
                               const char* output_path,
                               const bio_paired_merge_options_t* options,
                               bio_paired_merge_result_t* result) {
    if (!prepare_paired_merge_result(read1_path, read2_path, output_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::PairedMergeOptions settings = convert_paired_merge_options(options);
        validate_overlap_options(settings.overlap);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT && compress_mode != BIO_COMPRESS_OFF &&
            compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }
        const std::string read1(read1_path);
        const std::string read2(read2_path);
        const std::string output(output_path);
        const bool compress = resolve_compress(compress_mode, read1) ||
                              (compress_mode == BIO_COMPRESS_FOLLOW_INPUT && resolve_compress(compress_mode, read2));
        const bio::PairedMergeStats stats =
            bio::merge_paired_fastq(read1, read2, output, compress, settings);
        result->status = BIO_OK;
        copy_to_buffer(result->message, BIO_MESSAGE_CAPACITY, "");
        result->total_pairs = stats.total_pairs;
        result->merged_pairs = stats.merged_pairs;
        result->unmerged_pairs = stats.unmerged_pairs;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        result->gap_overlaps = stats.gap_overlaps;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

int32_t bio_trim_paired_adapter_fastq(const char* read1_path,
                                      const char* read2_path,
                                      const char* output1_path,
                                      const char* output2_path,
                                      const bio_paired_adapter_trim_options_t* options,
                                      bio_paired_adapter_trim_result_t* result) {
    if (!prepare_two_output_result(read1_path, read2_path, output1_path,
                                   output2_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::PairedAdapterTrimOptions settings =
            convert_paired_adapter_trim_options(options);
        validate_overlap_options(settings.overlap);
        if (settings.front_trimmed1 < 0 || settings.front_trimmed2 < 0) {
            throw std::invalid_argument("front_trimmed1 与 front_trimmed2 不能为负数。");
        }
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }
        const std::string read1(read1_path);
        const std::string read2(read2_path);
        const std::string output1(output1_path);
        const std::string output2(output2_path);
        const bool compress =
            resolve_compress(compress_mode, read1) ||
            (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
             resolve_compress(compress_mode, read2));
        const bio::PairedAdapterTrimStats stats = bio::trim_paired_adapter_fastq(
            read1, read2, output1, output2, compress, settings);
        result->status = BIO_OK;
        copy_to_buffer(result->message, BIO_MESSAGE_CAPACITY, "");
        result->total_pairs = stats.total_pairs;
        result->trimmed_pairs = stats.trimmed_pairs;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        result->trimmed_bases = stats.trimmed_bases;
        return BIO_OK;
    } catch (...) {
        /* 半成品已由算法层删除（两个输出成对处理）。 */
        return translate_exception(result);
    }
}

int32_t bio_correct_paired_fastq(const char* read1_path,
                                 const char* read2_path,
                                 const char* output1_path,
                                 const char* output2_path,
                                 const bio_paired_correction_options_t* options,
                                 bio_paired_correction_result_t* result) {
    if (!prepare_two_output_result(read1_path, read2_path, output1_path,
                                   output2_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::PairedCorrectionOptions settings =
            convert_paired_correction_options(options);
        validate_overlap_options(settings.overlap);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }
        const std::string read1(read1_path);
        const std::string read2(read2_path);
        const std::string output1(output1_path);
        const std::string output2(output2_path);
        const bool compress =
            resolve_compress(compress_mode, read1) ||
            (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
             resolve_compress(compress_mode, read2));
        const bio::PairedCorrectionStats stats = bio::correct_paired_fastq(
            read1, read2, output1, output2, compress, settings);
        result->status = BIO_OK;
        copy_to_buffer(result->message, BIO_MESSAGE_CAPACITY, "");
        result->total_pairs = stats.total_pairs;
        result->corrected_pairs = stats.corrected_pairs;
        result->corrected_reads = stats.corrected_reads;
        result->corrected_bases = stats.corrected_bases;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        return BIO_OK;
    } catch (...) {
        /* 半成品已由算法层删除（两个输出成对处理）。 */
        return translate_exception(result);
    }
}

int32_t bio_process_umi_fastq(const char* read1_path,
                              const char* read2_path,
                              const char* output1_path,
                              const char* output2_path,
                              const bio_umi_options_t* options,
                              bio_umi_result_t* result) {
    if (!prepare_umi_result(read1_path, read2_path, output1_path, output2_path,
                            result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::UmiOptions settings = convert_umi_options(options);
        validate_umi_options(settings);
        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }
        const std::string read1(read1_path);
        const std::string output1(output1_path);

        bio::UmiStats stats;
        if (read2_path != nullptr && read2_path[0] != '\0') {
            /* 双端：成对读、成对写。 */
            const std::string read2(read2_path);
            const std::string output2(output2_path);
            const bool compress =
                resolve_compress(compress_mode, read1) ||
                (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
                 resolve_compress(compress_mode, read2));
            stats = bio::process_umi_paired(read1, read2, output1, output2,
                                            compress, settings);
        } else {
            /* 单端：只读一份、写一份。 */
            const bool compress = resolve_compress(compress_mode, read1);
            stats = bio::process_umi_single(read1, output1, compress, settings);
        }

        result->status = BIO_OK;
        copy_to_buffer(result->message, BIO_MESSAGE_CAPACITY, "");
        result->total_reads = stats.total_reads;
        result->reads_with_umi = stats.reads_with_umi;
        result->trimmed_bases = stats.trimmed_bases;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        return BIO_OK;
    } catch (...) {
        /* 半成品由算法层删除。 */
        return translate_exception(result);
    }
}

int32_t bio_deduplicate_fastq(const char* read1_path,
                              const char* read2_path,
                              const char* output1_path,
                              const char* output2_path,
                              const bio_dedup_options_t* options,
                              bio_dedup_result_t* result) {
    if (!prepare_dedup_result(read1_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    /* 输出可以留空（只评估），因此三个路径都按"可空"解析。 */
    const std::string input1(read1_path);
    const std::string input2 = optional_path(read2_path);
    const std::string output1 = optional_path(output1_path);
    const std::string output2 = optional_path(output2_path);

    try {
        if (input2.empty() && !output2.empty()) {
            throw std::invalid_argument("没有给出 R2 输入，却给了第二份输出路径。");
        }
        if (!input2.empty() && (output1.empty() != output2.empty())) {
            throw std::invalid_argument(
                "双端去重要么同时给出两份输出路径，要么都不给（只做评估）。");
        }

        const bool dedup = !output1.empty();
        bio::DedupOptions settings;
        settings.dedup = dedup;
        /* 档位：调用方给了就用它，没给（<= 0）按模式取默认——
         * 只评估省内存取 1、真去重多花内存取 3（判错了要丢数据）。 */
        const int32_t fallback_level =
            dedup ? bio::kDefaultAccuracyDedup : bio::kDefaultAccuracyAnalyze;
        settings.accuracy_level =
            (options != nullptr && options->accuracy_level > 0)
                ? options->accuracy_level
                : fallback_level;
        if (options != nullptr && options->buffer_bytes > 0) {
            settings.buffer_bytes = static_cast<uint64_t>(options->buffer_bytes);
        }

        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }

        bio::DedupStats stats;
        if (input2.empty()) {
            /* 单端：只读一份。压缩跟随输入时只看这一份。 */
            const bool compress = resolve_compress(compress_mode, input1);
            stats = bio::deduplicate_single(input1, output1, compress, settings);
        } else {
            /* 双端：成对读、成对写。任一输入是 gzip 就让输出也用 gzip。 */
            const bool compress =
                resolve_compress(compress_mode, input1) ||
                (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
                 resolve_compress(compress_mode, input2));
            stats = bio::deduplicate_paired(input1, output1, input2, output2,
                                            compress, settings);
        }

        result->stats = stats.stats;
        result->duplicate_reads = stats.duplicate_reads;
        result->accuracy_level = stats.accuracy_level;
        return BIO_OK;
    } catch (...) {
        /* 半成品由算法层删除；这里只做翻译。 */
        return translate_exception(result);
    }
}

/* ------------------------------------------------------------------------- */
/* reads 质量统计                                                             */
/* ------------------------------------------------------------------------- */

/* 不透明句柄的实际定义放在这里而不是头文件里：调用方只该通过下面这组函数
 * 使用它，内部布局怎么变都不该影响 ABI。 */
struct bio_stats_handle {
    bio::ReadStats value;
};

bio_stats_handle* bio_stats_create(void) {
    try {
        return new bio_stats_handle();
    } catch (...) {
        /* 只在内存耗尽时会发生；返回空指针，调用方按"创建失败"处理。 */
        return nullptr;
    }
}

void bio_stats_destroy(bio_stats_handle* stats) {
    delete stats;
}

int32_t bio_stats_scan_fastq(bio_stats_handle* stats,
                             const char* input_path,
                             bio_report_t* report) {
    if (report == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    report->status = BIO_OK;
    report->message[0] = '\0';
    if (stats == nullptr || input_path == nullptr) {
        set_failure(report, BIO_ERR_ARGUMENT, "统计器与输入路径不能为空。");
        return report->status;
    }

    try {
        /* 先把路径收进具名变量：直接写 ``FastqReader reader(std::string(path))``
         * 会被编译器当成函数声明（C++ 的 most vexing parse）。 */
        const std::string path(input_path);
        bio::FastqReader reader(path);
        bio::FastqRecord record;
        while (reader.next(record)) {
            /* 正常 FASTQ 由读取器保证等长；这里只是把"万一"挡在累加之前，
             * 免得越界读到质量串之外。 */
            if (record.sequence.size() != record.quality.size()) {
                throw bio::FastqFormatError("记录的序列长度与质量长度不一致。");
            }
            stats->value.add(record.sequence, record.quality);
        }
        return BIO_OK;
    } catch (...) {
        return translate_exception(report);
    }
}

int32_t bio_stats_summary(bio_stats_handle* stats, bio_stat_summary_t* result) {
    if (result == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    std::memset(result, 0, sizeof(*result));
    result->status = BIO_OK;
    if (stats == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "统计器不能为空。");
        return result->status;
    }

    stats->value.summarize();
    result->total_reads = stats->value.total_reads();
    result->total_bases = stats->value.total_bases();
    result->q20_bases = stats->value.q20_bases();
    result->q30_bases = stats->value.q30_bases();
    result->q40_bases = stats->value.q40_bases();
    result->gc_bases = stats->value.gc_bases();
    result->mean_length = stats->value.mean_length();
    result->cycles = stats->value.cycles();
    result->max_length = stats->value.max_length();
    return BIO_OK;
}

int32_t bio_stats_curve(bio_stats_handle* stats,
                        int32_t kind,
                        double* values,
                        int32_t capacity,
                        int32_t* written) {
    if (written == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    *written = 0;
    if (stats == nullptr || values == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    if (kind < 0 || kind >= bio::kStatCurveCount) {
        return BIO_ERR_ARGUMENT;
    }

    stats->value.summarize();
    const std::size_t length = static_cast<std::size_t>(stats->value.cycles());
    *written = static_cast<int32_t>(length);
    if (capacity < static_cast<int32_t>(length)) {
        return BIO_ERR_OUTPUT;
    }
    if (length == 0) {
        return BIO_OK;
    }
    const double* source = stats->value.curve(static_cast<bio::StatCurve>(kind));
    if (source == nullptr) {
        *written = 0;
        return BIO_ERR_INTERNAL;
    }
    std::copy(source, source + length, values);
    return BIO_OK;
}

int32_t bio_stats_quality_histogram(bio_stats_handle* stats,
                                    int64_t* counts,
                                    int32_t capacity,
                                    int32_t* written) {
    if (written == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    *written = 0;
    if (stats == nullptr || counts == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    const std::size_t length = static_cast<std::size_t>(bio::kStatMaxPhred + 1);
    *written = static_cast<int32_t>(length);
    if (capacity < static_cast<int32_t>(length)) {
        return BIO_ERR_OUTPUT;
    }
    /* 内部按**质量字符码**（0~127）存，对外按 **Phred 值**（0~93）报：
     * 字符码是 Phred+33 的表示细节，调用方看到的应该是 Phred。
     * 这里不做转换的话，两侧的直方图键会整整差 33。 */
    const int64_t* source = stats->value.quality_histogram();
    constexpr std::size_t kPhredOffset = 33;
    for (std::size_t phred = 0; phred < length; ++phred) {
        counts[phred] = source[phred + kPhredOffset];
    }
    return BIO_OK;
}

int32_t bio_stats_kmer(bio_stats_handle* stats,
                       int64_t* counts,
                       int32_t capacity,
                       int32_t* written) {
    if (written == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    *written = 0;
    if (stats == nullptr || counts == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    const std::size_t length = bio::kStatKmerBuckets;
    *written = static_cast<int32_t>(length);
    if (capacity < static_cast<int32_t>(length)) {
        return BIO_ERR_OUTPUT;
    }
    std::copy_n(stats->value.kmer_counts(), length, counts);
    return BIO_OK;
}

int32_t bio_stats_lengths(bio_stats_handle* stats,
                          int64_t* counts,
                          int32_t capacity,
                          int32_t* written) {
    if (written == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    *written = 0;
    if (stats == nullptr || counts == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    const std::size_t length = stats->value.length_count_size();
    *written = static_cast<int32_t>(length);
    if (capacity < static_cast<int32_t>(length)) {
        return BIO_ERR_OUTPUT;
    }
    if (length > 0) {
        std::copy_n(stats->value.length_counts(), length, counts);
    }
    return BIO_OK;
}

/* ------------------------------------------------------------------------- */
/* 双端插入片段长度分布                                                       */
/* ------------------------------------------------------------------------- */

int32_t bio_insert_size_fastq(const char* read1_path,
                              const char* read2_path,
                              const bio_insert_size_options_t* options,
                              bio_insert_size_result_t* result,
                              int64_t* histogram,
                              int32_t histogram_capacity,
                              int32_t* histogram_length) {
    if (result == nullptr || histogram_length == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    std::memset(result, 0, sizeof(*result));
    result->status = BIO_OK;
    *histogram_length = 0;
    if (read1_path == nullptr || read2_path == nullptr || histogram == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT,
                    "两条输入路径与直方图缓冲区都不能为空。");
        return result->status;
    }

    try {
        bio::InsertSizeOptions settings;
        if (options != nullptr) {
            if (options->max_size > 0) {
                settings.max_size = options->max_size;
            }
            settings.overlap.diff_limit = options->diff_limit;
            settings.overlap.require = options->require;
            if (options->diff_percent_bp > 0) {
                settings.overlap.diff_percent_limit =
                    static_cast<double>(options->diff_percent_bp) / 10000.0;
            }
            settings.overlap.allow_gap = options->allow_gap != 0;
        }

        const std::string input1(read1_path);
        const std::string input2(read2_path);
        const bio::InsertSizeStats stats =
            bio::analyze_insert_size(input1, input2, settings);

        const int32_t length = static_cast<int32_t>(stats.histogram.size());
        *histogram_length = length;
        if (histogram_capacity < length) {
            set_failure(result, BIO_ERR_OUTPUT,
                        "直方图缓冲区太小：需要 " + std::to_string(length) +
                            " 个 int64。");
            return result->status;
        }
        std::copy(stats.histogram.begin(), stats.histogram.end(), histogram);

        result->total_pairs = stats.total_pairs;
        result->overlapped_pairs = stats.overlapped_pairs;
        result->peak_size = stats.peak_size;
        result->max_size = settings.max_size;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

/* ------------------------------------------------------------------------- */
/* 过表达序列分析                                                             */
/* ------------------------------------------------------------------------- */

struct bio_overrep_handle {
    bio::OverrepOptions options;
    bio::OverrepResult value;
};

bio_overrep_handle* bio_overrep_create(const bio_overrep_options_t* options) {
    try {
        auto* handle = new bio_overrep_handle();
        if (options != nullptr) {
            /* <= 0 一律当作"用默认值"：这几个都是可以不传的参数。 */
            if (options->sampling > 0) {
                handle->options.sampling = options->sampling;
            }
            if (options->base_limit > 0) {
                handle->options.base_limit = options->base_limit;
            }
            if (options->seq_length_sample > 0) {
                handle->options.seq_length_sample = options->seq_length_sample;
            }
        }
        return handle;
    } catch (...) {
        return nullptr;
    }
}

void bio_overrep_destroy(bio_overrep_handle* handle) {
    delete handle;
}

int32_t bio_overrep_scan_fastq(bio_overrep_handle* handle,
                               const char* input_path,
                               bio_report_t* report) {
    if (report == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    report->status = BIO_OK;
    report->message[0] = '\0';
    if (handle == nullptr || input_path == nullptr) {
        set_failure(report, BIO_ERR_ARGUMENT, "句柄与输入路径不能为空。");
        return report->status;
    }

    try {
        const std::string path(input_path);
        handle->value = bio::find_overrepresented_sequences(path, handle->options);
        return BIO_OK;
    } catch (...) {
        return translate_exception(report);
    }
}

int32_t bio_overrep_summary(bio_overrep_handle* handle,
                            bio_overrep_summary_t* result) {
    if (result == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    std::memset(result, 0, sizeof(*result));
    result->status = BIO_OK;
    if (handle == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "句柄不能为空。");
        return result->status;
    }

    const bio::OverrepResult& value = handle->value;
    result->total_reads = value.total_reads;
    result->total_bases = value.total_bases;
    result->sampled_reads = value.sampled_reads;
    result->seq_length = value.seq_length;
    result->sampling = value.sampling;
    result->sequence_count = static_cast<int32_t>(value.entries.size());
    result->max_sequence_length = 0;
    for (const bio::OverrepEntry& entry : value.entries) {
        result->max_sequence_length =
            std::max(result->max_sequence_length,
                     static_cast<int32_t>(entry.sequence.size()));
    }
    return BIO_OK;
}

int32_t bio_overrep_sequence(bio_overrep_handle* handle,
                             int32_t index,
                             char* sequence,
                             int32_t sequence_capacity,
                             int64_t* count,
                             int64_t* estimated_count,
                             double* base_percent,
                             int64_t* distribution,
                             int32_t distribution_capacity,
                             int32_t* distribution_length) {
    if (distribution_length == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    *distribution_length = 0;
    if (handle == nullptr || sequence == nullptr || count == nullptr ||
        estimated_count == nullptr || base_percent == nullptr ||
        distribution == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    if (index < 0 ||
        static_cast<std::size_t>(index) >= handle->value.entries.size()) {
        return BIO_ERR_ARGUMENT;
    }

    const bio::OverrepEntry& entry =
        handle->value.entries[static_cast<std::size_t>(index)];
    if (sequence_capacity < static_cast<int32_t>(entry.sequence.size()) + 1) {
        return BIO_ERR_OUTPUT;
    }
    const int32_t length = static_cast<int32_t>(entry.distribution.size());
    *distribution_length = length;
    if (distribution_capacity < length) {
        return BIO_ERR_OUTPUT;
    }

    std::memcpy(sequence, entry.sequence.c_str(), entry.sequence.size() + 1);
    *count = entry.count;
    *estimated_count = entry.estimated_count;
    *base_percent = entry.base_percent;
    if (length > 0) {
        std::copy(entry.distribution.begin(), entry.distribution.end(),
                  distribution);
    }
    return BIO_OK;
}

/* 规范化结果没有 bio_stats_t（它的字段自成一套），单独重置。 */
bool prepare_normalize_result(const char* input_path,
                              const char* output1_path,
                              bio_normalize_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    result->total_reads = 0;
    result->renamed_reads = 0;
    result->requantified_reads = 0;
    result->input_bases = 0;
    result->output_bases = 0;

    if (input_path == nullptr || output1_path == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "输入与输出路径不能为空。");
        return false;
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* reads 规范化                                                               */
/* ------------------------------------------------------------------------- */

int32_t bio_normalize_fastq(const char* read1_path,
                            const char* read2_path,
                            const char* output1_path,
                            const char* output2_path,
                            const bio_normalize_options_t* options,
                            bio_normalize_result_t* result) {
    if (!prepare_normalize_result(read1_path, output1_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input1(read1_path);
    const std::string output1(output1_path);
    const std::string input2 = optional_path(read2_path);
    const std::string output2 = optional_path(output2_path);

    try {
        if (input2.empty() != output2.empty()) {
            throw std::invalid_argument(
                "双端规范化需要同时给出 R2 输入与输出，或者都不给（单端）。");
        }

        bio::NormalizeOptions settings;
        if (options != nullptr) {
            if (options->input_phred > 0) {
                settings.input_phred = options->input_phred;
            }
            settings.fix_mgi = options->fix_mgi != 0;
        }
        if (settings.input_phred != bio::kPhredOffset33 &&
            settings.input_phred != bio::kPhredOffset64) {
            throw std::invalid_argument("input_phred 只能是 33 或 64，当前为 " +
                                        std::to_string(settings.input_phred) + "。");
        }

        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }

        bio::NormalizeStats stats;
        if (input2.empty()) {
            const bool compress = resolve_compress(compress_mode, input1);
            stats = bio::normalize_single(input1, output1, compress, settings);
        } else {
            const bool compress =
                resolve_compress(compress_mode, input1) ||
                (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
                 resolve_compress(compress_mode, input2));
            stats = bio::normalize_paired(input1, output1, input2, output2,
                                          compress, settings);
        }

        result->total_reads = stats.total_reads;
        result->renamed_reads = stats.renamed_reads;
        result->requantified_reads = stats.requantified_reads;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        return BIO_OK;
    } catch (...) {
        /* 半成品由算法层删除；这里只做翻译。 */
        return translate_exception(result);
    }
}

/* 按 index 过滤的结果没有 bio_stats_t，单独重置。 */
bool prepare_index_filter_result(const char* input_path,
                                 const char* output1_path,
                                 bio_index_filter_result_t* result) {
    if (result == nullptr) {
        return false;
    }
    result->status = BIO_OK;
    result->message[0] = '\0';
    result->total_reads = 0;
    result->filtered_reads = 0;
    result->input_bases = 0;
    result->output_bases = 0;

    if (input_path == nullptr || output1_path == nullptr) {
        set_failure(result, BIO_ERR_ARGUMENT, "输入与输出路径不能为空。");
        return false;
    }
    return true;
}

/* C ABI 的字符串数组 → std::vector<std::string>（跳过 NULL 项）。 */
std::vector<std::string> to_string_list(const char* const* items, int32_t count) {
    std::vector<std::string> result;
    if (items == nullptr || count <= 0) {
        return result;
    }
    result.reserve(static_cast<std::size_t>(count));
    for (int32_t index = 0; index < count; ++index) {
        if (items[index] != nullptr) {
            result.emplace_back(items[index]);
        }
    }
    return result;
}

/* ------------------------------------------------------------------------- */
/* 按 index 过滤                                                              */
/* ------------------------------------------------------------------------- */

int32_t bio_index_filter_fastq(const char* read1_path,
                               const char* read2_path,
                               const char* output1_path,
                               const char* output2_path,
                               const bio_index_filter_options_t* options,
                               bio_index_filter_result_t* result) {
    if (!prepare_index_filter_result(read1_path, output1_path, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    const std::string input1(read1_path);
    const std::string output1(output1_path);
    const std::string input2 = optional_path(read2_path);
    const std::string output2 = optional_path(output2_path);

    try {
        if (input2.empty() != output2.empty()) {
            throw std::invalid_argument(
                "双端过滤需要同时给出 R2 输入与输出，或者都不给（单端）。");
        }

        bio::IndexFilterOptions settings;
        if (options != nullptr) {
            settings.blacklist1 =
                to_string_list(options->blacklist1, options->blacklist1_count);
            settings.blacklist2 =
                to_string_list(options->blacklist2, options->blacklist2_count);
            if (options->threshold >= 0) {
                settings.threshold = options->threshold;
            }
        }

        const int32_t compress_mode =
            (options == nullptr) ? BIO_COMPRESS_FOLLOW_INPUT : options->compress;
        if (compress_mode != BIO_COMPRESS_FOLLOW_INPUT &&
            compress_mode != BIO_COMPRESS_OFF && compress_mode != BIO_COMPRESS_ON) {
            throw std::invalid_argument("compress 只能是 -1、0 或 1。");
        }

        bio::IndexFilterStats stats;
        if (input2.empty()) {
            const bool compress = resolve_compress(compress_mode, input1);
            stats = bio::filter_by_index_single(input1, output1, compress, settings);
        } else {
            const bool compress =
                resolve_compress(compress_mode, input1) ||
                (compress_mode == BIO_COMPRESS_FOLLOW_INPUT &&
                 resolve_compress(compress_mode, input2));
            stats = bio::filter_by_index_paired(input1, output1, input2, output2,
                                                compress, settings);
        }

        result->total_reads = stats.total_reads;
        result->filtered_reads = stats.filtered_reads;
        result->input_bases = stats.input_bases;
        result->output_bases = stats.output_bases;
        return BIO_OK;
    } catch (...) {
        /* 半成品由算法层删除；这里只做翻译。 */
        return translate_exception(result);
    }
}

int32_t bio_analyze_overlap(const char* read1,
                            const char* read2,
                            const bio_overlap_options_t* options,
                            bio_overlap_result_t* result) {
    if (!prepare_overlap_result(read1, read2, result)) {
        return result == nullptr ? BIO_ERR_ARGUMENT : result->status;
    }

    try {
        const bio::OverlapOptions config = convert_overlap_options(options);
        validate_overlap_options(config);

        const bio::OverlapOutcome outcome = bio::analyze_overlap(
            std::string_view(read1), std::string_view(read2), config);

        result->overlapped = outcome.overlapped ? 1 : 0;
        result->offset = outcome.offset;
        result->overlap_len = outcome.overlap_len;
        result->diff = outcome.diff;
        result->has_gap = outcome.has_gap ? 1 : 0;
        return BIO_OK;
    } catch (...) {
        return translate_exception(result);
    }
}

/* ------------------------------------------------------------------------- */
/* 二色测序系统判定                                                           */
/* ------------------------------------------------------------------------- */

int32_t bio_detect_two_color_system(const char* read1_path,
                                    int32_t* is_two_color,
                                    bio_report_t* report) {
    bio_report_t local;
    bio_report_t* target = report != nullptr ? report : &local;
    target->status = BIO_OK;
    target->message[0] = '\0';
    if (is_two_color != nullptr) {
        *is_two_color = 0;
    }

    if (read1_path == nullptr || is_two_color == nullptr) {
        return set_failure(target, BIO_ERR_ARGUMENT, "输入路径与结果指针不能为空。");
    }

    try {
        const std::string path(read1_path);
        *is_two_color = bio::is_two_color_system(path) ? 1 : 0;
        return BIO_OK;
    } catch (...) {
        return translate_exception(target);
    }
}

/* ------------------------------------------------------------------------- */
/* 工作流                                                                     */
/* ------------------------------------------------------------------------- */

bool bytes_are_all_zero(const void* data, std::size_t size) {
    const unsigned char* bytes = static_cast<const unsigned char*>(data);
    for (std::size_t index = 0; index < size; ++index) {
        if (bytes[index] != 0) {
            return false;
        }
    }
    return true;
}

/*
 * 工作流参数转换。
 *
 * 有一处约定要留意：**子结构体全零视为"没给"**，回退到该算法的默认值。
 * C 调用方习惯用 memset 清零，而各子结构体的默认值不是零（质量窗口 4、
 * poly 最短 10、过滤阈值 15/40/5、overlap 的 5/30/2000……）。不这么处理的话，
 * "清零之后静默换了一套参数"就是个很难发现的坑。
 */
bio::WorkflowOptions convert_workflow_options(const bio_workflow_options_t* raw) {
    bio::WorkflowOptions options;
    if (raw == nullptr) {
        return options;  // 全默认：会因为缺输入输出路径而失败，这是对的
    }

    /* 通用规则：全零的子结构体按"没给"处理。 */
    const auto given = [](const void* data, std::size_t size) -> const void* {
        return bytes_are_all_zero(data, size) ? nullptr : data;
    };

    options.read1_path = optional_path(raw->read1_path);
    options.read2_path = optional_path(raw->read2_path);
    options.output1_path = optional_path(raw->output1_path);
    options.output2_path = optional_path(raw->output2_path);
    options.unpaired_path = optional_path(raw->unpaired_path);
    options.failed_path = optional_path(raw->failed_out);
    options.merged_path = optional_path(raw->merged_out);
    options.overlapped_path = optional_path(raw->overlapped_out);

    options.normalize_enabled = raw->normalize_enabled != 0;
    options.normalize.input_phred =
        raw->input_phred > 0 ? raw->input_phred : bio::kPhredOffset33;
    options.normalize.fix_mgi = raw->fix_mgi != 0;

    options.index_filter_enabled = raw->index_filter_enabled != 0;
    options.index_filter.blacklist1 =
        to_string_list(raw->index_blacklist1, raw->index_blacklist1_count);
    options.index_filter.blacklist2 =
        to_string_list(raw->index_blacklist2, raw->index_blacklist2_count);
    options.index_filter.threshold =
        raw->index_filter_threshold > 0 ? raw->index_filter_threshold : 0;

    options.umi_enabled = raw->umi_enabled != 0;
    options.umi.location = raw->umi_location;
    options.umi.length = raw->umi_length;
    options.umi.skip = raw->umi_skip;
    options.umi.prefix = optional_path(raw->umi_prefix);
    if (raw->umi_delimiter != nullptr && raw->umi_delimiter[0] != '\0') {
        options.umi.delimiter = raw->umi_delimiter;
    }

    options.quality_cut = convert_quality_options(
        static_cast<const bio_quality_trim_options_t*>(
            given(&raw->quality_trim, sizeof(raw->quality_trim))));

    options.max_length1 = raw->max_length1 > 0 ? raw->max_length1 : 0;
    options.max_length2 = raw->max_length2 > 0 ? raw->max_length2 : 0;

    options.poly_trim = convert_poly_options(
        static_cast<const bio_poly_trim_options_t*>(
            given(&raw->poly_trim, sizeof(raw->poly_trim))));

    options.adapter_enabled = raw->adapter_enabled != 0;
    options.adapter.adapters =
        to_string_list(raw->adapter_sequences, raw->adapter_count);
    options.adapter.match_required =
        raw->adapter_match_required > 0 ? raw->adapter_match_required : 4;
    options.adapter.allow_one_gap = raw->adapter_allow_one_gap != 0;
    options.adapter_r2.adapters =
        to_string_list(raw->adapter_sequences_r2, raw->adapter_count_r2);
    options.adapter_r2.match_required = options.adapter.match_required;
    options.adapter_r2.allow_one_gap = options.adapter.allow_one_gap;
    options.adapter_dimer_enabled = raw->adapter_dimer_enabled != 0;
    options.adapter_dimer_max_len =
        raw->adapter_dimer_max_len >= 0 ? raw->adapter_dimer_max_len : 2;

    options.overlap = convert_overlap_options(
        static_cast<const bio_overlap_options_t*>(
            given(&raw->overlap, sizeof(raw->overlap))));
    options.correction_enabled = raw->correction_enabled != 0;
    /* 给了合并输出路径就等于要合并（两个途径任一生效）。 */
    options.merge_enabled = raw->merge_enabled != 0 || !options.merged_path.empty();
    options.merge_include_unmerged = raw->merge_include_unmerged != 0;

    options.filter = convert_filter_options(
        static_cast<const bio_read_filter_options_t*>(
            given(&raw->filter, sizeof(raw->filter))));

    options.dedup_evaluate = raw->dedup_evaluate != 0;
    options.dedup_enabled = raw->dedup_enabled != 0;
    options.dedup_accuracy_level = raw->dedup_accuracy_level;
    options.dedup_buffer_bytes = raw->dedup_buffer_bytes > 0
                                     ? static_cast<uint64_t>(raw->dedup_buffer_bytes)
                                     : 0;

    options.stats_enabled = raw->stats_enabled != 0;
    options.split_records = raw->split_records > 0 ? raw->split_records : 0;
    options.split_digits = raw->split_digits >= 0 ? raw->split_digits : 4;
    options.threads = raw->threads;
    options.max_reads = raw->max_reads > 0 ? raw->max_reads : 0;
    return options;
}

/*
 * 工作流结果句柄的实际布局。
 *
 * 四个 ``bio_stats_handle`` 直接**内联**在句柄里：工作流把统计写进它们的
 * ``value``，调用方通过 ``bio_workflow_stats`` 拿到借用指针，随句柄一起销毁。
 * 这样既不必在跑完之后把曲线再复制一份，也不必让工作流知道句柄这种东西。
 */
struct bio_workflow_result {
    bio_stats_handle pre1;
    bio_stats_handle pre2;
    bio_stats_handle post1;
    bio_stats_handle post2;
    bio::WorkflowResult value;
};

int32_t bio_run_workflow(const bio_workflow_options_t* options,
                         bio_workflow_result_t** result,
                         bio_report_t* report) {
    bio_report_t local;
    bio_report_t* target = report != nullptr ? report : &local;
    target->status = BIO_OK;
    target->message[0] = '\0';

    if (result == nullptr) {
        return set_failure(target, BIO_ERR_ARGUMENT, "结果指针不能为空。");
    }
    *result = nullptr;

    try {
        bio::WorkflowOptions settings = convert_workflow_options(options);

        auto* handle = new bio_workflow_result();
        /* 四个统计器由结果句柄持有；工作流直接往里累加，不再复制一份。 */
        settings.stats.pre1 = &handle->pre1.value;
        settings.stats.pre2 = &handle->pre2.value;
        settings.stats.post1 = &handle->post1.value;
        settings.stats.post2 = &handle->post2.value;

        try {
            handle->value = bio::run_workflow(settings);
        } catch (...) {
            delete handle;
            throw;
        }
        *result = handle;
        return BIO_OK;
    } catch (...) {
        return translate_exception(target);
    }
}

void bio_workflow_destroy(bio_workflow_result_t* result) {
    delete result;
}

bio_stats_handle* bio_workflow_stats(bio_workflow_result_t* result, int32_t slot) {
    if (result == nullptr) {
        return nullptr;
    }
    switch (slot) {
        case BIO_WORKFLOW_STATS_PRE1: return &result->pre1;
        case BIO_WORKFLOW_STATS_PRE2: return &result->pre2;
        case BIO_WORKFLOW_STATS_POST1: return &result->post1;
        case BIO_WORKFLOW_STATS_POST2: return &result->post2;
        default: return nullptr;
    }
}

int32_t bio_workflow_summary(bio_workflow_result_t* result,
                             bio_workflow_summary_t* summary) {
    if (summary == nullptr) {
        return BIO_ERR_ARGUMENT;
    }
    summary->status = BIO_OK;
    summary->message[0] = '\0';
    if (result == nullptr) {
        return set_failure(summary, BIO_ERR_ARGUMENT, "结果句柄不能为空。");
    }

    const bio::WorkflowResult& value = result->value;
    summary->total_reads = value.total_reads;
    summary->total_bases = value.total_bases;
    summary->output_reads = value.output_reads;
    summary->output_bases = value.output_bases;
    summary->unpaired_reads = value.unpaired_reads;
    summary->pairs_total = value.pairs_total;
    summary->normalized_reads = value.normalized_reads;
    summary->index_filtered_reads = value.index_filtered_reads;
    summary->umi_tagged_reads = value.umi_tagged_reads;
    summary->duplicate_reads = value.duplicate_reads;
    summary->dedup_accuracy_level = value.dedup_accuracy_level;
    summary->trimmed_reads = value.trimmed_reads;
    summary->poly_trimmed_reads = value.poly_trimmed_reads;
    summary->adapter_trimmed_reads = value.adapter_trimmed_reads;
    summary->adapter_dimer_pairs = value.adapter_dimer_pairs;
    summary->corrected_pairs = value.corrected_pairs;
    summary->corrected_bases = value.corrected_bases;
    summary->filtered_reads = value.filtered_reads;
    summary->filter_breakdown = value.filter_breakdown;
    summary->pairs_merged = value.pairs_merged;
    summary->gap_overlap_pairs = value.gap_overlap_pairs;
    summary->insert_size_peak = value.insert_size_peak;
    summary->insert_size_unknown = value.insert_size_unknown;
    summary->split_file_count = value.split_file_count;
    return BIO_OK;
}

int32_t bio_workflow_insert_size(bio_workflow_result_t* result,
                                 int64_t* histogram,
                                 int32_t capacity,
                                 int32_t* written) {
    if (written != nullptr) {
        *written = 0;
    }
    if (result == nullptr || histogram == nullptr || written == nullptr) {
        return BIO_ERR_ARGUMENT;
    }

    const std::vector<int64_t>& source = result->value.insert_size_histogram;
    const int32_t needed = static_cast<int32_t>(source.size());
    if (capacity < needed) {
        /* 与统计器那组取值函数一致：容量不够时回填**实际需要多少**。 */
        *written = needed;
        return BIO_ERR_OUTPUT;
    }
    for (int32_t index = 0; index < needed; ++index) {
        histogram[index] = source[static_cast<std::size_t>(index)];
    }
    *written = needed;
    return BIO_OK;
}

}  // extern "C"
