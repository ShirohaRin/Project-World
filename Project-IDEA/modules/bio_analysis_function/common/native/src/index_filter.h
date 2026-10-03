/*
 * index_filter.h —— 按 index（barcode）黑名单过滤 reads
 *
 * 与 Python 侧 ``submodules/index_filtering/algorithm.py`` 逐行对应，
 * 判定照上游 ``Filter::filterByIndex`` 与 ``Filter::match``。
 *
 * 双端时两端的 index 来源不同：R1 用名字里的**第一段**（``firstIndex``）比
 * ``blacklist1``，R2 用**最后一段**（``lastIndex``）比 ``blacklist2``；
 * 任一端命中就丢整对（保持 R1/R2 对齐）。
 *
 * **判定的一个陷阱**（上游如此，照抄）：比较时只比两者中**较短**的长度。
 * 因此黑名单写 ``ACGT`` 而 read 的 index 是 ``AC`` 时算命中；更极端地，
 * 名字里取不到 index（空串）时与任何非空黑名单都算命中——那一批 read 会被全丢。
 *
 * 串行实现：判定是常数时间的字符串比较，瓶颈在 I/O（与去重、规范化同类）。
 */

#ifndef BIO_INDEX_FILTER_H
#define BIO_INDEX_FILTER_H

#include <cstdint>
#include <string>
#include <vector>

#include "fastq.h"

namespace bio {

struct IndexFilterOptions {
    /* 与 R1 第一段 index 比对的黑名单。 */
    std::vector<std::string> blacklist1;
    /* 与 R2 最后一段 index 比对的黑名单（单端时忽略）。 */
    std::vector<std::string> blacklist2;
    /* 允许的错配数。上游 ``--filter_by_index_threshold`` 默认 0。 */
    int32_t threshold = 0;
};

struct IndexFilterStats {
    int64_t total_reads = 0; /* 单端按 read 条数，双端按 read 对数 */
    int64_t filtered_reads = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
};

/* ``target`` 是否命中黑名单（上游 ``Filter::match``）。 */
bool matches_blacklist(const std::vector<std::string>& blacklist,
                       const std::string& target, int32_t threshold);

/* 这一条（或一对）该不该丢。``name2`` 为空指针表示单端。 */
bool is_index_filtered(const std::string& name1, const std::string* name2,
                       const IndexFilterOptions& options);

IndexFilterStats filter_by_index_single(const std::string& input_path,
                                        const std::string& output_path,
                                        bool compress,
                                        const IndexFilterOptions& options);

IndexFilterStats filter_by_index_paired(const std::string& input1_path,
                                        const std::string& output1_path,
                                        const std::string& input2_path,
                                        const std::string& output2_path,
                                        bool compress,
                                        const IndexFilterOptions& options);

}  // namespace bio

#endif /* BIO_INDEX_FILTER_H */
