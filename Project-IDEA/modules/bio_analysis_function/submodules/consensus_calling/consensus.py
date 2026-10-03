"""共识打分与判定（分片 C）：给每个位置定一个碱基，与参考不同就报突变。

这是 `RA` 证据线真正下结论的一步。上游的口径（[Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）是：

1. 每个位置对四种碱基各算一个似然 ``L(X)``（用分片 B 那张经验错误率表，对每个观测连乘）；
2. 取 ``L`` 最大的那个碱基；**若它不是参考碱基**，就用
   ``log₁₀L(X*) − log₁₀(所有参考序列的累计长度)`` 作为这个预测的**共识打分**；
3. 打分 ≥ 阈值（默认 **10**）且变异频率 ≥ 阈值（默认 **0.8**）才报出来。

三处必须讲清楚的地方：

**为什么是似然比。** 单看 ``L(X*)`` 是个连乘出来的极小正数，没有可比性；实际判断是
"这个碱基比参考碱基好多少"，所以实现里算的是相对参考的比值

    ratio(X) = Σ_i [ log₁₀ P(b_i | X, q_i) − log₁₀ P(b_i | R, q_i) ]

其中 ``R`` 是参考碱基、``b_i``/``q_i`` 是第 i 个观测的碱基与质量。这就是 ``log₁₀(L(X)/L(R))``，
且 ``ratio(R) = 0``（参考自己跟自己比）。**打分 = ratio(X*) − log₁₀(N)**，减去参考总长的
``log₁₀`` 是把"全基因组每个位置都试过一遍"的多重比较折进去——位置越多、要求越严。名字里的
"E-value" 容易误解：这里是**越大越显著**（它是负对数尺度上的量），不是通常那种越小越好的 E 值。

**并列时怎么办。** 参考碱基与某个候选取到**完全相同的比值**时（典型情形：错误率表对该质量
没有观测、退化成均匀分布，所有候选的 ratio 都是 0），本实现**保留参考碱基、不报**。理由：
报突变是要付出代价的结论，证据不足时应当留在"没变化"这一侧——这条和有测试钉住。

**这一片只做碱基替换。** 观测里的插入/缺失证据（``I``/``D``）这一片**不参与**判定：缺失是
另一套语义（"这里的碱基没了"要和重复序列背景一起看，见分片 D 的 read 端裁剪），插进同一个
似然里会让"频率"到底按什么算变得含糊。所以 :func:`call_consensus` 目前产出的是**碱基被替换
的调用**（上游的 SNP/SUB），小 indel 的判定留给后续。

**规模与两遍扫描**：``rates`` 不给时，本函数先扫一遍堆叠建错误率表、再扫第二遍逐位置判定。
对文件输入没问题（重新打开即可）；对**迭代器**输入会先把记录定住成元组，否则第二遍就空了。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .error_rates import BaseErrorRates, build_error_rates
from .pileup import PileupColumn, iter_pileup
from .trimming import ReferenceTrimmer

__all__ = [
    "ConsensusCall",
    "ConsensusSettings",
    "PositionScore",
    "call_consensus",
    "score_position",
]

#: 可判读的碱基（与堆叠、错误率表同一口径）。
_BASES = "ACGT"
_LOG10 = math.log10


@dataclass(frozen=True, slots=True)
class ConsensusSettings:
    """判定阈值。默认值取自上游的默认设置（E-value 10、频率 0.8）。"""

    #: 共识打分下限：``ratio − log₁₀(参考总长)`` 必须 ≥ 它。
    e_value_cutoff: float = 10.0
    #: 变异频率下限：支持该碱基的观测数 / 参与判定的观测数 必须 ≥ 它。
    frequency_cutoff: float = 0.8


@dataclass(frozen=True, slots=True)
class PositionScore:
    """一个位置上的打分结果（不论最终报不报，都能拿到）。"""

    seq_id: str
    position: int
    reference_base: str
    total_reads: int
    best_base: str
    best_ratio: float
    frequency: float
    ratios: Mapping[str, float]

    @property
    def is_variant(self) -> bool:
        """最佳碱基是否与参考不同。"""
        return self.best_base != self.reference_base

    @property
    def variant_reads(self) -> int:
        """支持最佳碱基的观测数（``frequency`` 的分子）。"""
        return round(self.frequency * self.total_reads)


@dataclass(frozen=True, slots=True)
class ConsensusCall:
    """一条报出来的调用（0-based 位置）。

    ``prediction`` 区分两档：``consensus``（共识档——几乎全部 read 都跟参考不同，视为固定的
    突变）与 ``polymorphism``（多态档——只有一部分 read 不同，群体里还有没变的个体）。
    两档共用这一个类型，因为它们是**同一个位置、同一套打分**，只差阈值；上游的证据行也是这样，
    靠 ``prediction=`` 属性区分。
    """

    seq_id: str
    position: int
    reference_base: str
    call_base: str
    #: ``log₁₀(L(call_base) / L(reference_base))``——不含多重比较那部分。
    likelihood_ratio: float
    #: 共识打分 = ``likelihood_ratio − log₁₀(参考总长)``，越大越显著。
    consensus_score: float
    frequency: float
    total_reads: int
    variant_reads: int
    #: ``consensus``（本档固定值）。多态档是另一个类型 :class:`..polymorphism.PolymorphismCall`，
    #: 两者都有这个字段，便于下游统一按 ``prediction`` 处理。
    prediction: str = "consensus"


def score_position(column: PileupColumn, rates: BaseErrorRates) -> PositionScore | None:
    """给一个位置打分；不可判时返回 ``None``。

    不可判有两种：参考碱基不是 ACGT（无从定义"与参考不同"），以及该位置没有任何**可用**的
    碱基观测（全是 ``N``，或全落在被裁剪的 read 末端里）。被裁的观测**不参与**似然计算
    ——它们的位置本身就多解（见 `trimming.py`）。
    """
    reference_base = column.reference_base
    if reference_base not in _BASES:
        return None
    observations = [
        item for item in column.bases if item.base in _BASES and not item.trimmed
    ]
    if not observations:
        return None

    # 同一个 (候选碱基, 观测碱基, 质量) 组合在一列里会反复出现（同质量、同碱基），缓存
    # ``log₁₀ P`` 可以把每列的四次取对数降到"不同组合数"次。
    cache: dict[tuple[str, str, int], float] = {}

    def log_probability(candidate: str, base: str, quality: int) -> float:
        key = (candidate, base, quality)
        value = cache.get(key)
        if value is None:
            value = _LOG10(rates.probability(candidate, base, quality))
            cache[key] = value
        return value

    ratios: dict[str, float] = {}
    for candidate in _BASES:
        ratio = 0.0
        for observation in observations:
            ratio += log_probability(candidate, observation.base, observation.quality)
            ratio -= log_probability(reference_base, observation.base, observation.quality)
        ratios[candidate] = ratio

    # 并列时保留参考碱基（严格大于才替换）：证据不足时不报，留在"没变化"这一侧。
    best_base = reference_base
    best_ratio = ratios[reference_base]
    for candidate in _BASES:
        if ratios[candidate] > best_ratio:
            best_base, best_ratio = candidate, ratios[candidate]

    total = len(observations)
    supporting = sum(1 for observation in observations if observation.base == best_base)
    return PositionScore(
        seq_id=column.seq_id,
        position=column.position,
        reference_base=reference_base,
        total_reads=total,
        best_base=best_base,
        best_ratio=best_ratio,
        frequency=supporting / total,
        ratios=MappingProxyType(ratios),
    )


def _prepare(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    rates: BaseErrorRates | None,
    *,
    default_quality: int,
    trimming: Mapping[str, ReferenceTrimmer] | None,
) -> tuple[BaseErrorRates, float, str | Path | Iterable[SamRecord]]:
    """备好错误率表与"多重比较"折减项。

    顺带把**迭代器**输入定住成元组返回：建表与判定要扫两遍，第二遍遇到空迭代器就什么都判不出来。
    """
    total_reference_length = sum(len(sequence.sequence) for sequence in reference)
    length_penalty = _LOG10(total_reference_length) if total_reference_length else 0.0
    if rates is None:
        if not isinstance(source, (str, Path)):
            source = tuple(source)
        rates = build_error_rates(
            reference, source, default_quality=default_quality, trimming=trimming
        )
    return rates, length_penalty, source


def _to_call(score: PositionScore, consensus_score: float, prediction: str) -> ConsensusCall:
    return ConsensusCall(
        seq_id=score.seq_id,
        position=score.position,
        reference_base=score.reference_base,
        call_base=score.best_base,
        likelihood_ratio=score.best_ratio,
        consensus_score=consensus_score,
        frequency=score.frequency,
        total_reads=score.total_reads,
        variant_reads=score.variant_reads,
        prediction=prediction,
    )


def call_consensus(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    rates: BaseErrorRates | None = None,
    settings: ConsensusSettings = ConsensusSettings(),
    default_quality: int = 0,
    trimming: Mapping[str, ReferenceTrimmer] | None = None,
) -> Iterator[ConsensusCall]:
    """逐位置判定，产出通过阈值的**共识档**碱基替换调用（``prediction="consensus"``）。

    参数：
        reference: 参考集合。
        source: SAM 文件路径，或一堆 :class:`SamRecord`。
        rates: 预建的错误率表。``None`` 时先用同一份数据建表（会在数据上多扫一遍）。
            建表与判定用**不同**的数据集时（例如拿一份纯匹配数据校准、再判另一份），
            显式传进来即可。
        settings: 判定阈值。
        default_quality: ``QUAL`` 为 ``*`` 时使用的质量（见 :func:`iter_pileup`）。
        trimming: read 端裁剪表（见 :func:`build_trimming`）。给了它，被裁的末端碱基既不进
            错误率表、也不参与似然。**默认 ``None`` 表示不裁剪**——上游默认是裁剪的，
            这里不偷偷替调用方做决定（要裁剪就显式传，或者用比对器写出的 SAM 时自己建表）。
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
        score = score_position(column, rates)
        if score is None or not score.is_variant:
            continue
        consensus_score = score.best_ratio - length_penalty
        if consensus_score < settings.e_value_cutoff:
            continue
        if score.frequency < settings.frequency_cutoff:
            continue
        yield _to_call(score, consensus_score, "consensus")
