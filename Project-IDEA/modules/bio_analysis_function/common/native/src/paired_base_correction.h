/*
 * paired_base_correction.h —— 重叠区碱基校正（上游 BaseCorrector::correctByOverlapAnalysis）
 *
 * 本文件是 Python 版 ``submodules/paired_end_base_correction/algorithm.py`` 的逐位等价
 * 移植。改这里之前请先读那份 Python 实现与同目录的 ``paired_end_base_correction.md``。
 *
 * 与双端裁接头、合并共用 ``paired_pipeline.h`` 那条成对流水线。
 */

#ifndef BIO_PAIRED_BASE_CORRECTION_H
#define BIO_PAIRED_BASE_CORRECTION_H

#include <cstdint>
#include <string>

#include "fastq.h"
#include "overlap.h"

namespace bio {

struct PairedCorrectionOptions {
    /*
     * 默认走**无缺口**的 overlap 结论：上游在调用点写着 "no gap allowed for overlap
     * correction"——带缺口的重叠里"哪一位对应哪一位"本身就不可靠，遇到就跳过。
     * 只有显式打开 allow_gap 才可能拿到带缺口的重叠。
     */
    OverlapOptions overlap;
    int32_t threads = 0;
};

struct PairedCorrectionStats {
    int64_t total_pairs = 0;
    int64_t corrected_pairs = 0;
    int64_t corrected_reads = 0;
    int64_t corrected_bases = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
};

/*
 * 一对 read 的校正结果。
 *
 * ``read1`` / ``read2`` **总要填**：没改动的对也要原样写出（本算法不丢 read），
 * 而写出线程拿不到原始记录，所以"原样"这一份也随结果带出来。
 */
struct PairedCorrectionOutcome {
    int64_t input_bases = 0;
    int64_t corrected = 0;        /* 改掉的碱基数 */
    int64_t corrected_reads = 0;  /* 被改过的 read 条数（0 / 1 / 2） */
    FastqRecord read1;
    FastqRecord read2;
};

/*
 * 逐对判定：校正重叠区的错配碱基。**不继续处理、只给结论**，因此它既是文件级
 * 入口 ``correct_paired_fastq`` 的内核，也供工作流的计算端直接复用。
 *
 * ``overlap`` 由调用方算好传进来（理由同 ``trim_adapter_pair``：工作流一次
 * overlap 分析供四件事共用）。**带缺口的重叠会被跳过**——"哪一位对应哪一位"
 * 在缺口存在时不可靠，上游在调用点也是这么处理的。
 *
 * 两个质量门槛（可信 ≥ Q30、不可信 ≤ Q14）写死在实现里，不由参数传入——
 * 上游同样如此。
 */
PairedCorrectionOutcome correct_pair_bases(const FastqRecord& read1,
                                          const FastqRecord& read2,
                                          const OverlapOutcome& overlap);

/*
 * 成对读入两份 FASTQ，校正重叠区的错配碱基，**成对写出两份 FASTQ**。
 *
 * **不改长度、不丢 read**：所有记录都会原样或修正后写出。校正只在"一边可信（≥Q30）、
 * 另一边不可信（≤Q14）且两边碱基不互补"时才动手，并把可信一侧的质量值一并赋过去。
 * 失败时两个半成品输出都会被删除。
 */
PairedCorrectionStats correct_paired_fastq(
    const std::string& read1_path,
    const std::string& read2_path,
    const std::string& output1_path,
    const std::string& output2_path,
    bool compress,
    const PairedCorrectionOptions& options);

}  // namespace bio

#endif /* BIO_PAIRED_BASE_CORRECTION_H */
