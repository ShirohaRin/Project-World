"""基因间距离的定点用例。

参考与基因都手写、位置与距离都能手算。重点钉住两件事：

- **符号按转录方向**：同一个位置对正链基因记 ``+``、对负链基因可能也记 ``+``（位置在两者
  之间时符号并不相同）——这类"看起来该相反、其实按链判断"的地方最容易写错。
- **落在基因内（含端点）不算 intergenic**，以及 ``join`` 的多段特征按段参与比较。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Feature,
    Location,
    Part,
    ReferenceSequence,
)
from modules.bio_analysis_function.submodules.mutation_annotation import (
    DEFAULT_GENE_KINDS,
    intergenic_effect,
)

_SEQ_ID = "chr"


def _cds(start: int, end: int, *, strand: int = 1, **qualifiers: str) -> Feature:
    return Feature(
        kind="CDS",
        location=Location(parts=(Part(start, end, strand=strand),), operator="single"),
        qualifiers={key: (value,) for key, value in qualifiers.items()},
    )


def _reference(features, length: int = 60, *, seq_id: str = _SEQ_ID) -> ReferenceSequence:
    return ReferenceSequence(
        seq_id=seq_id, sequence="ACGT" * (length // 4) + "ACGT"[: length % 4], features=tuple(features)
    )


#: 三个基因：left(1-9, +)、mid(20-28, +)、right(40-48, −)。
_THREE_GENES = _reference(
    (
        _cds(1, 9, gene="left"),
        _cds(20, 28, gene="mid"),
        _cds(40, 48, strand=-1, gene="right"),
    )
)


def test_distance_between_two_plus_strand_genes() -> None:
    effect = intergenic_effect(_THREE_GENES, 15)

    assert effect is not None
    assert (effect.left_gene, effect.right_gene) == ("left", "mid")
    assert (effect.left_distance, effect.right_distance) == (6, 5)
    # 左基因正链：变异在它转录下游 → +6；右基因正链：变异在它转录上游 → −5
    assert (effect.left_signed_distance, effect.right_signed_distance) == (6, -5)
    assert effect.is_between_genes is True
    assert effect.description == "intergenic (+6/-5)"


def test_negative_strand_neighbour_gets_the_opposite_sign() -> None:
    effect = intergenic_effect(_THREE_GENES, 35)

    assert effect is not None
    assert (effect.left_gene, effect.right_gene) == ("mid", "right")
    assert (effect.left_distance, effect.right_distance) == (7, 5)
    # 右基因是负链：它的 3' 端在低坐标侧，所以变异在它转录下游 → +5（而不是 −5）
    assert (effect.left_signed_distance, effect.right_signed_distance) == (7, 5)
    assert effect.description == "intergenic (+7/+5)"


def test_after_the_last_gene_only_the_left_neighbour_exists() -> None:
    effect = intergenic_effect(_THREE_GENES, 55)

    assert effect is not None
    assert (effect.left_gene, effect.left_distance) == ("right", 7)
    # 左基因是负链：变异在它的高坐标侧，也就是转录上游 → −7
    assert effect.left_signed_distance == -7
    assert effect.right_gene is None and effect.right_distance is None
    assert effect.is_between_genes is False
    assert effect.description == "intergenic (-7/.)"


def test_before_the_first_gene_only_the_right_neighbour_exists() -> None:
    reference = _reference((_cds(10, 18, gene="first"), _cds(30, 38, gene="second")))
    effect = intergenic_effect(reference, 5)

    assert effect is not None
    assert effect.left_gene is None
    assert (effect.right_gene, effect.right_distance) == ("first", 5)
    assert effect.right_signed_distance == -5  # 正链基因的转录上游
    assert effect.description == "intergenic (./-5)"


def test_positions_inside_a_gene_are_not_intergenic() -> None:
    for position in (1, 3, 9, 20, 28, 40, 48):
        assert intergenic_effect(_THREE_GENES, position) is None, f"位置 {position} 不该算基因间"


def test_reference_without_any_gene_returns_none() -> None:
    assert intergenic_effect(_reference(()), 10) is None


def test_kinds_filter_controls_which_features_count() -> None:
    reference = _reference(
        (
            Feature(kind="misc_feature", location=Location(parts=(Part(20, 28, strand=1),), operator="single")),
            _cds(40, 48, gene="real"),
        )
    )

    # 默认不把 misc_feature 当基因：位置 24 在它里面，但仍算基因间
    effect = intergenic_effect(reference, 24)
    assert effect is not None
    assert (effect.left_gene, effect.right_gene) == (None, "real")

    # 把 misc_feature 也算进来时，它就变成"基因内部"了
    assert intergenic_effect(reference, 24, kinds=("CDS", "misc_feature")) is None


def test_joined_feature_participates_span_by_span() -> None:
    spanning = Feature(
        kind="CDS",
        location=Location(
            parts=(Part(10, 12, strand=1), Part(50, 52, strand=1)),
            operator="join",
            raw="join(10..12,50..52)",
        ),
        qualifiers={"gene": ("spanning",)},
    )
    reference = _reference((spanning, _cds(30, 38, gene="middle")))

    # 位置 20 夹在 spanning 的第一段与 middle 之间
    first = intergenic_effect(reference, 20)
    assert first is not None
    assert (first.left_gene, first.right_gene) == ("spanning", "middle")
    assert (first.left_distance, first.right_distance) == (8, 10)
    assert first.description == "intergenic (+8/-10)"

    # 位置 45 夹在 middle 与 spanning 的第二段之间——同一个基因出现在右侧
    second = intergenic_effect(reference, 45)
    assert second is not None
    assert (second.left_gene, second.right_gene) == ("middle", "spanning")
    assert (second.left_distance, second.right_distance) == (7, 5)


def test_out_of_range_position_raises() -> None:
    with pytest.raises(ValueError, match="超出"):
        intergenic_effect(_THREE_GENES, 61)
    with pytest.raises(ValueError, match="超出"):
        intergenic_effect(_THREE_GENES, 0)


def test_default_gene_kinds_cover_the_usual_annotation_types() -> None:
    assert DEFAULT_GENE_KINDS == ("CDS", "tRNA", "rRNA", "tmRNA", "ncRNA", "misc_RNA")
