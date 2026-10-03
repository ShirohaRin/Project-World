"""覆盖度与缺失区（MC）：从读段深度找"整段没有 read"的区域，作为大片段缺失的证据。

这是参考比对与变异检测线的第五项（算法清单 5.5.4），对应上游 breseq 的 `MC` 证据线
（missing coverage evidence）。上游的做法（[Barrick 2014, BMC Genomics](https://pmc.ncbi.nlm.nih.gov/articles/PMC4300727/)；
文档见 [breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）：

1. **覆盖度分布**：read 若随机撒在参考上，各深度的位置数应服从泊松分布；实际数据**过度离散**，
   所以改用**负二项分布**拟合（本实现按矩估计取参数）。
2. **低覆盖阈值**：取一个深度，使其**左尾概率**等于 ``0.05 / sqrt(参考序列长度)``——
   乘上 ``sqrt(L)`` 是把"整条参考上到处可能瞎报"的多重比较折进去，与共识打分的
   ``− log₁₀(参考总长)`` 同一思路。
3. **seed-and-extend**：以"一条 read 都没有"的位置为种子，向两侧扩展、穿过同样低覆盖的区域，
   直到覆盖度超过阈值。得到的是**证据**，`MC + JC → DEL` 的判定在编排层（算法清单 5.5.2）。

与共识调用那条线一样，**输入只到公共层**（参考集合 + SAM），不依赖其它子模块：
覆盖度只需要"每个位置被多少条 read 盖住"，用差分数组直接算，比堆叠省得多。

分片推进（每片带自己的测试）：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **覆盖度剖面**：SAM → 逐位深度（差分数组，只看 unique 比对）（`profile.py`） | 已完成 |
| B | **分布拟合与阈值**：负二项矩估计 → 左尾 α = 0.05/√L 的深度（`distribution.py`） | 已完成 |
| C | **缺失区检测**：零覆盖种子 + 向两侧扩展（`missing.py`） | 已完成 |
| D | **证据行构造与端到端**：`MC` 证据行（字段口径待与真实产物逐字段核对）、合成已知缺失对拍 | 待做 |
"""

from __future__ import annotations

from .distribution import (
    CoverageDistribution,
    fit_coverage_distribution,
    low_coverage_cutoff,
)
from .missing import (
    MissingCoverageAnalysis,
    MissingCoverageRegion,
    MissingCoverageSettings,
    analyze_missing_coverage,
    find_missing_coverage,
)
from .profile import (
    CoverageProfile,
    build_coverage_profile,
    iter_coverage_profiles,
)

__all__ = [
    "CoverageDistribution",
    "CoverageProfile",
    "MissingCoverageAnalysis",
    "MissingCoverageRegion",
    "MissingCoverageSettings",
    "analyze_missing_coverage",
    "build_coverage_profile",
    "find_missing_coverage",
    "fit_coverage_distribution",
    "iter_coverage_profiles",
    "low_coverage_cutoff",
]
