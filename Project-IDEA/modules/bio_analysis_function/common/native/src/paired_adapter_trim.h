/*
 * paired_adapter_trim.h —— 双端按 overlap 裁接头（上游 AdapterTrimmer::trimByOverlapAnalysis）
 *
 * 本文件是 Python 版 ``submodules/paired_end_adapter_trimming/algorithm.py`` 的
 * 逐位等价移植。改这里之前请先读那份 Python 实现与同目录的
 * ``paired_end_adapter_trimming.md``。
 *
 * 与"合并"共用 ``paired_pipeline.h`` 那条双端流水线——**它是这条线上的第二个使用方**，
 * 也是当初把成对流水线从算法内部提到公共层的理由。
 */

#ifndef BIO_PAIRED_ADAPTER_TRIM_H
#define BIO_PAIRED_ADAPTER_TRIM_H

#include <cstdint>
#include <string>

#include "fastq.h"
#include "overlap.h"

namespace bio {

struct PairedAdapterTrimOptions {
    OverlapOptions overlap;
    /* R1 / R2 头部**已经被剪掉**的碱基数（几何补偿量，没剪过就是 0）。 */
    int32_t front_trimmed1 = 0;
    int32_t front_trimmed2 = 0;
    int32_t threads = 0;
};

struct PairedAdapterTrimStats {
    int64_t total_pairs = 0;
    int64_t trimmed_pairs = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
    int64_t trimmed_bases = 0;
};

/*
 * 一对 read 的裁剪结果。
 *
 * ``read1`` / ``read2`` **总要填**：没裁到的对也要原样写出（本算法只裁剪、
 * 不丢弃 read），而写出线程拿不到原始记录，所以"原样"这一份也随结果带出来。
 */
struct PairedAdapterTrimOutcome {
    int64_t input_bases = 0;
    bool trimmed = false;
    int64_t trimmed_bases = 0;
    FastqRecord read1;
    FastqRecord read2;
};

/*
 * 逐对判定：按 overlap 裁掉接头。**不继续处理、只给结论**，因此它既是文件级
 * 入口 ``trim_paired_adapter_fastq`` 的内核，也供工作流的计算端直接复用。
 *
 * ``overlap`` 由调用方算好传进来，而不是在这里现算：工作流一个 read 对要同时
 * 用到裁接头、碱基校正、插入片段与合并，四件事共用同一次 overlap 分析
 * （上游在 ``processPairEnd`` 里也是这么缓存的）。单独调用本算法时，
 * 文件级入口会自己算一次。
 *
 * 几何补偿量（``front_trimmed1/2``）由调用方给：前面几步剪掉了多少头部碱基，
 * 直接决定这里保留多长。详见 ``paired_end_adapter_trimming.md``。
 */
PairedAdapterTrimOutcome trim_adapter_pair(const FastqRecord& read1,
                                           const FastqRecord& read2,
                                           const OverlapOutcome& overlap,
                                           const PairedAdapterTrimOptions& options);

/*
 * 成对读入两份 FASTQ，按 overlap 裁掉接头，**成对写出两份 FASTQ**。
 *
 * 没检出可裁接头的 read 对**原样写出**（本算法只裁剪、不丢弃 read）。
 * 失败时**两个半成品输出都会被删除**——成对的产物不能只留一半。
 */
PairedAdapterTrimStats trim_paired_adapter_fastq(
    const std::string& read1_path,
    const std::string& read2_path,
    const std::string& output1_path,
    const std::string& output2_path,
    bool compress,
    const PairedAdapterTrimOptions& options);

}  // namespace bio

#endif /* BIO_PAIRED_ADAPTER_TRIM_H */
