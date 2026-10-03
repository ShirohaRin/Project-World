#include "quality_trim.h"

#include <cstddef>

namespace bio {

namespace {

/* Phred+33 编码的偏移量：字符码 = Q + 33。 */
constexpr int kPhredOffset = 33;

constexpr char kNBase = 'N';

TrimOutcome dropped_outcome() {
    TrimOutcome outcome;
    outcome.dropped = true;
    return outcome;
}

/* 取字符码。用 unsigned char 转换，避免高位为 1 的字节被符号扩展成负数。 */
inline int64_t code_at(const std::string& text, int64_t index) {
    return static_cast<unsigned char>(text[static_cast<std::size_t>(index)]);
}

}  // namespace

std::pair<bool, int64_t> scan_forward(const std::string& quality,
                                      int64_t start,
                                      int64_t last_start,
                                      int64_t window,
                                      int64_t threshold,
                                      bool want_low) {
    // 先装入窗口的前 window-1 个碱基，循环里再补第 window 个，构成完整窗口。
    int64_t total = 0;
    for (int64_t offset = 0; offset < window - 1; ++offset) {
        total += code_at(quality, start + offset);
    }

    int64_t s = start;
    while (s <= last_start) {
        total += code_at(quality, s + window - 1);
        if (s > start) {
            total -= code_at(quality, s - 1);
        }
        const bool hit = want_low ? (total < threshold) : (total >= threshold);
        if (hit) {
            return {true, s};
        }
        ++s;
    }
    return {false, last_start + 1};
}

std::pair<bool, int64_t> scan_backward(const std::string& quality,
                                       int64_t first_end,
                                       int64_t last_end,
                                       int64_t window,
                                       int64_t threshold) {
    int64_t total = 0;
    for (int64_t offset = 0; offset < window - 1; ++offset) {
        total += code_at(quality, first_end - offset);
    }

    int64_t t = first_end;
    while (t >= last_end) {
        total += code_at(quality, t - window + 1);
        if (t < first_end) {
            total -= code_at(quality, t + 1);
        }
        if (total >= threshold) {
            return {true, t};
        }
        --t;
    }
    return {false, last_end - 1};
}

TrimOutcome compute_trim(const std::string& sequence,
                         const std::string& quality,
                         const QualityCutOptions& options) {
    const int64_t length = static_cast<int64_t>(sequence.size());
    int64_t front = options.trim_front;
    const int64_t tail = options.trim_tail;

    const bool has_quality_cut =
        options.enabled_front || options.enabled_right || options.enabled_tail;

    int64_t remaining = length - front - tail;
    if (remaining < 0) {
        return dropped_outcome();
    }

    // 没有质量剪切时只做固定位置修剪（front 与 tail 都为 0 则原样返回）。
    if (!has_quality_cut) {
        TrimOutcome outcome;
        outcome.begin = static_cast<std::size_t>(front);
        outcome.length = static_cast<std::size_t>(remaining);
        outcome.front_trimmed = static_cast<int32_t>(front);
        return outcome;
    }

    // ---- cut_front：从 5' 端找第一个达标窗口 ----
    if (options.enabled_front) {
        const int64_t window = options.window_size_front;
        if (length - front - tail - window <= 0) {
            return dropped_outcome();
        }
        const int64_t threshold = window * (kPhredOffset + options.quality_front);
        const auto [found, position] =
            scan_forward(quality, front, length - tail - window - 1, window, threshold, false);
        (void)found;  // cut_front 未命中时同样使用 position 作为退出位置
        int64_t cut_at = position;
        if (cut_at > 0) {
            // 把命中的那个"好窗口"也一起切掉：从窗口之后开始保留。
            cut_at = cut_at + window - 1;
        }
        while (cut_at < length && sequence[static_cast<std::size_t>(cut_at)] == kNBase) {
            ++cut_at;
        }
        front = cut_at;
        remaining = length - front - tail;
    }

    // ---- cut_right：从 5' 端找第一个不达标窗口，保留其内达标的单碱基前缀 ----
    if (options.enabled_right) {
        const int64_t window = options.window_size_right;
        if (length - front - tail - window <= 0) {
            return dropped_outcome();
        }
        const int64_t threshold = window * (kPhredOffset + options.quality_right);
        const auto [found, position] =
            scan_forward(quality, front, length - tail - window - 1, window, threshold, true);
        if (found) {
            int64_t cut_at = position;
            const int64_t minimum = kPhredOffset + options.quality_right;
            while (cut_at < length - 1 && code_at(quality, cut_at) >= minimum) {
                ++cut_at;
            }
            remaining = cut_at - front;
        }
        // 未命中说明整条 read 没有低质量窗口，remaining 保持不变。
    }

    // ---- cut_tail：从 3' 端找第一个达标窗口 ----
    // 上游明确写了 enabledRight 与 enabledTail 互斥：cut_right 更激进，同时开启时它说了算。
    if (!options.enabled_right && options.enabled_tail) {
        const int64_t window = options.window_size_tail;
        if (length - front - tail - window <= 0) {
            return dropped_outcome();
        }
        const int64_t threshold = window * (kPhredOffset + options.quality_tail);
        const auto [found, position] =
            scan_backward(quality, length - tail - 1, front + window, window, threshold);
        (void)found;
        int64_t keep_until = position;
        if (keep_until < length - 1) {
            // 回退到命中窗口的起始位置：这个窗口本身保留。
            keep_until = keep_until - window + 1;
        }
        while (keep_until >= 0 && sequence[static_cast<std::size_t>(keep_until)] == kNBase) {
            --keep_until;
        }
        remaining = keep_until - front + 1;
    }

    if (remaining <= 0 || front >= length - 1) {
        return dropped_outcome();
    }

    TrimOutcome outcome;
    outcome.begin = static_cast<std::size_t>(front);
    outcome.length = static_cast<std::size_t>(remaining);
    outcome.front_trimmed = static_cast<int32_t>(front);
    return outcome;
}

void apply_trim(FastqRecord& record, const TrimOutcome& outcome) {
    if (outcome.dropped) {
        record.clear();
        return;
    }
    if (outcome.begin == 0 && outcome.length == record.sequence.size()) {
        return;  // 无变化
    }
    record.sequence.erase(0, outcome.begin);
    record.sequence.resize(outcome.length);
    record.quality.erase(0, outcome.begin);
    record.quality.resize(outcome.length);
}

}  // namespace bio
