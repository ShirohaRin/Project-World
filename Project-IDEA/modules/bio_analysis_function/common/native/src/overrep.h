/*
 * overrep.h —— 过表达序列分析（fastp 的 over-representation analysis）
 *
 * 两步走，与上游的分工一致：
 *
 * 1. **找候选**（``Evaluator::computeOverRepSeq``）：只在文件开头一段数据上
 *    （默认 151 万碱基）把 10/20/40/100/150 bp 片段数一遍，按长度分档筛出高频项，
 *    再剔掉"是别人子串、且自己并不比别人多十倍"的冗余项；
 * 2. **量化**（``Stats::statRead`` + ``overRepPassed``）：在全文件上按采样
 *    （默认每 100 条取 1 条）数这些候选出现了多少次、落在 read 的哪些位置。
 *
 * 与 Python 侧 ``submodules/overrepresented_sequences/algorithm.py`` 逐行对应。
 *
 * ============================================================================
 * 内存开销要如实说明
 * ============================================================================
 *
 * 第一步要对**每一个窗口**建一个哈希项：1.51M 碱基、五档长度加起来约 430 万个
 * 窗口，其中绝大多数是唯一的，因此这一步的峰值内存可以到几百 MB。
 * 上游同样如此（这也是它默认关闭这个分析的原因）。它换来的是一次扫描就能
 * 发现任意长度的异常片段，而不必预先知道要找什么。
 */

#ifndef BIO_OVERREP_H
#define BIO_OVERREP_H

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace bio {

/* 候选扫描的碱基上限与读长取样条数。上游 ``Evaluator`` 里写死。 */
constexpr int64_t kOverrepCandidateBaseLimit = 151 * 10000;
constexpr int32_t kOverrepSeqLengthSample = 1000;

/* 采样率的默认值与合法范围。上游 ``--overrepresentation_sampling`` 默认 **20**。 */
constexpr int32_t kDefaultOverrepSampling = 20;
constexpr int32_t kMinOverrepSampling = 1;
constexpr int32_t kMaxOverrepSampling = 10000;

struct OverrepOptions {
    int32_t sampling = kDefaultOverrepSampling;
    int64_t base_limit = kOverrepCandidateBaseLimit;
    int32_t seq_length_sample = kOverrepSeqLengthSample;
};

struct OverrepEntry {
    std::string sequence;
    int64_t count = 0;            /* 采样计数 */
    int64_t estimated_count = 0;  /* 乘回采样率之后的估计总量 */
    double base_percent = 0.0;    /* 占全部碱基的百分比 */
    std::vector<int64_t> distribution; /* 每个位置的出现次数，长度 = seq_length */
};

struct OverrepResult {
    int64_t total_reads = 0;
    int64_t total_bases = 0;
    int64_t sampled_reads = 0;
    int32_t seq_length = 0;
    int32_t sampling = kDefaultOverrepSampling;
    std::vector<OverrepEntry> entries;
};

/* 候选扫描考察的片段长度；第五个是 ``min(150, 读长 - 2)``，可能与前面重复
 * （读长短时），上游不去重——同一个长度会被算两遍、计数翻倍。 */
std::vector<int32_t> overrep_steps(int32_t seq_length);

/* 候选入选判定：按长度**从上往下**分档，判据是 ``count >= 阈值``。 */
bool overrep_candidate_passes(int32_t length, int64_t count, int32_t seq_length);

/* 报告阈值：``sampling × count`` 要**严格大于**该长度的档位阈值
 * （长度不在表里时用 20）。注意它与候选那套**不是同一套**阈值。 */
bool overrep_report_passes(int32_t length, int64_t count, int32_t sampling);

/* 读一份 FASTQ 做完整分析（内部读两遍文件，见文件头说明）。 */
OverrepResult find_overrepresented_sequences(const std::string& input_path,
                                             const OverrepOptions& options);

}  // namespace bio

#endif /* BIO_OVERREP_H */
