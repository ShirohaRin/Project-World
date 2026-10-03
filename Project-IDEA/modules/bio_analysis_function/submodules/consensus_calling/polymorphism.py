"""多态档判定（分片 E）：这个位置有没有"一小撮人"带着别的碱基。

上游的多态性（`RA` 证据行里 ``prediction=polymorphism``）报的常常是**少数派碱基**——真实产物里
频率低到 7%–12%（例如 `RA 37 … 0 C T frequency=1.04031563e-01`，参考 C 还是多数派，T 只占一成）。
这一点决定了算法不能用"最佳碱基与参考不同"那一套：**少数派的似然永远比参考低**，
"谁是最佳碱基"这个问题本身就问不出它来。

所以这一档换一个模型：

**混合模型**。假设群体里比例 ``f`` 的个体在碱基 ``X`` 上变了、其余还是参考碱基 ``R``，
一条观测的概率就是

    P(观测) = f · P(b | X, q) + (1 − f) · P(b | R, q)

对每个候选 ``X ≠ R`` 在 ``f`` 的网格上取最大似然，得到 ``f̂``，并算

    ratio = log₁₀[ L(f̂) / L(f=0) ]        score = ratio − log₁₀(参考总长)

``L(f=0)`` 就是"全都是参考、看到的差异都是测序错误"这个**纯参考解释**；混合解释更好
（ratio > 0）才说明这里有别的碱基。减 ``log₁₀`` 参考总长与共识档同一口径（多重比较折减）。

按 ``f̂`` 分档，**同一个位置只会进其中一档**：

| ``f̂`` | 归谁 |
| --- | --- |
| ≥ ``max_frequency``（默认 0.8） | 共识档（:func:`..consensus.call_consensus`） |
| ``[min_frequency, max_frequency)`` | **多态档**（本模块） |
| < ``min_frequency`` | 不报（当作噪声） |

**默认阈值是我们的取值，不是上游的**：上游的多态性判定还带统计检验与链偏好检验
（证据行里的 ``bias_p_value`` / ``fisher_strand_p_value`` 等），我们没有复刻那一套。所以
:class:`PolymorphismSettings` 里三个阈值都显式留成参数，真实数据上按需要调。``f`` 的网格取
0.01 步长（取值精度对报告没有意义）。

**一列里最多报几个**：对每个候选碱基各判一次（一个位置理论上可能有两个少数派，极少见），
都通过阈值就都报出来；排序按打分降序、再按碱基名，保证输出确定。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .consensus import (
    ConsensusCall,
    ConsensusSettings,
    _prepare,
    call_consensus,
    score_position,
)
from .error_rates import BaseErrorRates
from .pileup import PileupColumn, iter_pileup
from .trimming import ReferenceTrimmer

__all__ = [
    "PolymorphismCall",
    "PolymorphismScore",
    "PolymorphismSettings",
    "call_polymorphisms",
    "call_variants",
    "score_polymorphisms",
]

_BASES = "ACGT"
_LOG10 = math.log10

#: 混合比例 ``f`` 的搜索网格（0.01 … 1.00）。``f=0`` 不在这里——那是"纯参考解释"，单独算。
_FREQUENCY_GRID: tuple[float, ...] = tuple(round(step / 100, 2) for step in range(1, 101))


@dataclass(frozen=True, slots=True)
class PolymorphismSettings:
    """多态档的阈值。

    **这三个默认值是我们的取值**（见模块文档）：上游那一套还带统计检验，我们没有复刻。
    """

    #: 混合比例下限（``f̂`` 低于它视为噪声）。
    min_frequency: float = 0.05
    #: 混合比例上限（**不含**）：达到它的位置归共识档，不重复报。
    #: 建议把它设成与 :class:`..consensus.ConsensusSettings` 的 ``frequency_cutoff`` 一致，
    #: 否则两档之间会出现一小段没人管、或者两边都管的频率区间。
    max_frequency: float = 0.8
    #: 打分下限（``ratio − log₁₀(参考总长)``）。
    e_value_cutoff: float = 3.0


@dataclass(frozen=True, slots=True)
class PolymorphismScore:
    """一个位置上、针对某个候选碱基的混合模型打分。"""

    seq_id: str
    position: int
    reference_base: str
    call_base: str
    #: 混合比例的最大似然估计 ``f̂``。
    frequency: float
    #: ``log₁₀[ L(f̂) / L(f=0) ]``——减多重比较折减之前的值。
    likelihood_ratio: float
    total_reads: int
    #: 观测里 ``call_base`` 的条数（``f̂`` 与它不一定相等，因为误差率不对称）。
    variant_reads: int


@dataclass(frozen=True, slots=True)
class PolymorphismCall:
    """一条报出来的多态调用（0-based 位置）。"""

    seq_id: str
    position: int
    reference_base: str
    #: 那个"少数派"碱基（也可能占多数、但没达到共识档的频率阈值）。
    call_base: str
    frequency: float
    #: ``likelihood_ratio − log₁₀(参考总长)``，越大越显著。与共识档的 ``consensus_score``
    #: 同一尺度（同样减了参考总长），但分子来自混合模型，两者不能直接比大小。
    score: float
    likelihood_ratio: float
    total_reads: int
    variant_reads: int
    #: 固定为 ``polymorphism``；共识档是另一个类型，两者都有这个字段便于下游统一处理。
    prediction: str = "polymorphism"


def _observations(column: PileupColumn) -> dict[tuple[str, int], int]:
    """按 (碱基, 质量) 归并观测数；被裁的、非 ACGT 的不进（与共识档同一口径）。"""
    counts: dict[tuple[str, int], int] = {}
    for observation in column.bases:
        if observation.base not in _BASES or observation.trimmed:
            continue
        key = (observation.base, observation.quality)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _log_likelihood(
    counts: Mapping[tuple[str, int], int],
    *,
    candidate: str,
    reference_base: str,
    rates: BaseErrorRates,
    fraction: float,
) -> float:
    """``log₁₀`` 似然：每条观测要么来自"变了的那部分"(f)、要么来自参考那部分(1−f)。"""
    total = 0.0
    for (base, quality), count in counts.items():
        probability = (
            fraction * rates.probability(candidate, base, quality)
            + (1.0 - fraction) * rates.probability(reference_base, base, quality)
        )
        total += count * _LOG10(probability)
    return total


def score_polymorphisms(
    column: PileupColumn,
    rates: BaseErrorRates,
    *,
    max_frequency: float = 0.8,
) -> tuple[PolymorphismScore, ...]:
    """给一个位置上的每个候选碱基算混合模型打分。

    只返回 ``0 < f̂ < max_frequency`` 的候选（达到共识档的、压根没观测的都不要），按打分降序、
    同分按碱基名排。参考碱基不是 ACGT、或该位置没有可用观测时返回空元组。
    """
    reference_base = column.reference_base
    if reference_base not in _BASES:
        return ()
    counts = _observations(column)
    if not counts:
        return ()

    total_reads = sum(counts.values())
    pure_reference = _log_likelihood(
        counts, candidate=reference_base, reference_base=reference_base, rates=rates, fraction=0.0
    )

    found: list[PolymorphismScore] = []
    for candidate in _BASES:
        if candidate == reference_base:
            continue
        variant_reads = sum(
            count for (base, _quality), count in counts.items() if base == candidate
        )
        if variant_reads == 0:
            continue  # 没有观测支持它，谈不上"有别的碱基"
        best_fraction = 0.0
        best_log_likelihood = pure_reference
        for fraction in _FREQUENCY_GRID:
            if fraction >= max_frequency:
                break  # 再大的比例归共识档，这里不判
            value = _log_likelihood(
                counts,
                candidate=candidate,
                reference_base=reference_base,
                rates=rates,
                fraction=fraction,
            )
            if value > best_log_likelihood:
                best_log_likelihood, best_fraction = value, fraction
        if best_fraction == 0.0:
            continue  # 纯参考解释就是最好的：这里没有别的碱基
        found.append(
            PolymorphismScore(
                seq_id=column.seq_id,
                position=column.position,
                reference_base=reference_base,
                call_base=candidate,
                frequency=best_fraction,
                likelihood_ratio=best_log_likelihood - pure_reference,
                total_reads=total_reads,
                variant_reads=variant_reads,
            )
        )

    found.sort(key=lambda item: (-item.likelihood_ratio, item.call_base))
    return tuple(found)


def call_polymorphisms(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    rates: BaseErrorRates | None = None,
    settings: PolymorphismSettings = PolymorphismSettings(),
    default_quality: int = 0,
    trimming: Mapping[str, ReferenceTrimmer] | None = None,
) -> Iterator[PolymorphismCall]:
    """逐位置判定，产出**多态档**调用（``prediction="polymorphism"``）。

    与共识档共用堆叠、错误率表与 read 端裁剪口径，但模型是混合模型（见模块文档），所以它能
    报出**少数派**碱基——那些位置在共识档里会整个消失（少数派的似然永远低于参考）。

    什么时候用它：进化实验里"这个突变还没固定"、样品里混了多个克隆或多拷贝——这类信息恰好是
    "只有一部分 read 支持"的形态。
    """
    rates, length_penalty, source = _prepare(
        reference,
        source,
        rates,
        default_quality=default_quality,
        trimming=trimming,
    )

    for column in iter_pileup(
        reference, source, default_quality=default_quality, trimming=trimming
    ):
        owner = score_position(column, rates)
        if owner is not None and owner.is_variant and owner.frequency >= settings.max_frequency:
            continue  # 这个位置归共识档管，不在这里重复报（见 max_frequency 的说明）
        for score in score_polymorphisms(
            column, rates, max_frequency=settings.max_frequency
        ):
            if score.frequency < settings.min_frequency:
                continue
            value = score.likelihood_ratio - length_penalty
            if value < settings.e_value_cutoff:
                continue
            yield PolymorphismCall(
                seq_id=score.seq_id,
                position=score.position,
                reference_base=score.reference_base,
                call_base=score.call_base,
                frequency=score.frequency,
                score=value,
                likelihood_ratio=score.likelihood_ratio,
                total_reads=score.total_reads,
                variant_reads=score.variant_reads,
            )


def call_variants(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    rates: BaseErrorRates | None = None,
    consensus: ConsensusSettings = ConsensusSettings(),
    polymorphism: PolymorphismSettings | None = PolymorphismSettings(),
    default_quality: int = 0,
    trimming: Mapping[str, ReferenceTrimmer] | None = None,
) -> Iterator[ConsensusCall | PolymorphismCall]:
    """两档一起跑出来，按（参考名、位置）升序；``prediction`` 区分它们。

    ``polymorphism=None`` 表示只要共识档。错误率表只建一次（两档共用）。调用条数通常很少，
    这里把两档收齐后统一排序，保证输出顺序确定。
    """
    prepared_rates, _penalty, prepared_source = _prepare(
        reference,
        source,
        rates,
        default_quality=default_quality,
        trimming=trimming,
    )
    calls: list[ConsensusCall | PolymorphismCall] = list(
        call_consensus(
            reference,
            prepared_source,
            rates=prepared_rates,
            settings=consensus,
            default_quality=default_quality,
            trimming=trimming,
        )
    )
    if polymorphism is not None:
        calls.extend(
            call_polymorphisms(
                reference,
                prepared_source,
                rates=prepared_rates,
                settings=polymorphism,
                default_quality=default_quality,
                trimming=trimming,
            )
        )
    calls.sort(key=lambda call: (call.seq_id, call.position))
    return iter(calls)
