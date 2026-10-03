#include "read_filter.h"

#include <cstddef>

namespace bio {

namespace {

/* Phred+33 的字符偏移：字符码 = Q + 33。 */
constexpr int32_t kPhredOffset = 33;

/* 万分之一 → 比例：3000 → 0.3。 */
constexpr double kBasisPointScale = 10000.0;

/* 百分比数值 → 比率：4000 万分之一 → 40.0（百分数）。 */
constexpr double kPercentScale = 100.0;

}  // namespace

FilterMetrics count_quality_metrics(std::string_view sequence,
                                    std::string_view quality,
                                    int32_t qualified_code) {
    FilterMetrics metrics;
    for (std::size_t index = 0; index < quality.size(); ++index) {
        // 与 Python 一样按无符号字节解释：质量字符的最高位可能为 1。
        const int32_t code = static_cast<unsigned char>(quality[index]);
        metrics.total_quality += code - kPhredOffset;
        if (code < qualified_code) {
            ++metrics.low_quality_bases;
        }
        if (sequence[index] == 'N') {
            ++metrics.n_bases;
        }
    }
    return metrics;
}

bool passes_low_complexity(std::string_view sequence, double threshold) {
    const auto length = static_cast<int64_t>(sequence.size());
    if (length <= 1) {
        return false;  // 分母为 0，上游明确返回 false
    }
    int64_t differences = 0;
    for (int64_t index = 0; index + 1 < length; ++index) {
        if (sequence[static_cast<std::size_t>(index)] !=
            sequence[static_cast<std::size_t>(index + 1)]) {
            ++differences;
        }
    }
    return static_cast<double>(differences) / static_cast<double>(length - 1) >= threshold;
}

int32_t filter_verdict(std::string_view sequence,
                       std::string_view quality,
                       const ReadFilterOptions& options) {
    const auto length = static_cast<int64_t>(sequence.size());

    // 1. 空 read：不受任何过滤开关影响（上游把它当作格式层面的异常）。
    if (length == 0) {
        return kFailLength;
    }

    // 2. 质量过滤：三项是 else if 关系，只报第一条命中的原因。
    if (options.enabled_quality) {
        const FilterMetrics metrics = count_quality_metrics(
            sequence, quality, options.qualified_quality_phred + kPhredOffset);

        // 浮点运算与 Python 同序（先乘后除），否则边界上的比较结果会漂。
        const double limit = options.unqualified_limit_bp / kPercentScale;
        if (static_cast<double>(metrics.low_quality_bases) >
            limit * static_cast<double>(length) / kPercentScale) {
            return kFailQuality;
        }
        // 整数除法：小数被丢掉，这是上游口径而不是笔误。
        if (options.average_qual > 0 &&
            metrics.total_quality / length < options.average_qual) {
            return kFailQuality;
        }
        if (metrics.n_bases > options.n_base_limit) {
            return kFailNBase;
        }
    }

    // 3. 长度过滤：先"过短"后"过长"，顺序本身有意义。
    if (options.enabled_length) {
        if (length < options.required_length) {
            return kFailLength;
        }
        if (options.max_length > 0 && length > options.max_length) {
            return kFailTooLong;
        }
    }

    // 4. 低复杂度过滤。
    if (options.enabled_complexity) {
        const double threshold = options.complexity_threshold_bp / kBasisPointScale;
        if (!passes_low_complexity(sequence, threshold)) {
            return kFailComplexity;
        }
    }

    return kPassFilter;
}

}  // namespace bio
