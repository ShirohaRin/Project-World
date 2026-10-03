"""变异 → 密码子与氨基酸效应（分片 B）。

给定"参考某个位置上的碱基被换成了另一个碱基"，回答：它落在哪个 CDS、是第几个密码子、
密码子怎么变、氨基酸怎么变、属于哪一类效应。上游 breseq 报告里的那一列就是它：

```text
A380,188  A→C  F239L (TTT→TTG)
C→A            R455S (CGC→AGC)
T→A            M1M (ATG→ATA)        ← 起始密码子改变
C→T            *3S (TAA→TCA)        ← 终止密码子丢失
```

**效应取值**（本实现定义的集合，是对上游口径的重建，见文档「已知限制」）：

| 取值 | 含义 |
| --- | --- |
| ``synonymous`` | 同义：氨基酸没变（含"终止密码子换成另一个终止密码子"） |
| ``nonsynonymous`` | 错义：氨基酸变了，且都不是终止子 |
| ``nonsense`` | 无义：新密码子成了终止密码子 |
| ``stop_lost`` | 原本的终止密码子被破坏，蛋白会读通下去 |
| ``start_codon_change`` | 起始密码子换成了另一个起始密码子（残基仍是 ``M``） |
| ``start_lost`` | 起始密码子不再是起始密码子 |

两处容易错、专门处理的地方：

1. **参考坐标 → CDS 内下标**。CDS 可能是负链、可能是 ``join``（跨复制原点就是这样写的），
   "参考上第几个碱基"与"CDS 里的第几个碱基"不是同一个数。:func:`coding_offset` 负责这层映射：
   按位置逐段走，段内偏移在负链上要**翻转**（因为按位置取序列时该段被反向互补了）。
2. **重叠基因**。细菌基因组里同一个碱基属于两个基因是常态（phiX174 就大量重叠），所以一次
   替换的注释结果是**元组**而不是单个值——上游报告里也常见一条变异挂两三个基因。

**负链基因要取互补**。替换的两侧（``reference_base`` / ``call_base``）是**参考正链**口径的
（与 SAM / VCF 一致）；而负链基因的密码子是**编码方向**的（那一段被反向互补过）。所以把
判定碱基写进密码子之前要先取互补，否则负链基因的氨基酸改变会算错——这个坑有测试钉住。

**这一片只处理碱基替换**。缺失/插入对读码框的影响（移码、``coding (n/m nt)``、``Δ16 bp``）
是另一套语义，留给后续分片。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...common.reference_io import Feature, Location, ReferenceSet
from .translation import START_CODONS, CdsTranslation, cds_translation, translate

__all__ = [
    "CodingEffect",
    "GeneModel",
    "Substitution",
    "annotate_substitution",
    "coding_offset",
]

#: 可判读的碱基（替换的两侧都必须是其中之一）。
_BASES = "ACGT"
#: 单碱基互补表（替换两侧已校验是 ACGT，所以这张小表够用）。
_COMPLEMENT = str.maketrans("ACGT", "TGCA")

#: 效应取值。
EffectKind = Literal[
    "synonymous",
    "nonsynonymous",
    "nonsense",
    "stop_lost",
    "start_codon_change",
    "start_lost",
]


@dataclass(frozen=True, slots=True)
class Substitution:
    """一次碱基替换。

    ``position`` 是 **1-based 参考坐标**（与 `common/reference_io` 同一口径）；
    共识碱基调用给的是 0-based，用 :meth:`from_zero_based` 转一下，避免自己手工 ±1 出错。
    """

    seq_id: str
    position: int
    reference_base: str
    call_base: str

    def __post_init__(self) -> None:
        if not self.seq_id:
            raise ValueError("替换缺少参考序列名。")
        if self.position < 1:
            raise ValueError(f"位置必须 ≥ 1（1-based），当前为 {self.position}。")
        for name, base in (("参考碱基", self.reference_base), ("判定碱基", self.call_base)):
            if len(base) != 1 or base not in _BASES:
                raise ValueError(f"{name} 必须是 ACGT 中的一个字符，当前为 {base!r}。")
        if self.reference_base == self.call_base:
            raise ValueError(
                f"替换前后都是 {self.reference_base}，这不是一个变异（{self.seq_id}:{self.position}）。"
            )

    @classmethod
    def from_zero_based(
        cls, seq_id: str, position: int, reference_base: str, call_base: str
    ) -> Substitution:
        """由 0-based 坐标构造（共识碱基调用的输出就是这个口径）。"""
        return cls(
            seq_id=seq_id,
            position=position + 1,
            reference_base=reference_base,
            call_base=call_base,
        )


@dataclass(frozen=True, slots=True)
class CodingEffect:
    """一次替换落在某个 CDS 里造成的效应。"""

    seq_id: str
    gene: str
    product: str
    strand: int
    position: int
    reference_base: str
    call_base: str
    #: 在 CDS 核苷酸里的第几位（1-based，含终止密码子）。
    nucleotide_index: int
    #: 第几个密码子（1-based）与该 CDS 的密码子总数。
    codon_index: int
    codon_count: int
    reference_codon: str
    call_codon: str
    reference_residue: str
    call_residue: str
    effect: EffectKind

    @property
    def is_synonymous(self) -> bool:
        """蛋白是否没变（同义，或终止密码子换成了另一个终止密码子）。"""
        return self.effect == "synonymous"

    @property
    def description(self) -> str:
        """报告里那一列：``F239L (TTT→TTG)``。"""
        return (
            f"{self.reference_residue}{self.codon_index}{self.call_residue}"
            f" ({self.reference_codon}→{self.call_codon})"
        )


def coding_offset(location: Location, position: int) -> int | None:
    """参考坐标在"按位置取出的序列"里的下标（0-based）；不在任何一段里返回 ``None``。

    段内偏移在负链上要翻转：``Location.extract`` 对负链段取的是反向互补，段里最后一个碱基
    在取出的序列里排第一。
    """
    offset = 0
    for part in location.parts:
        if part.start <= position <= part.end:
            index = position - part.start
            if part.strand == -1:
                index = part.length - 1 - index
            return offset + index
        offset += part.length
    return None


def _effect_for(
    reference_codon: str, call_codon: str, *, codon_index: int
) -> tuple[EffectKind, str, str]:
    """判定效应并给出两侧残基。

    起始密码子单独处理：它在蛋白里记成 ``M``（见 `translation.py` 的口径），
    所以"换成另一个起始密码子"时两侧残基都是 ``M``——这正是上游写成 ``M1M`` 的原因。
    """
    if codon_index == 1:
        reference_residue = "M" if reference_codon in START_CODONS else translate(reference_codon)
        if call_codon in START_CODONS:
            return "start_codon_change", reference_residue, "M"
        return "start_lost", reference_residue, translate(call_codon)

    reference_residue = translate(reference_codon)
    call_residue = translate(call_codon)
    if reference_residue == "*":
        if call_residue == "*":
            return "synonymous", "*", "*"
        return "stop_lost", "*", call_residue
    if call_residue == "*":
        return "nonsense", reference_residue, "*"
    if reference_residue == call_residue:
        return "synonymous", reference_residue, call_residue
    return "nonsynonymous", reference_residue, call_residue


@dataclass(frozen=True, slots=True)
class _CdsRecord:
    feature: Feature
    translation: CdsTranslation
    codons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeneModel:
    """预编译的注释模型：把参考里的 CDS 一次翻译好，之后逐条变异反复用它。

    批量注释时应当建一次、用很多次（每条变异都重新翻译全部 CDS 是很浪费的）。
    单个变异图省事可以直接用 :func:`annotate_substitution`，它内部就是这样建的。
    """

    references: ReferenceSet
    records: tuple[_CdsRecord, ...]
    #: 因为位置是多段混合链（没法当读码框）而没收进来的特征数。
    skipped_features: int = 0

    @classmethod
    def build(cls, references: ReferenceSet, *kinds: str) -> GeneModel:
        """按参考集合建模型（默认只收 ``CDS``）。"""
        wanted = kinds or ("CDS",)
        records: list[_CdsRecord] = []
        skipped = 0
        for sequence in references:
            for feature in sequence.features_of(*wanted):
                if feature.location.mixed_strand:
                    skipped += 1  # 混合链位置没法当读码框，翻译那一步本来也会报错
                    continue
                translation = cds_translation(feature, sequence)
                translate_until = translation.nucleotide_length - translation.trailing_bases
                codons = tuple(
                    translation.sequence[index : index + 3]
                    for index in range(0, translate_until, 3)
                )
                records.append(
                    _CdsRecord(feature=feature, translation=translation, codons=codons)
                )
        return cls(references=references, records=tuple(records), skipped_features=skipped)

    def annotate(self, substitution: Substitution) -> tuple[CodingEffect, ...]:
        """注释一次替换；落在重叠基因里就返回多条，落在编码区外返回空元组。"""
        sequence = self.references.get(substitution.seq_id)
        if substitution.position > sequence.length:
            raise ValueError(
                f"位置 {substitution.position} 超出 {substitution.seq_id} 的长度 "
                f"{sequence.length}。"
            )
        observed = sequence.sequence[substitution.position - 1]
        if observed != substitution.reference_base:
            raise ValueError(
                f"参考在 {substitution.seq_id}:{substitution.position} 是 {observed}，"
                f"与给出的参考碱基 {substitution.reference_base} 不一致。"
            )

        effects: list[CodingEffect] = []
        for record in self.records:
            if record.translation.seq_id != substitution.seq_id:
                continue
            offset = coding_offset(record.feature.location, substitution.position)
            if offset is None:
                continue
            codon_index = offset // 3 + 1
            if codon_index > len(record.codons):
                continue  # 落在部分 CDS 不完整的那截尾巴上，读码框不确定，不注释
            position_in_codon = offset % 3
            reference_codon = record.codons[codon_index - 1]
            # 密码子是编码方向的：负链基因要把参考正链上的判定碱基先取互补再写进去。
            strand = record.translation.strand
            base_in_coding = (
                substitution.call_base
                if strand == 1
                else substitution.call_base.translate(_COMPLEMENT)
            )
            call_codon = (
                reference_codon[:position_in_codon]
                + base_in_coding
                + reference_codon[position_in_codon + 1 :]
            )
            effect, reference_residue, call_residue = _effect_for(
                reference_codon, call_codon, codon_index=codon_index
            )
            feature = record.feature
            effects.append(
                CodingEffect(
                    seq_id=substitution.seq_id,
                    gene=feature.gene,
                    product=feature.product,
                    strand=strand,
                    position=substitution.position,
                    reference_base=substitution.reference_base,
                    call_base=substitution.call_base,
                    nucleotide_index=offset + 1,
                    codon_index=codon_index,
                    codon_count=len(record.codons),
                    reference_codon=reference_codon,
                    call_codon=call_codon,
                    reference_residue=reference_residue,
                    call_residue=call_residue,
                    effect=effect,
                )
            )
        return tuple(effects)


def annotate_substitution(
    references: ReferenceSet, substitution: Substitution
) -> tuple[CodingEffect, ...]:
    """注释一次替换（内部现建模型）。

    只注释一两条变异时够用；**成批注释请自己建一次 :class:`GeneModel` 再反复调用**
    ``model.annotate(...)``，否则每条变异都会把全部 CDS 重翻一遍。
    """
    return GeneModel.build(references).annotate(substitution)
