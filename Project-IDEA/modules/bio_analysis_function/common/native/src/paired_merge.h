#ifndef BIO_PAIRED_MERGE_H
#define BIO_PAIRED_MERGE_H

#include <cstdint>
#include <string>

#include "bio_native.h"
#include "fastq.h"
#include "overlap.h"

namespace bio {

struct PairedMergeOptions {
    OverlapOptions overlap;
    int32_t threads = 0;
};

struct PairedMergeStats {
    int64_t total_pairs = 0;
    int64_t merged_pairs = 0;
    int64_t unmerged_pairs = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
    int64_t gap_overlaps = 0;
};

/*
 * 一对 read 的合并结果。
 *
 * ``input_bases`` 随结果一起带出来：统计必须在**写出线程**里按输入顺序累加
 * （见 paired_pipeline.h），而写出线程只拿得到这个结构，拿不到原始 read。
 */
struct PairedMergeOutcome {
    bool merged = false;
    bool gap = false;
    int64_t input_bases = 0;
    FastqRecord record;  /* 仅在 merged 为真时有效 */
};

/*
 * 逐对判定：两条 read 重叠就合并成一条。**不继续处理、只给结论**，因此它既是
 * 文件级入口 ``merge_paired_fastq`` 的内核，也供工作流的计算端直接复用。
 *
 * ``overlap`` 由调用方算好传进来。工作流在合并前**会重算一次**——读到这一步
 * read 已经被前面的修剪改过了（上游为 issue #675 做的修正，见
 * ``PairEndProcessor::processPairEnd``）。
 *
 * 合并结果的名字由输入名字加 `` merged_<左段长>_<右段长>`` 构成（上游格式）。
 */
PairedMergeOutcome merge_read_pair(const FastqRecord& read1,
                                   const FastqRecord& read2,
                                   const OverlapOutcome& overlap);

PairedMergeStats merge_paired_fastq(const std::string& read1_path,
                                    const std::string& read2_path,
                                    const std::string& output_path,
                                    bool compress,
                                    const PairedMergeOptions& options);

}  // namespace bio

#endif /* BIO_PAIRED_MERGE_H */
