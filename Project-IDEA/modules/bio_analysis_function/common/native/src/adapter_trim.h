/*
 * adapter_trim.h —— reads 接头裁剪（上游 fastp 的 AdapterTrimmer / Matcher）
 *
 * 本文件是 Python 版 ``submodules/adapter_trimming/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与设计文档 `submodules/adapter_trimming/adapter_trimming.md`——上游有两处
 * "看起来像笔误/隐患"的写法是刻意保留的，都写在那边的注释里：
 *
 * 1. 插入/缺失那两段比对**锚定在 read 开头**（随扫描位置变的只有比对长度）；
 * 2. ``matchWithOneInsertion`` 在前缀累计提前 break 后会读未初始化的数组元素，
 *    本实现按它的意图把累计算完整，因此结果确定、可复现。
 */

#ifndef BIO_ADAPTER_TRIM_H
#define BIO_ADAPTER_TRIM_H

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "bio_native.h"
#include "fastq.h"

namespace bio {

/* 一次裁剪的配置。接头表由调用方给出（空表 = 不裁）。 */
struct AdapterTrimOptions {
    std::vector<std::string> adapters;
    int32_t match_required = 4;   /* 单条时的最短匹配长度 */
    bool allow_one_gap = true;    /* 是否允许 1 个插入/缺失 */
};

/* 一条 read 的裁剪结果。 */
struct AdapterTrimOutcome {
    bool trimmed = false;
    int64_t removed_bases = 0;
};

/*
 * 判断把 ``inserted`` 多出的那个碱基"吃掉"之后能否在容错范围内对上。
 *
 * ``compare_length`` 必须不小于 2（上游的调用点都满足；更小时上游会越界访问，
 * 这里直接判否）。
 */
bool match_with_one_insertion(const char* inserted,
                              const char* normal,
                              int64_t compare_length,
                              int64_t diff_limit);

/*
 * 在 read 上找接头起点；找到返回 true，``position`` 回传起点（**可以为负**：
 * 表示整条 read 都是接头，接头的前 ``-position`` 个碱基没被读到）。
 */
bool find_adapter_position(std::string_view sequence,
                           std::string_view adapter,
                           int32_t match_required,
                           bool allow_one_gap,
                           int64_t& position);

/* 按候选表裁剪一条 read（依次尝试，任一条命中就算裁过）。 */
AdapterTrimOutcome apply_adapter_trim(FastqRecord& record,
                                      const AdapterTrimOptions& options);

/* 候选表模式下的最短匹配长度（上游规则：>16 取 5、>256 取 6）。 */
int32_t match_required_for(std::size_t candidate_count);

}  // namespace bio

#endif /* BIO_ADAPTER_TRIM_H */
