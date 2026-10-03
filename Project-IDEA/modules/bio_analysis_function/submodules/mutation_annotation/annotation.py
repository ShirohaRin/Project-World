"""端到端注释表（分片 D）：把变异调用结果 + 特征表拼成一张可核对的表。

前面三片各有各的入口（`effects.py` 判编码区效应、`intergenic.py` 量基因间距离），
这一片把它们**汇到一个分类口径**上，回答"这条变异到底算什么"；顺带补上第三类位置
——落在非 CDS 的基因特征里（tRNA / rRNA 等，`NoncodingEffect`），免得把"落在 tRNA 里"
和"压根没有注释"混为一谈。

**分类口径**（一条变异恰好落到其中一类）：

| ``kind`` | 什么时候 | 结果里带什么 |
| --- | --- | --- |
| ``coding`` | 落在某个 CDS 的读码框里 | `CodingEffect` 一条或数条（重叠基因） |
| ``intergenic`` | 两侧至少有一个基因，且自己不在任何基因内 | `IntergenicEffect` |
| ``noncoding`` | 落在非 CDS 的基因特征里（tRNA / rRNA / tmRNA / ncRNA / misc_RNA） | `NoncodingEffect` |
| ``unannotated`` | 以上都不是（这条参考没有可参照的基因） | 无 |

判定顺序就是上表顺序：编码区优先（重叠基因各自给一条），其次基因间，再次非编码特征。
``description`` 是报告里那一列，``kind == "coding"`` 时取第一条效应的写法；
一个变异展开成表格时**每个效应一行**（重叠基因就占多行）。

``position`` 一律是 **1-based** 参考坐标（与本模块、与 `reference_io` 一致）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...common.reference_io import ReferenceSequence, ReferenceSet
from .effects import CodingEffect, GeneModel, Substitution, coding_offset
from .intergenic import DEFAULT_GENE_KINDS, IntergenicEffect, intergenic_effect

__all__ = [
    "ANNOTATION_COLUMNS",
    "AnnotationKind",
    "NoncodingEffect",
    "VariantAnnotation",
    "annotate_variant",
    "annotate_variants",
    "iter_annotation_rows",
    "write_annotation_table",
]

#: 注释表的列顺序（TSV 的表头）。
ANNOTATION_COLUMNS: tuple[str, ...] = (
    "seq_id",
    "position",
    "reference_base",
    "call_base",
    "kind",
    "gene",
    "effect",
    "description",
    "product",
)

AnnotationKind = Literal["coding", "intergenic", "noncoding", "unannotated"]


@dataclass(frozen=True, slots=True)
class NoncodingEffect:
    """落在非 CDS 基因特征内部的变异（tRNA / rRNA 等）。"""

    seq_id: str
    feature_kind: str
    gene: str
    strand: int
    position: int
    #: 在该特征取出的序列里的第几位（1-based）与该特征总长（碱基数）。
    nucleotide_index: int
    length: int

    @property
    def description(self) -> str:
        """``noncoding (4/8 nt)``。上游对这类位置的写法没有逐条核对过，属重建口径。"""
        return f"noncoding ({self.nucleotide_index}/{self.length} nt)"


@dataclass(frozen=True, slots=True)
class VariantAnnotation:
    """一条变异的完整注释结果。"""

    seq_id: str
    position: int
    reference_base: str
    call_base: str
    kind: AnnotationKind
    coding_effects: tuple[CodingEffect, ...] = ()
    intergenic: IntergenicEffect | None = None
    noncoding: NoncodingEffect | None = None

    @property
    def genes(self) -> tuple[str, ...]:
        """受影响的基因：编码区内的基因，或基因间的两侧邻居（可能重复出现同一个基因）。"""
        if self.coding_effects:
            return tuple(effect.gene for effect in self.coding_effects)
        if self.intergenic is not None:
            return tuple(
                gene for gene in (self.intergenic.left_gene, self.intergenic.right_gene) if gene
            )
        if self.noncoding is not None:
            return (self.noncoding.gene,) if self.noncoding.gene else ()
        return ()

    @property
    def effect(self) -> str:
        """效应名（``kind == "coding"`` 时取第一条）。"""
        if self.coding_effects:
            return self.coding_effects[0].effect
        return self.kind

    @property
    def description(self) -> str:
        """报告里那一列。"""
        if self.coding_effects:
            return self.coding_effects[0].description
        if self.intergenic is not None:
            return self.intergenic.description
        if self.noncoding is not None:
            return self.noncoding.description
        return "unannotated"


def _noncoding_effect(
    reference: ReferenceSequence, position: int, kinds: tuple[str, ...]
) -> NoncodingEffect | None:
    """落在非 CDS 基因特征里时给出特征信息；否则 ``None``。"""
    for feature in reference.features_of(*kinds):
        if feature.kind == "CDS":
            continue  # 编码区由 effects.py 那一侧负责
        offset = coding_offset(feature.location, position)
        if offset is None:
            continue
        strand = next(
            part.strand
            for part in feature.location.parts
            if part.start <= position <= part.end
        )
        return NoncodingEffect(
            seq_id=reference.seq_id,
            feature_kind=feature.kind,
            gene=feature.gene,
            strand=strand,
            position=position,
            nucleotide_index=offset + 1,
            length=sum(part.length for part in feature.location.parts),
        )
    return None


def _annotate_one(
    reference: ReferenceSequence,
    model: GeneModel,
    substitution: Substitution,
    kinds: tuple[str, ...],
) -> VariantAnnotation:
    coding = model.annotate(substitution)
    if coding:
        return VariantAnnotation(
            seq_id=substitution.seq_id,
            position=substitution.position,
            reference_base=substitution.reference_base,
            call_base=substitution.call_base,
            kind="coding",
            coding_effects=coding,
        )

    intergenic = intergenic_effect(reference, substitution.position, kinds=kinds)
    if intergenic is not None:
        return VariantAnnotation(
            seq_id=substitution.seq_id,
            position=substitution.position,
            reference_base=substitution.reference_base,
            call_base=substitution.call_base,
            kind="intergenic",
            intergenic=intergenic,
        )

    noncoding = _noncoding_effect(reference, substitution.position, kinds)
    if noncoding is not None:
        return VariantAnnotation(
            seq_id=substitution.seq_id,
            position=substitution.position,
            reference_base=substitution.reference_base,
            call_base=substitution.call_base,
            kind="noncoding",
            noncoding=noncoding,
        )

    return VariantAnnotation(
        seq_id=substitution.seq_id,
        position=substitution.position,
        reference_base=substitution.reference_base,
        call_base=substitution.call_base,
        kind="unannotated",
    )


def annotate_variant(
    references: ReferenceSet,
    substitution: Substitution,
    *,
    kinds: tuple[str, ...] = DEFAULT_GENE_KINDS,
) -> VariantAnnotation:
    """注释一条变异（内部现建模型）。只注释一两条时够用。"""
    return annotate_variants(references, (substitution,), kinds=kinds)[0]


def annotate_variants(
    references: ReferenceSet,
    substitutions: Iterable[Substitution],
    *,
    kinds: tuple[str, ...] = DEFAULT_GENE_KINDS,
) -> tuple[VariantAnnotation, ...]:
    """成批注释（**模型只建一次**，按输入顺序返回）。"""
    model = GeneModel.build(references)
    return tuple(
        _annotate_one(references.get(substitution.seq_id), model, substitution, kinds)
        for substitution in substitutions
    )


def iter_annotation_rows(
    annotations: Iterable[VariantAnnotation],
) -> Iterator[dict[str, str]]:
    """把注释结果摊成表格行：**一个效应一行**（重叠基因占多行，基因间写两侧邻居）。"""
    for annotation in annotations:
        common = {
            "seq_id": annotation.seq_id,
            "position": str(annotation.position),
            "reference_base": annotation.reference_base,
            "call_base": annotation.call_base,
        }
        if annotation.coding_effects:
            for effect in annotation.coding_effects:
                yield {
                    **common,
                    "kind": annotation.kind,
                    "gene": effect.gene,
                    "effect": effect.effect,
                    "description": effect.description,
                    "product": effect.product,
                }
            continue
        if annotation.intergenic is not None:
            flanking = "/".join(
                gene
                for gene in (annotation.intergenic.left_gene, annotation.intergenic.right_gene)
                if gene
            )
            yield {
                **common,
                "kind": annotation.kind,
                "gene": flanking,
                "effect": annotation.kind,
                "description": annotation.intergenic.description,
                "product": "",
            }
            continue
        if annotation.noncoding is not None:
            yield {
                **common,
                "kind": annotation.kind,
                "gene": annotation.noncoding.gene,
                "effect": annotation.kind,
                "description": annotation.noncoding.description,
                "product": "",
            }
            continue
        yield {
            **common,
            "kind": annotation.kind,
            "gene": "",
            "effect": annotation.kind,
            "description": "unannotated",
            "product": "",
        }


def write_annotation_table(
    path: str | Path,
    annotations: Iterable[VariantAnnotation],
) -> int:
    """把注释表写成 TSV（表头见 :data:`ANNOTATION_COLUMNS`），返回**行数**（不含表头）。

    父目录会自动创建；制表符与换行在字段里会被替换成空格，保证表格不被撑破。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\t".join(ANNOTATION_COLUMNS) + "\n")
        for row in iter_annotation_rows(annotations):
            cells = [row[column].replace("\t", " ").replace("\n", " ") for column in ANNOTATION_COLUMNS]
            handle.write("\t".join(cells) + "\n")
            rows += 1
    return rows
