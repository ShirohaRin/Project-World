#include "overlap.h"

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

#include "sequence.h"

namespace bio {

namespace {

/* 每个错位量都要求前这么多个碱基的错配不超限（上游写作 complete_compare_require）。 */
constexpr int64_t kProtectedPrefix = 50;

/* 缺口路径没找到插入位置时回传的"无穷大"（上游写作 100000000）。 */
constexpr int32_t kNoInsertionDiff = 100000000;

/*
 * 当前重叠长度下的错配上限。
 *
 * 上游用 C 的 ``int()`` 截断，这里同（``static_cast`` 也是向零截断）。
 * ``diff_percent_limit`` 由 C ABI 以万分之一整数换算而来，换算写成
 * ``bp / 10000.0`` 后与 Python 侧的浮点字面量是同一个 double（除法是正确舍入的），
 * 因此两侧的截断结果逐位相同。
 */
int32_t limit_for(int64_t overlap_len, const OverlapOptions& options) {
    const int64_t by_percent =
        static_cast<int64_t>(overlap_len * options.diff_percent_limit);
    return static_cast<int32_t>(std::min<int64_t>(options.diff_limit, by_percent));
}

/*
 * 判定一个错位量是否成立；成立时把错配数写进 ``diff``，返回 true。
 *
 * 上游在这里只拿**前 50 个碱基**做判定（超限就否掉），但比对长度超过 50 时
 * 报出去的 ``diff`` 是**整段**的真实错配数。这个不对称是刻意的，也是它能把
 * "前面完全对上、后面才开始错"（接头读通的样子）认出来的原因。
 */
bool accept_no_gap(const char* left,
                   const char* right,
                   int64_t length,
                   int64_t limit,
                   int32_t& diff) {
    const int64_t protected_prefix = std::min<int64_t>(length, kProtectedPrefix);
    int64_t mismatches = count_mismatches_bounded(left, right, protected_prefix, limit);
    if (mismatches > limit) {
        diff = static_cast<int32_t>(mismatches);
        return false;
    }
    if (length > kProtectedPrefix) {
        mismatches = count_mismatches(left, right, length);
    }
    diff = static_cast<int32_t>(mismatches);
    return true;
}

}  // namespace

int32_t diff_with_one_insertion(const char* inserted,
                                const char* normal,
                                int64_t compare_length,
                                int32_t diff_limit) {
    /*
     * 上游没有这个保护：compare_length 为 0 时它会分配零长数组并越界访问。
     * Python 版与这里都判 -1（"这个位置不可能对得上"）。
     */
    if (compare_length < 1) {
        return -1;
    }

    /*
     * left[i]：前缀累计错配（inserted[0..i] vs normal[0..i]）
     * right[i]：后缀累计错配（inserted[i+1..] vs normal[i..]）
     *
     * 本函数在缺口扫描里会被调用很多次，两条数组用 thread_local 复用，
     * 免得逐次分配。
     */
    thread_local std::vector<int32_t> left;
    thread_local std::vector<int32_t> right;
    const std::size_t size = static_cast<std::size_t>(compare_length);
    left.assign(size, 0);
    right.assign(size, 0);

    left[0] = inserted[0] == normal[0] ? 0 : 1;
    right[size - 1] = inserted[compare_length] == normal[compare_length - 1] ? 0 : 1;

    for (int64_t index = 1; index < compare_length; ++index) {
        left[static_cast<std::size_t>(index)] =
            left[static_cast<std::size_t>(index) - 1] +
            (inserted[index] == normal[index] ? 0 : 1);
    }

    for (int64_t index = compare_length - 2; index >= 0; --index) {
        right[static_cast<std::size_t>(index)] =
            right[static_cast<std::size_t>(index) + 1] +
            (inserted[index + 1] == normal[index] ? 0 : 1);
        if (right[static_cast<std::size_t>(index)] + left[0] > diff_limit) {
            /* 更靠前的槽位必然也超预算：填成"大于容错"（上游同样这么填）。 */
            for (int64_t earlier = 0; earlier < index; ++earlier) {
                right[static_cast<std::size_t>(earlier)] = diff_limit + 1;
            }
            break;
        }
    }

    int32_t min_diff = kNoInsertionDiff;
    for (int64_t index = 1; index < compare_length; ++index) {
        if (left[static_cast<std::size_t>(index) - 1] +
                right[static_cast<std::size_t>(compare_length) - 1] > diff_limit) {
            return -1;
        }
        const int32_t diff = left[static_cast<std::size_t>(index) - 1] +
                             right[static_cast<std::size_t>(index)];
        if (diff <= min_diff) {
            min_diff = diff;
        }
    }
    return min_diff;
}

OverlapOutcome analyze_overlap(std::string_view read1,
                               std::string_view read2,
                               const OverlapOptions& options) {
    /*
     * rc2 放复用缓冲区：一次分析要扫几十上百个错位量，全都指向它；
     * thread_local 也保证了本函数可被多个工作线程并发调用。
     */
    thread_local std::string reverse2;
    reverse2.resize(read2.size());
    if (!read2.empty()) {
        reverse_complement(read2.data(), &reverse2[0],
                           static_cast<int64_t>(read2.size()));
    }

    const char* str1 = read1.data();
    const char* str2 = reverse2.data();
    const int64_t length1 = static_cast<int64_t>(read1.size());
    const int64_t length2 = static_cast<int64_t>(reverse2.size());
    const int64_t require = options.require;

    int32_t diff = 0;

    // 1) 正向、无缺口：r1 的尾巴对 rc2 的头
    for (int64_t offset = 0; offset < length1 - require; ++offset) {
        const int64_t overlap_len = std::min(length1 - offset, length2);
        if (accept_no_gap(str1 + offset, str2, overlap_len,
                          limit_for(overlap_len, options), diff)) {
            return OverlapOutcome{true, static_cast<int32_t>(offset),
                                  static_cast<int32_t>(overlap_len), diff, false};
        }
    }

    // 2) 反向、无缺口：r1 的头对 rc2 的中间（接头出现在 rc2 的尾部）
    for (int64_t offset = 0; offset > -(length2 - require); --offset) {
        const int64_t overlap_len = std::min(length1, length2 + offset);
        if (accept_no_gap(str1, str2 - offset, overlap_len,
                          limit_for(overlap_len, options), diff)) {
            return OverlapOutcome{true, static_cast<int32_t>(offset),
                                  static_cast<int32_t>(overlap_len), diff, false};
        }
    }

    if (!options.allow_gap) {
        return OverlapOutcome{};
    }

    const auto gap_outcome = [](int64_t offset, int64_t overlap_len, int32_t gap_diff) {
        return OverlapOutcome{true, static_cast<int32_t>(offset),
                              static_cast<int32_t>(overlap_len), gap_diff, true};
    };

    // 3) 正向、允许 1 个插入/缺失
    for (int64_t offset = 0; offset < length1 - require; ++offset) {
        const int64_t overlap_len = std::min(length1 - offset, length2);
        const int32_t limit = limit_for(overlap_len, options);
        const int64_t compare_length = overlap_len - 1;
        int32_t gap_diff =
            diff_with_one_insertion(str1 + offset, str2, compare_length, limit);
        if (gap_diff < 0 || gap_diff > limit) {
            gap_diff = diff_with_one_insertion(str2, str1 + offset, compare_length, limit);
        }
        if (gap_diff >= 0 && gap_diff <= limit) {
            return gap_outcome(offset, overlap_len, gap_diff);
        }
    }

    // 4) 反向、允许 1 个插入/缺失
    for (int64_t offset = 0; offset > -(length2 - require); --offset) {
        const int64_t overlap_len = std::min(length1, length2 + offset);
        const int32_t limit = limit_for(overlap_len, options);
        const int64_t compare_length = overlap_len - 1;
        const char* shifted = str2 - offset;
        int32_t gap_diff = diff_with_one_insertion(str1, shifted, compare_length, limit);
        if (gap_diff < 0 || gap_diff > limit) {
            gap_diff = diff_with_one_insertion(shifted, str1, compare_length, limit);
        }
        if (gap_diff >= 0 && gap_diff <= limit) {
            return gap_outcome(offset, overlap_len, gap_diff);
        }
    }

    return OverlapOutcome{};
}

}  // namespace bio
