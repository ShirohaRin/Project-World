"""缺失区检测（分片 C）：零覆盖种子 + 向两侧扩展（seed-and-extend）。

上游对这一段的口径（[Barrick 2014, BMC Genomics](https://pmc.ncbi.nlm.nih.gov/articles/PMC4300727/)）：
"finds places in the genome where no reads align and then extends these intervals in both
directions and through repeat regions. Extension of the MC interval is stopped when uniquely
mapped read coverage exceeds a threshold"。

本实现照这条描述写死成一句话：

> **一段"低覆盖区"= 连续的、深度 ≤ 阈值的位置；只有其中至少含一个"零覆盖"位置的才报。**

- **种子**是深度为 0 的位置——"这里一条 read 都没有"才是缺失的硬信号；
- **扩展**就是把这个区间向两侧吃进同样低覆盖的位置（它们在阈值之内，说明"低得可疑"）；
- **穿过重复区**在这里是自然发生的：重复区里的 read 会被 MAPQ 0 挡在覆盖度之外，
  于是它在覆盖剖面上本来就是低覆盖，会被同一段吃进去。
- 相邻两段之间若隔着"高覆盖"的位置就不合并——那说明中间其实有 read，是两件事。

报出来的是**证据**（`MC`），不是突变：`MC + JC → DEL`（精确端点靠 JC 或两侧同向重复）
是编排层的规则（算法清单 5.5.2），本模块不下这个结论。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .distribution import (
    CoverageDistribution,
    fit_coverage_distribution,
    low_coverage_cutoff,
)
from .profile import CoverageProfile, iter_coverage_profiles

__all__ = [
    "MissingCoverageAnalysis",
    "MissingCoverageRegion",
    "MissingCoverageSettings",
    "analyze_missing_coverage",
    "find_missing_coverage",
]


@dataclass(frozen=True, slots=True)
class MissingCoverageSettings:
    """MC 这条线的两个参数。默认值是上游写明的那个口径（左尾 0.05 / 只看 unique 比对）。"""

    #: 左尾概率的分子：阈值取左尾概率 = 它 / sqrt(参考序列长度)。
    tail_probability: float = 0.05
    #: 只有比对质量 ≥ 它的 read 才计入"唯一"覆盖（MAPQ 0 是多命中）。
    min_mapping_quality: int = 1


@dataclass(frozen=True, slots=True)
class MissingCoverageRegion:
    """一段疑似缺失（0-based 闭区间 ``[start, end]``）。

    四个 ``*_cov`` 与上游证据行的同名属性对齐：区间**外**第一个位置的深度、区间**内**第一个
    位置的深度，右侧同理。区间贴着序列两端时，外侧没有位置，取 ``None``。
    """

    seq_id: str
    start: int
    end: int
    left_outside_cov: int | None
    left_inside_cov: int
    right_inside_cov: int
    right_outside_cov: int | None

    @property
    def length(self) -> int:
        """区间长度（碱基数）——``DEL`` 的长度字段就是它。"""
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class MissingCoverageAnalysis:
    """一条参考序列上的 MC 分析结果。

    ``distribution`` / ``cutoff`` 为 ``None`` 表示这条序列没有可用覆盖（分布无从拟合），
    此时 ``regions`` 一定是空的。
    """

    seq_id: str
    distribution: CoverageDistribution | None
    cutoff: int | None
    regions: tuple[MissingCoverageRegion, ...]


def find_missing_coverage(
    profile: CoverageProfile,
    *,
    tail_probability: float = 0.05,
) -> MissingCoverageAnalysis:
    """在一份覆盖剖面上找缺失区（拟合分布 → 定阈值 → seed-and-extend）。"""
    distribution = fit_coverage_distribution(profile)
    if distribution is None:
        return MissingCoverageAnalysis(
            seq_id=profile.seq_id, distribution=None, cutoff=None, regions=()
        )
    cutoff = low_coverage_cutoff(
        distribution, profile.length, tail_probability=tail_probability
    )

    depths = profile.depths
    length = len(depths)
    regions: list[MissingCoverageRegion] = []
    index = 0
    while index < length:
        if depths[index] > cutoff:
            index += 1
            continue
        start = index
        has_seed = False
        while index < length and depths[index] <= cutoff:
            if depths[index] == 0:
                has_seed = True
            index += 1
        end = index - 1
        if not has_seed:
            continue  # 低但不为零：没有"一条 read 都没有"的种子，不报
        regions.append(
            MissingCoverageRegion(
                seq_id=profile.seq_id,
                start=start,
                end=end,
                left_outside_cov=depths[start - 1] if start > 0 else None,
                left_inside_cov=depths[start],
                right_inside_cov=depths[end],
                right_outside_cov=depths[end + 1] if end + 1 < length else None,
            )
        )
    return MissingCoverageAnalysis(
        seq_id=profile.seq_id,
        distribution=distribution,
        cutoff=cutoff,
        regions=tuple(regions),
    )


def analyze_missing_coverage(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    settings: MissingCoverageSettings = MissingCoverageSettings(),
) -> Iterator[MissingCoverageAnalysis]:
    """从比对结果出发，按参考文件的顺序逐条序列给出 MC 分析。

    这是给编排层用的入口：``reference`` + SAM → 若干 ``MissingCoverageAnalysis``。
    """
    for profile in iter_coverage_profiles(
        reference, source, min_mapping_quality=settings.min_mapping_quality
    ):
        yield find_missing_coverage(profile, tail_probability=settings.tail_probability)
