/*
 * umi_process.h —— UMI 处理（上游 UmiProcessor::process + Read 的三个方法）
 *
 * 本文件是 Python 版 ``submodules/umi_processing/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与同目录的 ``umi_processing.md``。
 *
 * 单端走 ``pipeline.h``（一次一条 read），双端走 ``paired_pipeline.h``（成对）。
 */

#ifndef BIO_UMI_PROCESS_H
#define BIO_UMI_PROCESS_H

#include <cstdint>
#include <string>

#include "fastq.h"

namespace bio {

/* UMI 来源，取值与上游 ``UMI_LOC_*``（``src/options.h``）一致。 */
enum UmiLocation {
    kUmiLocationIndex1 = 1,   /* R1 名字里的第一段 index */
    kUmiLocationIndex2 = 2,   /* R2 名字里的最后一段 index */
    kUmiLocationRead1 = 3,    /* R1 序列开头的 length 个碱基 */
    kUmiLocationRead2 = 4,    /* R2 序列开头的 length 个碱基 */
    kUmiLocationPerIndex = 5, /* 两段 index 拼起来 */
    kUmiLocationPerRead = 6,  /* 两条 read 的序列开头拼起来 */
};

struct UmiOptions {
    int32_t location = kUmiLocationRead1;
    int32_t length = 0;
    int32_t skip = 0;
    std::string prefix;
    std::string delimiter = ":";
    int32_t threads = 0;
};

struct UmiStats {
    int64_t total_reads = 0;      /* 按 **read 条数**计，双端时一对算两条 */
    int64_t reads_with_umi = 0;
    int64_t trimmed_bases = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
};

/*
 * 一条（或一对）read 的处理结果。
 *
 * ``read1`` / ``read2`` 总要填：UMI 取不到时也要原样写出（本算法不丢 read），
 * 而写出端拿不到原始记录，所以"原样"这一份也随结果带出来。
 */
struct UmiOutcome {
    FastqRecord read1;
    FastqRecord read2;
    bool has_read2 = false;
    int64_t input_bases = 0;
    bool tagged1 = false;
    bool tagged2 = false;
};

/*
 * 逐条（或逐对）处理：把 UMI 取出来挂到名字上。**不继续处理、只给结论**，
 * 因此它既是单端与双端两个文件级入口的内核，也供工作流的计算端直接复用。
 *
 * ``read2`` 传 nullptr 即单端。位置与长度等语义见 ``umi_processing.md``。
 */
UmiOutcome apply_umi_to_reads(const FastqRecord& read1,
                              const FastqRecord* read2,
                              const UmiOptions& options);

/* 单端：处理一份 FASTQ。 */
UmiStats process_umi_single(const std::string& input_path,
                            const std::string& output_path,
                            bool compress,
                            const UmiOptions& options);

/*
 * 双端：成对处理两份 FASTQ、分别写到两个输出。
 * 失败时两个半成品输出都会被删除。
 */
UmiStats process_umi_paired(const std::string& read1_path,
                            const std::string& read2_path,
                            const std::string& output1_path,
                            const std::string& output2_path,
                            bool compress,
                            const UmiOptions& options);

}  // namespace bio

#endif /* BIO_UMI_PROCESS_H */
