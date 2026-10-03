/*
 * read_stats.h —— reads 质量统计（fastp 的 Stats）
 *
 * ============================================================================
 * 它算什么
 * ============================================================================
 *
 * 逐碱基累加三类原始计数，最后整成曲线与比例：
 *
 * 1. **按位置**：每个 cycle 上各碱基的质量和 / 计数 / 占比（"质量从哪里开始塌"）；
 * 2. **全局**：Q20 / Q30 / Q40 碱基总数、GC 总数、读长分布；
 * 3. **序列内容**：1024 个 5-mer 桶的计数（内容异常的线索）。
 *
 * 本文件与 Python 侧 ``submodules/read_stats/algorithm.py`` 逐行对应，
 * 那份 Python 实现是**可审查的规格**，这份是生产实现。
 *
 * ============================================================================
 * 为什么累加与汇总要分开
 * ============================================================================
 *
 * ``add`` 是热路径（每条 read 一遍逐碱基循环），只做加法；
 * ``summarize`` 才把原始计数整成曲线，代价与数据量无关、与读长成正比。
 * 一批数据只需要汇总一次，但累加要做几千万次——混在一起就是白白多算。
 *
 * ============================================================================
 * 分桶用"碱基字符的低 3 位"
 * ============================================================================
 *
 * 与上游一致：``bucket = base & 0x07``。因为 ASCII 的大小写差 32（8 的倍数），
 * 这个分法**自动把大小写合并到同一桶**；A/C/T/G/N 恰好落在 1/3/4/7/6。
 * 照抄的理由不是省一次查表，而是**分桶结果会直接进入曲线数组**——
 * 换个分法曲线就对不上了。
 */

#ifndef BIO_READ_STATS_H
#define BIO_READ_STATS_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

namespace bio {

/* 碱基分桶数：按"碱基字符的低 3 位"分桶，与上游 Stats 一致。 */
constexpr std::size_t kStatBaseBuckets = 8;

/* 5-mer 的桶数：4^5 = 1024。上游按 2048 分配，但下标永远小于 1024。 */
constexpr std::size_t kStatKmerBuckets = 1 << 10;

/* 质量**字符码**的槽位数（Phred+33 最高到 '~' = 126，取 128 个槽）。 */
constexpr std::size_t kStatQualityCodes = 128;

/* Phred 上限（Phred+33 的字符上限是 '~' = 126）。 */
constexpr int kStatMaxPhred = 93;

/* Q20 / Q30 的判定阈值，以字符码表示。上游在 ``statRead`` 里写死成 '5' 与 '?'。 */
constexpr int kStatQ20Code = '5';  /* 53 */
constexpr int kStatQ30Code = '?';  /* 63 */

/* Q40 的 Phred 值。上游统计 Q40 总计时的循环是 ``for(c=40; c<94; c++)``。 */
constexpr int kStatQ40Phred = 40;

/* 单碱基 → 2 bit 编码：A=0、T=1、C=2、G=3，其余 -1。
 * 只认大写——上游 BASE2VAL 对 ``acgt`` 也返回 -1，因此小写碱基不计入 k-mer
 * （但会计入按位置的含量曲线）。这是上游的不一致，照抄。 */
int stat_base_code(unsigned char base);

/*
 * 曲线种类。顺序即 C ABI 里 ``bio_stat_curve_kind_t`` 的取值，不要改。
 */
enum class StatCurve {
    QualityMean = 0,
    QualityA = 1,
    QualityT = 2,
    QualityC = 3,
    QualityG = 4,
    QualityN = 5,
    ContentA = 6,
    ContentT = 7,
    ContentC = 8,
    ContentG = 9,
    ContentN = 10,
    ContentGC = 11,
};

constexpr int kStatCurveCount = 12;

/*
 * 统计累加器。
 *
 * 用法：反复 ``add`` 之后调一次 ``summarize``，然后读各访问器。
 * 一个实例就是**一份统计**——要统计另一份数据就新建一个。
 */
class ReadStats {
public:
    /* 累加一条 read。``sequence`` 与 ``quality`` 必须等长（调用方保证）。 */
    void add(std::string_view sequence, std::string_view quality);

    /* 把原始计数整成曲线与比例。幂等；未调用时访问器给不出正确结果。 */
    void summarize();

    bool summarized() const { return summarized_; }
    int64_t total_reads() const { return total_reads_; }
    int64_t total_bases() const { return total_bases_; }
    int64_t q20_bases() const { return q20_total_; }
    int64_t q30_bases() const { return q30_total_; }
    int64_t q40_bases() const { return q40_total_; }
    int64_t gc_bases() const { return gc_bases_; }
    /* 平均读长：整数除法（与上游 ``mLengthSum/mReads`` 一致）。 */
    int32_t mean_length() const;
    /* cycle 数 = 第一个"所有 read 都结束"的位置。 */
    int32_t cycles() const { return static_cast<int32_t>(cycles_); }
    /* 出现过的最大读长（读长分布数组的长度减一）。 */
    int32_t max_length() const { return static_cast<int32_t>(length_counts_.size()); }

    /* 下面几个都要求先 ``summarize``；返回的也是那条曲线的长度（= cycles）。 */
    const double* curve(StatCurve kind) const;
    const int64_t* quality_histogram() const { return quality_histogram_.data(); }
    const int64_t* kmer_counts() const { return kmer_counts_.data(); }
    /* 读长分布：下标即读长，长度 = max_length()。 */
    const int64_t* length_counts() const { return length_counts_.data(); }
    std::size_t length_count_size() const { return length_counts_.size(); }

private:
    void ensure_capacity(std::size_t length);

    /* ---- 累加区 ---- */
    std::size_t capacity_ = 0;
    std::array<std::vector<int64_t>, kStatBaseBuckets> q20_bases_;
    std::array<std::vector<int64_t>, kStatBaseBuckets> q30_bases_;
    std::array<std::vector<int64_t>, kStatBaseBuckets> base_contents_;
    std::array<std::vector<int64_t>, kStatBaseBuckets> base_quality_;
    std::vector<int64_t> total_base_;
    std::vector<int64_t> total_quality_;
    std::array<int64_t, kStatQualityCodes> quality_histogram_{};
    std::array<int64_t, kStatKmerBuckets> kmer_counts_{};
    std::vector<int64_t> length_counts_;

    int64_t total_reads_ = 0;
    int64_t length_sum_ = 0;

    /* ---- 汇总区（summarize 之后有效） ---- */
    bool summarized_ = false;
    std::size_t cycles_ = 0;
    int64_t total_bases_ = 0;
    int64_t q20_total_ = 0;
    int64_t q30_total_ = 0;
    int64_t q40_total_ = 0;
    int64_t gc_bases_ = 0;
    std::array<std::vector<double>, kStatCurveCount> curves_;
};

}  // namespace bio

#endif /* BIO_READ_STATS_H */
