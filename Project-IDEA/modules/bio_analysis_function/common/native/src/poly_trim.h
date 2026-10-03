/*
 * poly_trim.h —— reads 尾部 polyG / polyX 修剪
 *
 * 本文件是 Python 版 ``submodules/poly_trimming/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与设计文档 `submodules/poly_trimming/poly_trimming.md`——上游若干处
 * 看起来奇怪的逻辑是刻意保留的，不是可以顺手简化的地方。
 *
 * 两者的分工：
 *
 * - **polyG** 专治 Illumina 双色合成化学的系统性假象。那种化学里 G 被编码为
 *   "红绿两个荧光通道都没有信号"，于是信号变暗的一簇会被误读成一串 G。
 *   因为假象只会产生 G，判定时只看"是不是 G"，不统计其他碱基。
 * - **polyX** 处理真实存在的同种碱基尾巴（如 mRNA 的 polyA）。碱基种类事先未知，
 *   必须在扫描中同时维护 A/T/C/G 四个计数，四种全部不像时才停。
 */

#ifndef BIO_POLY_TRIM_H
#define BIO_POLY_TRIM_H

#include <cstdint>
#include <string_view>

#include "fastq.h"

namespace bio {

/* 与 C ABI 的 bio_poly_trim_options_t 一一对应（去掉线程与压缩）。 */
struct PolyTrimOptions {
    bool enabled_poly_g = false;
    bool enabled_poly_x = false;
    int32_t min_length_poly_g = 10;
    int32_t min_length_poly_x = 10;
};

/*
 * 修剪结果。
 *
 * ``kept_length`` 是应当保留的前缀长度；``trimmed_bases`` 为 0 表示没有修剪。
 * ``poly_base`` 是被判定的 poly 碱基（``'A'`` 等），未修剪时为 ``'\0'``。
 */
struct PolyTrimOutcome {
    std::size_t kept_length = 0;
    int64_t trimmed_bases = 0;
    char poly_base = '\0';
};

/*
 * 只切 3' 端的 polyG 尾巴。
 *
 * 从右往左扫描，记录扫过范围内最左边的 G；扫描因错配过多而停止后，
 * 若扫过长度达到阈值，就截断到那个 G 的位置（**那个 G 本身也被切掉**）。
 */
PolyTrimOutcome trim_poly_g(std::string_view sequence, int32_t min_length);

/*
 * 切 3' 端任意碱基的同种尾巴。
 *
 * 停下后取计数最多的碱基（并列时按 A > T > C > G 的顺序取），
 * 再从扫描范围左端向右找到第一个该碱基的位置并从此处截断。
 */
PolyTrimOutcome trim_poly_x(std::string_view sequence, int32_t min_length);

/* 按上游顺序串联两步：polyG 在前，polyX 在后。 */
PolyTrimOutcome compute_poly_trim(std::string_view sequence, const PolyTrimOptions& options);

/* 按结果就地改写记录。poly 修剪只改序列，但质量串必须同步截短。 */
void apply_poly_trim(FastqRecord& record, const PolyTrimOutcome& outcome);

}  // namespace bio

#endif /* BIO_POLY_TRIM_H */
