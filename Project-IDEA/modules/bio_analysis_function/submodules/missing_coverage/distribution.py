"""覆盖度分布与低覆盖阈值（分片 B）：负二项拟合 + 左尾概率定阈。

上游的推理链（[Barrick 2014, BMC Genomics](https://pmc.ncbi.nlm.nih.gov/articles/PMC4300727/)，
也见 [breseq 文档 · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）：

1. 如果 read 是**随机**撒在参考上的，各深度的位置数应服从**泊松分布**；实际数据比泊松更"胖"：
   库的 GC 偏好、重复区、局部的可比对性差异都会让同一条序列上的覆盖度**过度离散**。
2. 所以改用**负二项分布**拟合。本实现按**矩估计**取参数：均值 ``μ``、总体方差 ``σ²``，
   离散参数 ``r = μ² / (σ² − μ)``、``p = r / (r + μ)``。
   ``σ² ≤ μ``（没有过度离散，甚至比泊松更窄）时**退回泊松**——负二项在 ``r → ∞`` 的极限就是它，
   此时硬凑一个 ``r`` 只会得到负数或无穷。
3. **低覆盖阈值**：取一个深度，使它的**左尾概率**等于 ``0.05 / sqrt(L)``，``L`` 是这条参考序列的
   长度。乘在分母上的 ``sqrt(L)`` 是把"整条参考上到处都可能瞎报"的多重比较折进去——位置越多、
   要求越严。这与共识打分的 ``− log₁₀(参考总长)`` 是同一个思路。

**本实现与上游的差异（明确记录）**：上游文档只说"拟合负二项"，没给拟合方法；这里用矩估计而非
极大似然，也不做离群点剔除，**不承诺与上游数值一致**。要改的是方法而不是开关，所以不设参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, floor, lgamma, log, log1p, sqrt

from .profile import CoverageProfile

__all__ = [
    "CoverageDistribution",
    "fit_coverage_distribution",
    "low_coverage_cutoff",
]


@dataclass(frozen=True, slots=True)
class CoverageDistribution:
    """覆盖度分布：观测到的两个矩，加上由此得到的离散参数。

    ``dispersion is None`` 表示"没有过度离散"，此时按泊松分布算概率（``r → ∞`` 的极限）。
    """

    #: 平均深度。
    mean: float
    #: 深度的总体方差（``ddof=0``）。
    variance: float
    #: 负二项的离散参数 ``r``；``None`` 表示退化成泊松。
    dispersion: float | None

    @classmethod
    def from_moments(cls, mean: float, variance: float) -> "CoverageDistribution":
        """按矩估计构造：``σ² > μ`` 时取负二项，否则退回泊松。"""
        if mean < 0 or variance < 0:
            raise ValueError(f"均值与方差都必须非负，当前为 {mean!r} / {variance!r}。")
        if variance > mean > 0:
            return cls(mean=mean, variance=variance, dispersion=mean * mean / (variance - mean))
        return cls(mean=mean, variance=variance, dispersion=None)

    @property
    def is_poisson(self) -> bool:
        """是否退回到了泊松（没有过度离散）。"""
        return self.dispersion is None

    @property
    def probability(self) -> float:
        """负二项的 ``p``（仅在 :attr:`dispersion` 不是 ``None`` 时有意义）。"""
        if self.dispersion is None:
            raise ValueError("泊松分支没有负二项的 p 参数。")
        return self.dispersion / (self.dispersion + self.mean)

    def pmf(self, depth: int) -> float:
        """深度恰好为 ``depth`` 的概率。"""
        if depth < 0:
            return 0.0
        if self.dispersion is None:
            if self.mean <= 0:
                return 1.0 if depth == 0 else 0.0
            return exp(-self.mean + depth * log(self.mean) - lgamma(depth + 1))
        r = self.dispersion
        p = self.probability
        return exp(
            lgamma(depth + r)
            - lgamma(r)
            - lgamma(depth + 1)
            + r * log(p)
            + depth * log1p(-p)
        )

    def cdf(self, depth: int) -> float:
        """深度 **≤** ``depth`` 的概率（左尾）。"""
        if depth < 0:
            return 0.0
        return sum(self.pmf(value) for value in range(depth + 1))

    def quantile(self, probability: float) -> int:
        """最小的深度 ``d`` 使 ``cdf(d) ≥ probability``。

        深度是整数、且分布偏斜，用精确的分位点定义（而不是连续近似）。
        """
        if not 0.0 < probability < 1.0:
            raise ValueError(f"概率必须落在 (0, 1)，当前为 {probability!r}。")
        limit = int(self.mean + 20.0 * sqrt(self.variance) + 100.0)
        total = self.pmf(0)
        depth = 0
        while total < probability and depth < limit:
            depth += 1
            total += self.pmf(depth)
        if total < probability:  # pragma: no cover - limit 已给足余量
            raise ValueError(
                f"在深度 ≤ {limit} 内找不到左尾概率 {probability!r} 的分位点。"
            )
        return depth


def fit_coverage_distribution(profile: CoverageProfile) -> CoverageDistribution | None:
    """拟合一条参考序列的覆盖度分布。

    整条序列一条 read 都没有（均值 0）时返回 ``None``——没有数据就没有分布，
    编一个阈值出来会让"这里其实什么都没测到"变成"这里检测到缺失"。
    """
    if profile.length == 0:
        return None
    mean = profile.mean
    if mean <= 0.0:
        return None
    return CoverageDistribution.from_moments(mean, profile.variance)


def low_coverage_cutoff(
    distribution: CoverageDistribution,
    reference_length: int,
    *,
    tail_probability: float = 0.05,
) -> int:
    """低覆盖阈值：左尾概率等于 ``tail_probability / sqrt(reference_length)`` 的深度。

    深度 **≤** 这个阈值的位置算"低覆盖"。``reference_length`` 是**这条参考序列**的长度
    （不是全基因组总长），与上游一致。

    阈值额外**不超过平均深度**：阈值高于均值意味着"一半以上的位置都算低覆盖"，
    那样找到的"缺失区"就没有意义了，此时退回平均深度（这一条是我们的护栏，不是上游的规定）。
    """
    if reference_length <= 0:
        raise ValueError(f"参考序列长度必须为正，当前为 {reference_length!r}。")
    if not 0.0 < tail_probability < 1.0:
        raise ValueError(f"尾概率必须落在 (0, 1)，当前为 {tail_probability!r}。")
    alpha = tail_probability / sqrt(reference_length)
    return max(0, min(distribution.quantile(alpha), floor(distribution.mean)))
