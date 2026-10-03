/*
 * adapter_detect.h —— reads 接头检测（上游 fastp 的 Evaluator::evalAdapterAndReadNum）
 *
 * 本文件是 Python 版 ``common/adapter_detection/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与设计文档 `common/adapter_detection/adapter_detection.md`——上游若干处
 * "看起来可以简化"的写法是刻意保留的。
 *
 * 两段式：
 *   1. 查已知接头表（:func:`check_known_adapters`）：容错匹配，命中率高就返回表中序列；
 *   2. 从头检测（k-mer 富集挑种子 + 前缀树两侧延伸）。
 *
 * 已知接头表**不在这一层内置**：它由调用方（Python 侧的数据文件）传进来。
 * 这样表只有一份来源，也不必把 234 条序列在两个语言里各存一份。
 */

#ifndef BIO_ADAPTER_DETECT_H
#define BIO_ADAPTER_DETECT_H

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace bio {

/* 与 C ABI 的 bio_adapter_detect_options_t 一一对应。 */
struct AdapterDetectOptions {
    int64_t max_reads = 256 * 1024;         /* 采样条数上限 */
    int64_t max_bases = 151LL * 256 * 1024; /* 采样碱基数上限 */
    int32_t min_reads = 10000;              /* 少于它不做检测 */
    int32_t key_length = 10;                /* 种子 k-mer 长度（4~12） */
    int32_t shift_tail = 1;                 /* 扫描/拼接时忽略末尾几个碱基 */
    int32_t max_adapter_length = 60;        /* 返回序列的截断长度 */
};

/* 结果来源。 */
enum : int32_t {
    kAdapterSourceNone = 0,
    kAdapterSourceKnown = 1,
    kAdapterSourceKmer = 2,
};

/* 一次检测的结果（与 C ABI 的结果结构对应，只是这里是 C++ 类型）。 */
struct AdapterDetection {
    bool detected = false;
    std::string adapter;
    int32_t source = kAdapterSourceNone;
    std::string reason;          /* 未检测到时的人类可读原因 */
    std::string seed_sequence;   /* 从头检测时的种子 */
    int64_t seed_count = 0;
    double seed_fold = 0.0;      /* 富集倍数 */
    int64_t sampled_reads = 0;
    int64_t sampled_bases = 0;
};

/* ------------------------------------------------------------------------ */
/* 工具（供检测与后续算法复用）                                              */
/* ------------------------------------------------------------------------ */

/* 碱基 → 2 bit：A=0、T=1、C=2、G=3（与上游 int2seq 的 bases[4] 同序），其他为 -1。 */
int32_t base_code(char base);

/*
 * 取 ``[position, position + key_length)`` 的 2-bit 编码；含非 ACGT 返回 -1。
 *
 * ``last_key`` 是上一个窗口的编码：有效（≥0）时走滚动（只接上新进窗口的那个碱基），
 * 无效时退回完整重算——与上游 ``Evaluator::seq2int`` 的写法一致。
 */
int32_t key_at(std::string_view sequence,
               int64_t position,
               int32_t key_length,
               int32_t last_key);

/* 把键还原成碱基序列（上游 ``Evaluator::int2seq``）。 */
std::string sequence_of_key(int32_t key, int32_t key_length);

/*
 * 从 ``sequence`` 里认出已知接头：要求某条已知接头是它的**前缀**（一个错配都不允许）。
 * 认出来返回那条完整序列，否则返回空串。表需按字典序（上游用 std::map，遍历顺序即字典序）。
 */
std::string match_known_adapter(std::string_view sequence,
                                const std::vector<std::string>& adapters);

/* ------------------------------------------------------------------------ */
/* 采样与检测                                                                */
/* ------------------------------------------------------------------------ */

/*
 * 从 FASTQ 顺序采样 read 的**序列**（只要序列，不要质量与名字）。
 *
 * 与上游一致：读够 ``max_reads`` 条或 ``max_bases`` 个碱基就停（谁先到算谁），
 * 超过上限的那一条会被带上。检测要多次遍历样本，因此这里整体读进内存。
 */
std::vector<std::string> sample_sequences(const std::string& fastq_path,
                                          const AdapterDetectOptions& options);

/*
 * 第一段：在已知接头表里找命中率最高的一条。
 *
 * ``checked_reads`` / ``hits`` 回传"检查了多少条 read"与"命中多少次"，
 * 供上层给出证据（也用于 Python 侧的对拍）。表为空时直接返回空串且不检查任何 read。
 */
std::string check_known_adapters(const std::vector<std::string>& reads,
                                 const std::vector<std::string>& adapters,
                                 const AdapterDetectOptions& options,
                                 int64_t& checked_reads,
                                 int64_t& hits);

/* 第二段：k-mer 富集挑种子 + 前缀树延伸。表只用于最后的"吸附"（可为空）。 */
AdapterDetection detect_from_kmers(const std::vector<std::string>& reads,
                                   const std::vector<std::string>& adapters,
                                   const AdapterDetectOptions& options);

/* 检测入口：先第一段，命中就直接返回；否则走第二段。 */
AdapterDetection detect_adapter(const std::vector<std::string>& reads,
                                const std::vector<std::string>& adapters,
                                const AdapterDetectOptions& options);

}  // namespace bio

#endif /* BIO_ADAPTER_DETECT_H */
