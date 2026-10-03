/*
 * quality_trim.h —— 滑窗质量切剪（fastp 的 cut_front / cut_right / cut_tail）
 *
 * 本文件是 Python 版 ``submodules/quality_trimming/algorithm.py`` 的逐位等价移植，
 * 包括所有边界行为。改这里之前请先读那份 Python 实现与它自己的设计文档
 * （`submodules/quality_trimming/quality_trimming.md`）：
 * 上游若干处"看起来奇怪"的逻辑是刻意保留的，不是可以顺手简化的地方。
 *
 * 算法核心被拆成"只计算保留区间"与"应用区间"两步：
 *
 * - :func:`compute_trim` 是纯函数，不修改输入，便于单测与复用；
 * - :func:`apply_trim` 负责就地改写记录。
 *
 * 这样安排是因为剪切逻辑的边界极多，能单独测区间就单独测。
 */

#ifndef BIO_QUALITY_TRIM_H
#define BIO_QUALITY_TRIM_H

#include <cstdint>
#include <utility>

#include "fastq.h"

namespace bio {

/* 与 C ABI 的 bio_quality_trim_options_t 一一对应，去掉线程与压缩两项。 */
struct QualityCutOptions {
    bool enabled_front = false;
    bool enabled_right = false;
    bool enabled_tail = false;
    int32_t window_size_front = 4;
    int32_t window_size_right = 4;
    int32_t window_size_tail = 4;
    int32_t quality_front = 20;
    int32_t quality_right = 20;
    int32_t quality_tail = 20;
    int32_t trim_front = 0;
    int32_t trim_tail = 0;
};

/* 剪切结果：保留 ``[begin, begin + length)`` 这一段；``dropped`` 为真时该 read 应被丢弃。 */
struct TrimOutcome {
    bool dropped = false;
    std::size_t begin = 0;
    std::size_t length = 0;
    int32_t front_trimmed = 0;
};

/*
 * 计算应当保留的区间，不修改任何输入。
 *
 * sequence 与 quality 必须等长（调用方保证；这是 FASTQ 格式的硬性要求）。
 * 返回值的 ``dropped`` 为真表示上游会返回空指针，即这条 read 不应继续存在。
 */
TrimOutcome compute_trim(const std::string& sequence,
                         const std::string& quality,
                         const QualityCutOptions& options);

/* 按结果就地改写记录；``dropped`` 为真时把记录清空（调用方负责不写出它）。 */
void apply_trim(FastqRecord& record, const TrimOutcome& outcome);

/*
 * 正向滚动扫描，窗口用**起点** s 表示，覆盖 ``[s, s + window - 1]``。
 *
 * ``start`` 到 ``last_start``（含）是允许的窗口起点范围。
 * **未命中时返回 ``last_start + 1``**——这是上游 for 循环的自然退出值，
 * 后续判断依赖它。``want_low`` 为真时找第一个"和低于阈值"的窗口（cut_right 用），
 * 否则找第一个"和达到阈值"的窗口（cut_front 用）。
 *
 * 前置条件：``start + window <= quality.size()``。
 */
std::pair<bool, int64_t> scan_forward(const std::string& quality,
                                      int64_t start,
                                      int64_t last_start,
                                      int64_t window,
                                      int64_t threshold,
                                      bool want_low);

/*
 * 反向滚动扫描，窗口用**终点** t 表示，覆盖 ``[t - window + 1, t]``。
 *
 * **未命中时返回 ``last_end - 1``**，同样是上游循环的自然退出值。
 * 前置条件：``first_end >= window - 1`` 且 ``last_end >= window - 1``。
 */
std::pair<bool, int64_t> scan_backward(const std::string& quality,
                                       int64_t first_end,
                                       int64_t last_end,
                                       int64_t window,
                                       int64_t threshold);

}  // namespace bio

#endif /* BIO_QUALITY_TRIM_H */
