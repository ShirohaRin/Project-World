/*
 * insert_size.h —— 双端插入片段长度分布（fastp 的 insert size histogram）
 *
 * 逐对对两条 read 做 overlap 分析，把推算出的片段长度汇成直方图并找峰值。
 * 与 Python 侧 ``submodules/insert_size_distribution/algorithm.py`` 逐行对应。
 *
 * 片段的换算规则照抄上游 ``PairEndProcessor::statInsertSize``：
 * ``offset > 0``（片段长于读长）用 ``len1 + len2 - 重叠``；否则重叠长度本身
 * 就是片段长度；判不出时进溢出桶，而不是丢掉那一对。
 *
 * **本算法只读不写**：不产出任何文件，所以没有"失败删半成品"那套处理。
 */

#ifndef BIO_INSERT_SIZE_H
#define BIO_INSERT_SIZE_H

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "overlap.h"

namespace bio {

/* 直方图上限的默认值。上游 ``Options::insertSizeMax`` 同样是 512。 */
constexpr int32_t kDefaultInsertSizeMax = 512;

struct InsertSizeOptions {
    /* 直方图上限。下标 0~max_size-1 是真实片段长度，max_size 是**溢出桶**
     * （判不出的 + 超上限的）。上游用一个桶装两种情况，照抄。 */
    int32_t max_size = kDefaultInsertSizeMax;
    /* 重叠判定参数，与 ``bio_analyze_overlap`` 用的是同一套。 */
    OverlapOptions overlap;
};

struct InsertSizeStats {
    int64_t total_pairs = 0;
    int64_t overlapped_pairs = 0;
    int32_t peak_size = 0;
    /* 长度 = max_size + 1，最后一项是溢出桶。 */
    std::vector<int64_t> histogram;
};

/* 一对 read 的片段长度；判不出时返回 -1。 */
int32_t insert_size_from(const OverlapOutcome& outcome,
                         std::size_t read1_length,
                         std::size_t read2_length);

/*
 * 流式成对读完两份 FASTQ，统计片段长度分布。
 *
 * 两份输入的记录数必须一致，不一致抛 ``FastqFormatError``。
 */
InsertSizeStats analyze_insert_size(const std::string& read1_path,
                                    const std::string& read2_path,
                                    const InsertSizeOptions& options);

}  // namespace bio

#endif /* BIO_INSERT_SIZE_H */
