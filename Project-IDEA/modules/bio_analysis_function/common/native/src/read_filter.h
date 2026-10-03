/*
 * read_filter.h —— reads 过滤（fastp 的 Filter::passFilter）
 *
 * 本文件是 Python 版 ``submodules/read_filtering/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与设计文档 `submodules/read_filtering/read_filtering.md`——上游若干处
 * "看起来可以简化"的写法是刻意保留的。
 *
 * 三处口径必须与 Python 版逐位一致，否则边界上的 read 会成批地换个去留：
 *
 * 1. **判定顺序**：零长度 → 质量（低质量比例 / 平均质量 / N 上限，三者短路）
 *    → 长度（先"过短"后"过长"）→ 低复杂度。顺序决定报出的是哪个原因。
 * 2. **浮点比较的顺序**：低质量比例是 ``limit * length / 100.0``，
 *    乘法再除法，与 Python 同序，才能保证浮点结果逐位相同。
 * 3. **整数除法**：平均质量那一处是 ``total_quality / length``（整数相除，
 *    小数被丢掉）。这不是笔误，是上游口径。
 */

#ifndef BIO_READ_FILTER_H
#define BIO_READ_FILTER_H

#include <cstdint>
#include <string_view>

namespace bio {

/* 结果码：数值与上游 fastp ``src/common.h`` 一致（那里刻意留出间隔以便扩展）。 */
enum : int32_t {
    kPassFilter = 0,
    kFailNBase = 12,
    kFailLength = 16,
    kFailTooLong = 17,
    kFailQuality = 20,
    kFailComplexity = 24,
    /*
     * "接头二聚体"不是一个判据，而是**接头裁剪之后**才能得出的结论：两条 read
     * 都有接头证据、且裁完都短到只剩接头本身。上游由 ``PairEndProcessor`` 在
     * overlap 段之后直接覆盖过滤结果，所以它不参与 ``filter_verdict`` 的判定，
     * 数值放在这里只是为了让标记与报告口径统一（``pipeline.h`` 的
     * ``verdict_label`` 已经认这个值）。
     */
    kFailAdapterDimer = 28,
};

/*
 * 与 C ABI 的 ``bio_read_filter_options_t`` 一一对应（去掉线程与压缩）。
 *
 * 两个"比例"类参数用**万分之一**（basis point，1 = 0.01%）表示，而不是浮点：
 * C ABI 里出现浮点字段时，结构体布局依赖编译器的对齐规则，整数则没有这个隐患；
 * 同时 4 位小数足以无损表示实际会设的阈值（0.3 → 3000）。
 * 算法内部再还原成与 Python 版完全相同的 double（3000 / 10000.0），
 * 因此比较结果逐位一致。
 */
struct ReadFilterOptions {
    bool enabled_quality = true;
    int32_t qualified_quality_phred = 15;   /* 0~93 */
    int32_t unqualified_limit_bp = 4000;    /* 万分之一，4000 = 40% */
    int32_t n_base_limit = 5;
    int32_t average_qual = 0;               /* 0 = 不限 */

    bool enabled_length = true;
    int32_t required_length = 15;
    int32_t max_length = 0;                 /* 0 = 不限 */

    bool enabled_complexity = false;
    int32_t complexity_threshold_bp = 3000; /* 万分之一，3000 = 30% */
};

/* 一条 read 的质量统计（一次遍历同时算出三个量）。 */
struct FilterMetrics {
    int64_t low_quality_bases = 0;
    int64_t n_bases = 0;
    int64_t total_quality = 0;
};

/*
 * 一次遍历统计低质量碱基数、N 个数与质量总和。
 *
 * ``qualified_code`` 是达标质量的**字符码**（Phred + 33），与上游
 * ``fastp_simd::countQualityMetrics`` 的语义一致：低质量判据是严格小于，
 * 质量累计的是 ``code - 33``。
 *
 * 前置条件：``sequence`` 与 ``quality`` 等长（由 FASTQ 读取器保证，不在这里重复检查）。
 */
FilterMetrics count_quality_metrics(std::string_view sequence,
                                    std::string_view quality,
                                    int32_t qualified_code);

/*
 * 低复杂度判据：相邻碱基不同的比例是否达到阈值。
 *
 * 复杂度 $C = \#\{i : s_i \ne s_{i+1}\} / (n-1)$，判据是 $C \ge$ threshold。
 * ``n <= 1`` 时分母为 0，上游明确返回 false（判为不通过）。
 */
bool passes_low_complexity(std::string_view sequence, double threshold);

/*
 * 判定一条 read 的去留，返回 :data:`kPassFilter` 或某个 ``kFail*``。
 *
 * 判定顺序与边界行为见文件头的说明；参数越界由调用方（C ABI 层）先挡住。
 */
int32_t filter_verdict(std::string_view sequence,
                       std::string_view quality,
                       const ReadFilterOptions& options);

}  // namespace bio

#endif /* BIO_READ_FILTER_H */
