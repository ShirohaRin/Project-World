"""证据 → 突变的固定规则（RA 线）。

上游 breseq 把这一步放在"突变预测"里：**证据是证据，突变是突变**，两者之间是一组固定的
规则（算法清单 5.5.2 已经把这件事定性为编排层的事，不单独做算法块）。本模块就是那组规则的
第一段——**RA 线**（`RA → SNP/SUB/短 INS/DEL`）：

| 证据 | 判出的突变 | 本实现 |
| --- | --- | --- |
| `RA` · 共识档（`prediction=consensus`） | `SNP`（碱基替换） | ✓ |
| `RA` · 多态档（`prediction=polymorphism`） | `SNP` | ✓ |
| `RA` · 小 indel | 短 `INS` / `DEL`（≤2 bp） | ✗（共识调用目前只判碱基替换） |
| `RA` · 相邻两个碱基同时变 | `SUB` | ✗（同上） |

MC / JC 那两条证据线的规则（`MC + JC → DEL`、`JC → INS/DEL`、`JC + JC → MOB`、
`JC → AMP`）等接上那两条线时再往这里加——**同一份规则的同一个地方**，不要散到流程各处。

坐标口径：:class:`Variant.position` 是 **1-based**（`.gd` 与参考特征表的口径），
而共识调用的输出是 0-based，转换只在这一处发生。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ...bio_analysis_function.common.genome_diff import (
    GenomeDiffRecord,
    MutationEntry,
    deletion_record,
    insertion_record,
    read_alignment_evidence,
    substitution_record,
)
from ...bio_analysis_function.submodules.consensus_calling import (
    ConsensusCall,
    PolymorphismCall,
)

__all__ = [
    "Variant",
    "merge_variants",
    "mutation_entries",
    "to_diff_record",
    "to_evidence_record",
    "variants_from_calls",
]

#: 本实现目前只产出碱基替换（共识调用与多态档判的都是替换）。
BASE_SUBSTITUTION = "SNP"


@dataclass(frozen=True, slots=True)
class Variant:
    """一条判定出来的突变（编排层内部的口径，1-based）。"""

    type: str
    seq_id: str
    position: int
    #: 参考碱基（`SNP`/`DEL` 用；其它类型写 ``"."``）。
    reference_base: str
    #: 判定碱基（`SNP` 用；`DEL` 写 ``"."``、`INS` 写插入序列）。
    call_base: str
    frequency: float
    #: `consensus`（共识档）或 `polymorphism`（多态档）。
    prediction: str
    #: 打分：共识档是上游那个 `consensus_score`；多态档是**我们的混合模型打分**（不同统计量）。
    score: float
    #: 缺失长度（`DEL` 用；其它类型为 0）。
    length: int = 0
    #: 注释（基因 + 效应 + 变化那句话）；由注释那一步填，没注释时是空串。
    annotation: str = ""

    @property
    def sort_key(self) -> tuple[str, int, str, str]:
        """（参考名、位置、类型、判定碱基）——输出顺序确定，便于两份结果做 diff。"""
        return (self.seq_id, self.position, self.type, self.call_base)

    @property
    def label(self) -> str:
        """人读的一行（报告与日志用）。"""
        if self.type == BASE_SUBSTITUTION:
            return f"{self.reference_base}→{self.call_base}"
        return self.type


def variants_from_calls(
    calls: Iterable[ConsensusCall | PolymorphismCall],
) -> tuple[Variant, ...]:
    """把共识档 / 多态档的调用转成突变（0-based → 1-based 在这里转）。"""
    variants: list[Variant] = []
    for call in calls:
        if isinstance(call, ConsensusCall):
            score = call.consensus_score
        else:
            score = call.score
        variants.append(
            Variant(
                type=BASE_SUBSTITUTION,
                seq_id=call.seq_id,
                position=call.position + 1,
                reference_base=call.reference_base,
                call_base=call.call_base,
                frequency=call.frequency,
                prediction=call.prediction,
                score=score,
            )
        )
    return merge_variants(variants)


def merge_variants(*groups: Iterable[Variant]) -> tuple[Variant, ...]:
    """合并若干组突变：按位置排序，**同一个位置的同一种变化只留一条**。

    去重键是"类型 + 参考名 + 位置 + 判定碱基"——同一位置上的两种不同替换是两条突变，
    而同一位置同一变化被两条证据同时支持时只报一次。
    """
    merged: dict[tuple[str, str, int, str], Variant] = {}
    for group in groups:
        for variant in group:
            merged.setdefault(
                (variant.type, variant.seq_id, variant.position, variant.call_base), variant
            )
    return tuple(sorted(merged.values(), key=lambda item: item.sort_key))


def to_diff_record(
    variant: Variant, *, attributes: Iterable[tuple[str, str]] = ()
) -> GenomeDiffRecord:
    """把一条突变转成 `.gd` 的突发行。"""
    if variant.type == BASE_SUBSTITUTION:
        return substitution_record(
            seq_id=variant.seq_id,
            position=variant.position,
            call_base=variant.call_base,
            frequency=variant.frequency,
            attributes=attributes,
        )
    if variant.type == "DEL":
        if variant.length < 1:
            raise ValueError("缺失突变必须给出缺失长度。")
        return deletion_record(
            seq_id=variant.seq_id,
            position=variant.position,
            length=variant.length,
            frequency=variant.frequency,
            attributes=attributes,
        )
    if variant.type == "INS":
        return insertion_record(
            seq_id=variant.seq_id,
            position=variant.position,
            inserted=variant.call_base,
            frequency=variant.frequency,
            attributes=attributes,
        )
    raise ValueError(f"本实现还写不出 {variant.type!r} 这种突发行。")


def to_evidence_record(variant: Variant) -> GenomeDiffRecord:
    """把一条突变的支持证据写成 `RA` 行。

    两条属性上的纪律：

    - **共识档**的打分就是上游那个 `consensus_score`，照写；
    - **多态档**的打分是我们的**混合模型**打分，与上游的 `polymorphism_score` 不是同一个
      统计量（上游还带 `bias_p_value` 那一套检验，我们没有复刻），所以写在**我们自己的键**
      `mixed_model_score` 下，不去占用上游的键名。
    """
    if variant.prediction == "consensus":
        return read_alignment_evidence(
            seq_id=variant.seq_id,
            position=variant.position,
            reference_base=variant.reference_base,
            call_base=variant.call_base,
            frequency=variant.frequency,
            consensus_score=variant.score,
            prediction="consensus",
        )
    return read_alignment_evidence(
        seq_id=variant.seq_id,
        position=variant.position,
        reference_base=variant.reference_base,
        call_base=variant.call_base,
        frequency=variant.frequency,
        prediction="polymorphism",
        attributes=(("mixed_model_score", f"{variant.score:g}"),),
    )


def mutation_entries(
    variants: Iterable[Variant],
    *,
    attributes_of: Callable[[Variant], Iterable[tuple[str, str]]] | None = None,
) -> tuple[MutationEntry, ...]:
    """把突变装配成 `.gd` 的条目（突变 + 支持它的证据）。

    ``attributes_of`` 用来注入注释属性；不给就用突变自己带的 :attr:`Variant.annotation`
    （写成我们的键 `annotation`）。**键名由用它的那层定**，与 `.gd` 构造层的约定一致
    （那一层不替上游发明键名）。
    """
    entries: list[MutationEntry] = []
    for variant in variants:
        if attributes_of is not None:
            attributes = tuple(attributes_of(variant))
        elif variant.annotation:
            attributes = (("annotation", variant.annotation),)
        else:
            attributes = ()
        entries.append(
            MutationEntry(
                mutation=to_diff_record(variant, attributes=attributes),
                evidence=(to_evidence_record(variant),),
            )
        )
    return tuple(entries)
