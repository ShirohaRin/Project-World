/*
 * normalize.h —— reads 规范化（质量编码转换 + MGI 名字修复）
 *
 * 与 Python 侧 ``submodules/read_normalization/algorithm.py`` 逐行对应：
 *
 * 1. **质量编码**：Phred+64 → Phred+33，逐字符 ``max(33, q - 31)``
 *    （上游 ``Read::convertPhred64To33``）。那个 ``max`` 是必须的——
 *    字符低于 ``'@'`` 时减 31 会得到负值，上游把下界钉在 33。
 * 2. **MGI 名字**：``xxx/1`` → ``xxx /1``（上游 ``Read::fixMGI``）。
 *
 * **只动质量字符与名字**，不改碱基、不丢 read，因此没有"过滤"这回事。
 *
 * 为什么用串行实现：本算法没有重计算（质量转换是一次字节查表、名字修复是
 * 常数时间），瓶颈完全在 I/O。加 worker 只会增加搬运开销——与去重、统计
 * 同属"原生层串行"的那一类（理由见 common/native/native.md 的 5.4.0.9）。
 */

#ifndef BIO_NORMALIZE_H
#define BIO_NORMALIZE_H

#include <cstdint>
#include <string>

#include "fastq.h"

namespace bio {

/* 质量编码的两种偏移。 */
constexpr int32_t kPhredOffset33 = 33;
constexpr int32_t kPhredOffset64 = 64;

struct NormalizeOptions {
    /* 输入的质量编码偏移：只能是 33 或 64。 */
    int32_t input_phred = kPhredOffset33;
    /* 是否修 MGI 名字。 */
    bool fix_mgi = false;
};

struct NormalizeStats {
    int64_t total_reads = 0;         /* 单端按条、双端按条（一对算两条） */
    int64_t renamed_reads = 0;
    int64_t requantified_reads = 0;
    int64_t input_bases = 0;
    int64_t output_bases = 0;
};

/* 把一条记录规范化，就地改写；返回是否改过名字、是否重编码过质量。 */
void normalize_record(FastqRecord& record, const NormalizeOptions& options,
                      bool& renamed, bool& requantified);

/* 单端：``output_path`` 为空表示只统计、不写文件。 */
NormalizeStats normalize_single(const std::string& input_path,
                                const std::string& output_path,
                                bool compress,
                                const NormalizeOptions& options);

/* 双端：成对进、成对出；两份记录数必须一致。 */
NormalizeStats normalize_paired(const std::string& input1_path,
                                const std::string& output1_path,
                                const std::string& input2_path,
                                const std::string& output2_path,
                                bool compress,
                                const NormalizeOptions& options);

}  // namespace bio

#endif /* BIO_NORMALIZE_H */
