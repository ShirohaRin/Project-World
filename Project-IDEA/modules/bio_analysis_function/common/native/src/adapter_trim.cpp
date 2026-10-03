#include "adapter_trim.h"

#include <algorithm>
#include <cstddef>
#include <vector>

#include "sequence.h"

namespace bio {

namespace {

/* 上游常量。 */
constexpr int64_t kAllowOneMismatchForEach = 8;  /* 每 8 个碱基允许 1 个错配 */
constexpr int64_t kMinCompareLength = 2;         /* 比对长度比它还小就无法判定 */

}  // namespace

bool match_with_one_insertion(const char* inserted,
                              const char* normal,
                              int64_t compare_length,
                              int64_t diff_limit) {
    if (compare_length < kMinCompareLength) {
        return false;
    }

    /*
     * left[i]：前缀累计错配（inserted[0..i] vs normal[0..i]）
     * right[i]：后缀累计错配（inserted[i+1..] vs normal[i..]）
     *
     * 上游在前缀累计里会提前 break（省一点计算），代价是**后段变成未初始化内存**，
     * 而最后的判定循环在某些输入下会读到它们——那是未定义行为，本实现按它的意图
     * 把累计算完整（单调递增的真值），结果确定、可复现。
     */
    std::vector<int64_t> left(static_cast<std::size_t>(compare_length), 0);
    std::vector<int64_t> right(static_cast<std::size_t>(compare_length), 0);

    left[0] = inserted[0] == normal[0] ? 0 : 1;
    right[static_cast<std::size_t>(compare_length) - 1] =
        inserted[compare_length] == normal[compare_length - 1] ? 0 : 1;

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

    for (int64_t index = 1; index < compare_length; ++index) {
        if (left[static_cast<std::size_t>(index) - 1] +
                right[static_cast<std::size_t>(compare_length) - 1] > diff_limit) {
            return false;
        }
        if (left[static_cast<std::size_t>(index) - 1] +
                right[static_cast<std::size_t>(index)] <= diff_limit) {
            return true;
        }
    }
    return false;
}

bool find_adapter_position(std::string_view sequence,
                           std::string_view adapter,
                           int32_t match_required,
                           bool allow_one_gap,
                           int64_t& position) {
    const int64_t read_length = static_cast<int64_t>(sequence.size());
    const int64_t adapter_length = static_cast<int64_t>(adapter.size());
    const int64_t required = static_cast<int64_t>(match_required);
    const char* rdata = sequence.data();
    const char* adata = adapter.data();
    if (adapter_length < required) {
        return false;
    }

    /* 负起点：接头的开头被跳过也要认（上游注释：接头二聚体常少一个 A）。 */
    int64_t start = 0;
    if (adapter_length >= 16) {
        start = -4;
    } else if (adapter_length >= 12) {
        start = -3;
    } else if (adapter_length >= 8) {
        start = -2;
    }

    /* 1) 等长比对（Hamming 距离，每 8 个碱基允许 1 个错配） */
    for (int64_t pos = start; pos < read_length - required; ++pos) {
        const int64_t compare_length = std::min(read_length - pos, adapter_length);
        const int64_t allowed_mismatch = compare_length / kAllowOneMismatchForEach;
        const int64_t start_offset = std::max<int64_t>(0, -pos);
        const int64_t mismatches = count_mismatches_bounded(
            adata + start_offset,
            rdata + start_offset + pos,
            compare_length - start_offset,
            allowed_mismatch);
        if (mismatches <= allowed_mismatch) {
            position = pos;
            return true;
        }
    }

    if (!allow_one_gap) {
        return false;
    }

    /*
     * 2) 允许一个插入（read 比对接头多一个碱基）、3) 允许一个缺失。
     *
     * 注意上游的写法：这两段比对**锚定在 read 的开头**，随 pos 变的只有比对长度，
     * 指针并不做 pos 偏移。看起来像笔误，但"要不要裁、切在哪里"依赖它，因此照原样。
     */
    for (int64_t pos = 0; pos < read_length - required - 1; ++pos) {
        const int64_t compare_length = std::min(read_length - pos - 1, adapter_length);
        const int64_t allowed_mismatch =
            compare_length / kAllowOneMismatchForEach - 1;
        if (match_with_one_insertion(rdata, adata, compare_length, allowed_mismatch)) {
            position = pos;
            return true;
        }
    }

    for (int64_t pos = 0; pos < read_length - required; ++pos) {
        const int64_t compare_length = std::min(read_length - pos, adapter_length - 1);
        const int64_t allowed_mismatch =
            compare_length / kAllowOneMismatchForEach - 1;
        if (match_with_one_insertion(adata, rdata, compare_length, allowed_mismatch)) {
            position = pos;
            return true;
        }
    }

    return false;
}

int32_t match_required_for(std::size_t candidate_count) {
    if (candidate_count > 256) {
        return 6;
    }
    if (candidate_count > 16) {
        return 5;
    }
    return 4;
}

AdapterTrimOutcome apply_adapter_trim(FastqRecord& record,
                                      const AdapterTrimOptions& options) {
    AdapterTrimOutcome outcome;
    if (options.adapters.empty()) {
        return outcome;
    }

    const int32_t match_required = options.adapters.size() == 1
                                       ? options.match_required
                                       : match_required_for(options.adapters.size());

    /* 依次尝试：上一条裁完的结果作为下一条的输入（上游行为）。 */
    for (const std::string& adapter : options.adapters) {
        int64_t position = 0;
        if (!find_adapter_position(record.sequence, adapter, match_required,
                                   options.allow_one_gap, position)) {
            continue;
        }
        if (position < 0) {
            /* 整条 read 都是接头：清空（切掉的是接头的前 alen+pos 个碱基） */
            outcome.removed_bases +=
                static_cast<int64_t>(adapter.size()) + position;
            record.sequence.clear();
            record.quality.clear();
        } else {
            outcome.removed_bases +=
                static_cast<int64_t>(record.sequence.size()) - position;
            record.sequence.resize(static_cast<std::size_t>(position));
            record.quality.resize(static_cast<std::size_t>(position));
        }
        outcome.trimmed = true;
    }
    return outcome;
}

}  // namespace bio
