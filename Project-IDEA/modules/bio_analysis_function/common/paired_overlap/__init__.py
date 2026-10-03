"""paired_overlap：双端 read 的 overlap 分析（**公共层工具**，不是算法入口）。

实现与 fastp 1.3.x 的 ``OverlapAnalysis::analyze`` 逐位对齐。

**产出**：一个 :class:`OverlapResult`（是否重叠、错位量、重叠长度、错配数、是否带缺口）。
**不写文件、不改动输入**。

**为什么是工具**：产出自身对用户没有意义，必须被进一步处理——按 overlap 裁掉接头、
用重叠区校正低质量碱基、或把两条 read 合并成一条。按 `开发规则.md` 3.2 属于第 2 类，
因此没有独立入口；后续的双端算法会调用它。

    algorithm.py  分析本体（四次扫描：正向/反向 × 无缺口/允许 1 个缺口）

序列层面的公共工具（反向互补、错配计数）在上一级：``common/sequences.py``。
"""

from .algorithm import (
    GAP_ONLY_VECTOR,
    UPSTREAM_TEST_VECTORS,
    OverlapConfig,
    OverlapResult,
    analyze_overlap,
    diff_with_one_insertion,
)

__all__ = [
    "GAP_ONLY_VECTOR",
    "UPSTREAM_TEST_VECTORS",
    "OverlapConfig",
    "OverlapResult",
    "analyze_overlap",
    "diff_with_one_insertion",
]
