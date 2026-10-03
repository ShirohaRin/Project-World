/*
 * overlap.h —— 双端 read 的 overlap 分析（上游 fastp 的 OverlapAnalysis）
 *
 * 本文件是 Python 版 ``common/paired_overlap/algorithm.py`` 的逐位等价移植。
 * 改这里之前请先读那份 Python 实现与设计文档 `common/paired_overlap/paired_overlap.md`——判定规则里有几处
 * "不对称"是上游刻意的，都写在那边的注释里：
 *
 * 1. 每个错位量**只判定重叠区前 50 个碱基**，但报出去的错配数是**整段**的；
 * 2. 缺口路径（``Matcher::diffWithOneInsertion``）会在前缀累计第一次超限时
 *    直接返回 -1，因此**只认靠近被比较区域末尾的缺口**。
 *
 * 它是个**工具**，不是算法：产出（是否重叠、错位量、重叠长度、错配数）自身对
 * 用户没有意义，必须被进一步处理（按 overlap 裁接头、校正重叠区低质量碱基、
 * 或把两条 read 合并成一条）。按 `开发规则.md` 3.2 属于第 2 类，放在公共层。
 */

#ifndef BIO_OVERLAP_H
#define BIO_OVERLAP_H

#include <cstdint>
#include <string_view>

namespace bio {

/* 一次 overlap 分析的参数，默认值与 fastp 命令行一致。 */
struct OverlapOptions {
    int32_t diff_limit = 5;              /* 重叠区允许的最大错配数 */
    int32_t require = 30;                /* 认定为重叠所需的最短重叠长度 */
    double diff_percent_limit = 0.2;     /* 错配数还不得超过重叠长度的这个比例 */
    bool allow_gap = false;              /* 是否再尝试"允许 1 个插入/缺失" */
};

/*
 * 一次 overlap 分析的结论。
 *
 * ``offset`` 是 r2 的**反向互补**（rc2）相对 r1 的错位量，符号对应两种不同的
 * 片段几何，**别记反**：
 *
 * - ``> 0``：片段**长于**读长，两条 read 只在中间重叠，都没有读进接头；
 * - ``= 0``：片段长度约等于读长；
 * - ``< 0``：片段**短于**读长，两条 read 都读穿片段、**两端都读进了接头**，
 *   此时 ``overlap_len`` 就是片段长度（裁接头直接拿它当保留长度）。
 *
 * 反向互补会翻转顺序，所以 r2 读到的接头落在 **rc2 的开头**（不是尾部）。
 * 依据是上游 ``PairEndProcessor::statInsertSize`` 的两个分支。
 */
struct OverlapOutcome {
    bool overlapped = false;
    int32_t offset = 0;
    int32_t overlap_len = 0;
    int32_t diff = 0;
    bool has_gap = false;
};

/*
 * 允许 1 个插入时的最小错配数；超过上限返回 -1。
 *
 * ``compare_length`` 必须不小于 1；``inserted`` 需比 ``compare_length`` 多一个
 * 碱基、``normal`` 需至少有 ``compare_length`` 个（上游的调用点都满足）。
 *
 * 与上游唯一的差别：C++ 里前缀累计数组在提前 break 之后是未初始化内存，
 * 而判定循环可能读到它们。本实现（与 Python 版一致）把累计算完整，
 * 因此结果确定、可复现。
 */
int32_t diff_with_one_insertion(const char* inserted,
                                const char* normal,
                                int64_t compare_length,
                                int32_t diff_limit);

/*
 * 分析两条 read 是否来自同一片段。
 *
 * ``read1`` / ``read2`` 传**原始方向**的碱基串，内部自己取反向互补，
 * 因此调用方不必先处理方向。重叠长度达不到 ``require`` 时一律判定为"不重叠"
 * （上游注释：不确定就不认）。
 */
OverlapOutcome analyze_overlap(std::string_view read1,
                               std::string_view read2,
                               const OverlapOptions& options);

}  // namespace bio

#endif /* BIO_OVERLAP_H */
