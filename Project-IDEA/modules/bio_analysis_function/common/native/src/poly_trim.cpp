#include "poly_trim.h"

#include <algorithm>
#include <cstddef>

namespace bio {

namespace {

/* 每多少个碱基允许 1 个"非目标碱基"。 */
constexpr int64_t kAllowOneMismatchForEach = 8;

/* polyG 的硬上限：错配超过它立刻停止扫描。 */
constexpr int64_t kMaxMismatch = 5;

constexpr char kGuanine = 'G';

/* 下标顺序与上游 ATCG_BASES 一致：A=0、T=1、C=2、G=3。 */
constexpr char kBases[4] = {'A', 'T', 'C', 'G'};

/*
 * 碱基 → 下标。返回 4 表示 N，5 表示非碱基。
 *
 * 用查表而不是比较链：这个函数在每条 read 的每个碱基上都会被调用一次，
 * 是内循环里最热的地方。
 */
int base_index(char base) {
    switch (base) {
        case 'A': return 0;
        case 'T': return 1;
        case 'C': return 2;
        case 'G': return 3;
        case 'N': return 4;
        default: return 5;
    }
}

inline char base_at(std::string_view sequence, int64_t index) {
    return sequence[static_cast<std::size_t>(index)];
}

}  // namespace

PolyTrimOutcome trim_poly_g(std::string_view sequence, int32_t min_length) {
    const int64_t length = static_cast<int64_t>(sequence.size());
    PolyTrimOutcome outcome;
    outcome.kept_length = sequence.size();
    if (length == 0) {
        return outcome;
    }

    int64_t mismatch = 0;
    int64_t scanned = 0;
    int64_t first_g_position = length - 1;

    while (scanned < length) {
        if (base_at(sequence, length - scanned - 1) != kGuanine) {
            ++mismatch;
        } else {
            first_g_position = length - scanned - 1;
        }

        const int64_t allowed = (scanned + 1) / kAllowOneMismatchForEach;
        if (mismatch > kMaxMismatch ||
            (mismatch > allowed && scanned >= static_cast<int64_t>(min_length) - 1)) {
            break;
        }
        ++scanned;
    }

    if (scanned >= static_cast<int64_t>(min_length)) {
        outcome.kept_length = static_cast<std::size_t>(first_g_position);
        outcome.trimmed_bases = length - first_g_position;
        outcome.poly_base = kGuanine;
    }
    return outcome;
}

PolyTrimOutcome trim_poly_x(std::string_view sequence, int32_t min_length) {
    const int64_t length = static_cast<int64_t>(sequence.size());
    PolyTrimOutcome outcome;
    outcome.kept_length = sequence.size();
    if (length == 0) {
        return outcome;
    }

    int64_t counts[4] = {0, 0, 0, 0};
    int64_t scanned = 0;

    while (scanned < length) {
        const int index = base_index(base_at(sequence, length - scanned - 1));
        if (index < 4) {
            ++counts[index];
        } else if (index == 4) {
            // N 无法判定是哪种碱基，让它对四个桶各投一票、相互抵消。
            for (int base = 0; base < 4; ++base) {
                ++counts[base];
            }
        }

        const int64_t length_scanned = scanned + 1;
        const int64_t allowed =
            std::min(kMaxMismatch, length_scanned / kAllowOneMismatchForEach);

        // 四种碱基全部不成立才会停：只要有一种的"非它数量"仍在允许范围内，
        // 就说明它仍可能是这条尾巴的碱基。
        bool all_bases_fail = true;
        for (int base = 0; base < 4; ++base) {
            if (length_scanned - counts[base] <= allowed) {
                all_bases_fail = false;
                break;
            }
        }

        if (all_bases_fail &&
            (scanned >= kAllowOneMismatchForEach ||
             length_scanned >= static_cast<int64_t>(min_length) - 1)) {
            break;
        }
        ++scanned;
    }

    if (scanned + 1 < static_cast<int64_t>(min_length)) {
        return outcome;
    }

    // 计数最多者胜出；用严格大于，保证并列时取下标更小的碱基。
    int poly_index = 0;
    int64_t max_count = -1;
    for (int base = 0; base < 4; ++base) {
        if (counts[base] > max_count) {
            max_count = counts[base];
            poly_index = base;
        }
    }

    const char poly_base = kBases[poly_index];
    int64_t cut_position = length - scanned - 1;
    if (cut_position < 0) {
        // 扫描覆盖了整条 read（循环自然结束），说明整条都是一个 poly 尾巴。
        // 上游此处会访问 data[-1]（c_str() 之前的内存），属未定义行为；
        // 本实现明确取 0，即整条 read 都被切掉——这是与上游唯一的有意差异。
        cut_position = 0;
    }
    // 扫描可能停在尾巴中间的杂质上，向右找到该 poly 碱基真正开始的位置。
    while (base_at(sequence, cut_position) != poly_base) {
        ++cut_position;
    }

    outcome.kept_length = static_cast<std::size_t>(cut_position);
    outcome.trimmed_bases = length - cut_position;
    outcome.poly_base = poly_base;
    return outcome;
}

PolyTrimOutcome compute_poly_trim(std::string_view sequence, const PolyTrimOptions& options) {
    PolyTrimOutcome total;
    total.kept_length = sequence.size();

    // 用视图而非拷贝逐步收缩：两步之间不需要真正的字符串。
    std::string_view current = sequence;

    if (options.enabled_poly_g) {
        const PolyTrimOutcome step = trim_poly_g(current, options.min_length_poly_g);
        if (step.trimmed_bases > 0) {
            current = current.substr(0, step.kept_length);
            total.trimmed_bases += step.trimmed_bases;
            total.poly_base = step.poly_base;
        }
    }

    if (options.enabled_poly_x) {
        const PolyTrimOutcome step = trim_poly_x(current, options.min_length_poly_x);
        if (step.trimmed_bases > 0) {
            current = current.substr(0, step.kept_length);
            total.trimmed_bases += step.trimmed_bases;
            total.poly_base = step.poly_base;
        }
    }

    total.kept_length = current.size();
    return total;
}

void apply_poly_trim(FastqRecord& record, const PolyTrimOutcome& outcome) {
    if (outcome.trimmed_bases == 0) {
        return;
    }
    record.sequence.resize(outcome.kept_length);
    record.quality.resize(outcome.kept_length);
}

}  // namespace bio
