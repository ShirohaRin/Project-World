"""替换效应的定点用例 + 真实参考对拍。

期望值全部能手算：参考与 CDS 都手写，密码子直接读出来比对。两条重点：

- **负链基因**：密码子是编码方向的，而替换两侧是参考正链口径的——写进密码子前必须取互补，
  写错的话负链基因的氨基酸会算错（这组用例就是为了钉住它）。
- **跨复制原点的 ``join`` CDS**：参考坐标与 CDS 内下标不是同一个数，映射必须对。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Feature,
    Location,
    Part,
    ReferenceSequence,
    ReferenceSet,
    read_genbank,
)
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.mutation_annotation import (
    GeneModel,
    Substitution,
    annotate_substitution,
    coding_offset,
    translate_features,
)

_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)
_SEQ_ID = "chr"
_EFFECT_KINDS = {
    "synonymous",
    "nonsynonymous",
    "nonsense",
    "stop_lost",
    "start_codon_change",
    "start_lost",
}


def _feature(kind: str, location: Location, **qualifiers: str) -> Feature:
    return Feature(
        kind=kind,
        location=location,
        qualifiers={key: (value,) for key, value in qualifiers.items()},
    )


def _cds(start: int, end: int, *, strand: int = 1, **qualifiers: str) -> Feature:
    return _feature(
        "CDS", Location(parts=(Part(start, end, strand=strand),), operator="single"), **qualifiers
    )


def _references(*sequences: ReferenceSequence) -> ReferenceSet:
    return ReferenceSet.of(sequences)


#: 正链参考：CDS 在 3..11，密码子是 ATG / GCT / TAA。
_PLUS = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence="CC" + "ATGGCTTAA" + "AAAA",
    features=(_cds(3, 11, gene="plus", product="test protein"),),
)

#: 含终止密码子情形的参考：ATG / TGG / TAA。
_STOP = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence="CC" + "ATGTGGTAA",
    features=(_cds(3, 11, gene="stopgene"),),
)

#: 负链参考：整条序列就是 CDS 的反向互补。
_MINUS_CODING = "ATGGCTTAA"
_MINUS = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence=reverse_complement(_MINUS_CODING),
    features=(_cds(1, 9, strand=-1, gene="minus"),),
)

#: 跨"复制原点"的 join CDS：18..20 在前、1..6 在后，拼起来正好是 ATGGCTTAA。
_SPANNING = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence="GCTTAA" + "C" * 11 + "ATG",
    features=(
        _feature(
            "CDS",
            Location(
                parts=(Part(18, 20, strand=1), Part(1, 6, strand=1)),
                operator="join",
                raw="join(18..20,1..6)",
            ),
            gene="spanning",
        ),
    ),
)

#: 重叠基因：位置 7-9 同属两个 CDS（对 first 是第 3 个密码子，对 second 是起始密码子）。
_OVERLAP = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence="ATGGCT" + "ATG" + "TAA",
    features=(_cds(1, 9, gene="first"), _cds(7, 12, gene="second")),
)


# ---------------------------------------------------------------------------
# 参考坐标 ↔ CDS 内下标
# ---------------------------------------------------------------------------


def test_coding_offset_on_a_plus_strand_span() -> None:
    location = Location(parts=(Part(3, 11, strand=1),), operator="single")

    assert coding_offset(location, 3) == 0
    assert coding_offset(location, 5) == 2
    assert coding_offset(location, 11) == 8
    assert coding_offset(location, 2) is None
    assert coding_offset(location, 12) is None


def test_coding_offset_flips_inside_a_minus_strand_span() -> None:
    location = Location(parts=(Part(3, 11, strand=-1),), operator="single")

    assert coding_offset(location, 11) == 0  # 段里最后一个碱基在编码方向排第一
    assert coding_offset(location, 7) == 4
    assert coding_offset(location, 3) == 8
    assert coding_offset(location, 12) is None


def test_coding_offset_walks_multiple_spans_in_order() -> None:
    joined = Location(parts=(Part(1, 3, strand=1), Part(6, 8, strand=1)), operator="join")
    assert [coding_offset(joined, position) for position in (1, 3, 6, 8)] == [0, 2, 3, 5]
    assert coding_offset(joined, 4) is None

    # complement(join(1..3,6..8))：段序颠倒、每段链翻转（模型就是这么存的）
    complemented = Location(
        parts=(Part(6, 8, strand=-1), Part(1, 3, strand=-1)), operator="join"
    )
    assert [coding_offset(complemented, position) for position in (8, 6, 3, 1)] == [0, 2, 3, 5]


# ---------------------------------------------------------------------------
# 正链上的各种效应
# ---------------------------------------------------------------------------


def test_nonsynonymous_substitution() -> None:
    effects = annotate_substitution(
        _references(_PLUS), Substitution(_SEQ_ID, 6, "G", "A")
    )

    assert len(effects) == 1
    effect = effects[0]
    assert (effect.gene, effect.product, effect.strand) == ("plus", "test protein", 1)
    assert (effect.position, effect.reference_base, effect.call_base) == (6, "G", "A")
    assert (effect.nucleotide_index, effect.codon_index, effect.codon_count) == (4, 2, 3)
    assert (effect.reference_codon, effect.call_codon) == ("GCT", "ACT")
    assert (effect.reference_residue, effect.call_residue) == ("A", "T")
    assert effect.effect == "nonsynonymous"
    assert effect.is_synonymous is False
    assert effect.description == "A2T (GCT→ACT)"


def test_synonymous_substitution() -> None:
    effect = annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 8, "T", "C"))[0]
    assert effect.effect == "synonymous"
    assert effect.is_synonymous is True
    assert effect.description == "A2A (GCT→GCC)"


def test_nonsense_substitution() -> None:
    # _STOP 的 CDS 是 ATG(3-5) TGG(6-8) TAA(9-11)：改第 7 位（TGG 的第 2 位）→ TAG
    effect = annotate_substitution(_references(_STOP), Substitution(_SEQ_ID, 7, "G", "A"))[0]
    assert (effect.reference_codon, effect.call_codon) == ("TGG", "TAG")
    assert effect.effect == "nonsense"
    assert effect.description == "W2* (TGG→TAG)"


def test_stop_codon_replaced_by_another_stop_codon_is_synonymous() -> None:
    effect = annotate_substitution(_references(_STOP), Substitution(_SEQ_ID, 11, "A", "G"))[0]
    assert effect.codon_index == 3
    assert effect.effect == "synonymous"
    assert effect.description == "*3* (TAA→TAG)"


def test_stop_lost_substitution() -> None:
    effect = annotate_substitution(_references(_STOP), Substitution(_SEQ_ID, 9, "T", "C"))[0]
    assert (effect.reference_codon, effect.call_codon) == ("TAA", "CAA")
    assert effect.effect == "stop_lost"
    assert effect.description == "*3Q (TAA→CAA)"


def test_start_codon_change_between_two_start_codons() -> None:
    """ATG→ATA：两边都算起始，所以两侧残基都是 M——上游写成 ``M1M`` 就是这个原因。"""
    effect = annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 5, "G", "A"))[0]
    assert effect.codon_index == 1
    assert effect.effect == "start_codon_change"
    assert effect.description == "M1M (ATG→ATA)"


def test_start_lost_substitution() -> None:
    effect = annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 4, "T", "C"))[0]
    assert (effect.reference_codon, effect.call_codon) == ("ATG", "ACG")
    assert effect.effect == "start_lost"
    assert (effect.reference_residue, effect.call_residue) == ("M", "T")
    assert effect.description == "M1T (ATG→ACG)"


# ---------------------------------------------------------------------------
# 负链与跨原点
# ---------------------------------------------------------------------------


def test_negative_strand_substitution_uses_the_complement() -> None:
    """负链基因的第 1 个密码子：改编码方向的第 2 位（参考位置 8，参考碱基 A）→ ATG 变 ACG。"""
    effect = annotate_substitution(
        _references(_MINUS), Substitution(_SEQ_ID, 8, "A", "G")
    )[0]

    assert effect.strand == -1
    assert effect.nucleotide_index == 2
    assert effect.codon_index == 1
    assert (effect.reference_codon, effect.call_codon) == ("ATG", "ACG")
    assert effect.effect == "start_lost"


def test_negative_strand_nonsynonymous_substitution() -> None:
    """编码方向第 2 个密码子的第 2 位 = 参考位置 5，参考碱基 C 的互补是 G；GCT 变 GAT。"""
    effect = annotate_substitution(
        _references(_MINUS), Substitution(_SEQ_ID, 5, "G", "T")
    )[0]

    assert effect.strand == -1
    assert (effect.nucleotide_index, effect.codon_index) == (5, 2)
    assert (effect.reference_codon, effect.call_codon) == ("GCT", "GAT")
    assert effect.description == "A2D (GCT→GAT)"


def test_substitution_spanning_the_origin_is_mapped_correctly() -> None:
    # 位置 19 在 join 的第一段（18..20）里，是 CDS 的第 2 个碱基
    first = annotate_substitution(_references(_SPANNING), Substitution(_SEQ_ID, 19, "T", "C"))[0]
    assert (first.nucleotide_index, first.codon_index) == (2, 1)
    assert (first.reference_codon, first.call_codon) == ("ATG", "ACG")
    assert first.effect == "start_lost"

    # 位置 2 在第二段（1..6）里，是 CDS 的第 5 个碱基
    second = annotate_substitution(_references(_SPANNING), Substitution(_SEQ_ID, 2, "C", "A"))[0]
    assert (second.nucleotide_index, second.codon_index) == (5, 2)
    assert (second.reference_codon, second.call_codon) == ("GCT", "GAT")
    assert second.effect == "nonsynonymous"


# ---------------------------------------------------------------------------
# 重叠基因与编码区外
# ---------------------------------------------------------------------------


def test_overlapping_genes_each_get_their_own_effect() -> None:
    """同一个碱基在两个基因里意义不同：对 first 是内部密码子，对 second 是起始密码子。"""
    effects = annotate_substitution(_references(_OVERLAP), Substitution(_SEQ_ID, 8, "T", "C"))

    assert [effect.gene for effect in effects] == ["first", "second"]
    assert [effect.codon_index for effect in effects] == [3, 1]
    assert [effect.effect for effect in effects] == ["nonsynonymous", "start_lost"]
    assert [effect.description for effect in effects] == [
        "M3T (ATG→ACG)",
        "M1T (ATG→ACG)",
    ]


def test_positions_outside_any_coding_region_yield_no_effect() -> None:
    assert annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 1, "C", "A")) == ()
    assert annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 14, "A", "C")) == ()


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_substitution_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="位置必须"):
        Substitution(_SEQ_ID, 0, "A", "C")
    with pytest.raises(ValueError, match="参考碱基"):
        Substitution(_SEQ_ID, 1, "N", "C")
    with pytest.raises(ValueError, match="判定碱基"):
        Substitution(_SEQ_ID, 1, "A", "AA")
    with pytest.raises(ValueError, match="这不是一个变异"):
        Substitution(_SEQ_ID, 1, "A", "A")


def test_substitution_from_zero_based_shifts_the_coordinate() -> None:
    substitution = Substitution.from_zero_based(_SEQ_ID, 5, "G", "A")
    assert substitution.position == 6


def test_annotation_rejects_out_of_range_and_mismatched_bases() -> None:
    with pytest.raises(ValueError, match="超出"):
        annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 99, "A", "C"))
    with pytest.raises(ValueError, match="不一致"):
        annotate_substitution(_references(_PLUS), Substitution(_SEQ_ID, 4, "G", "A"))
    with pytest.raises(ValueError, match="没有 SEQ_ID"):
        annotate_substitution(_references(_PLUS), Substitution("other", 4, "T", "A"))


# ---------------------------------------------------------------------------
# 模型复用
# ---------------------------------------------------------------------------


def test_gene_model_is_built_once_and_reused() -> None:
    model = GeneModel.build(_references(_PLUS))

    assert len(model.records) == 1
    assert model.skipped_features == 0
    assert model.annotate(Substitution(_SEQ_ID, 6, "G", "A"))[0].description == "A2T (GCT→ACT)"
    assert model.annotate(Substitution(_SEQ_ID, 5, "G", "A"))[0].effect == "start_codon_change"


def test_gene_model_reports_mixed_strand_features_it_skips() -> None:
    mixed = ReferenceSequence(
        seq_id=_SEQ_ID,
        sequence="AAATGGCTTAA",
        features=(
            _feature(
                "CDS",
                Location(
                    parts=(Part(1, 3, strand=1), Part(6, 8, strand=-1)),
                    operator="join",
                    raw="join(1..3,complement(6..8))",
                ),
            ),
        ),
    )
    model = GeneModel.build(_references(mixed))

    assert model.records == ()
    assert model.skipped_features == 1


# ---------------------------------------------------------------------------
# 真实参考对拍
# ---------------------------------------------------------------------------


def _position_of_offset(location: Location, offset: int) -> int:
    """CDS 内第 ``offset`` 个碱基（0-based，编码方向）对应的参考坐标——:func:`coding_offset` 的反向。"""
    for part in location.parts:
        if offset < part.length:
            return part.start + offset if part.strand == 1 else part.end - offset
        offset -= part.length
    raise AssertionError("offset 超出这个位置的范围")


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_phix174_second_codon_of_every_cds_is_annotated() -> None:
    """phiX174：对每个 CDS 的第 2 个密码子第 1 位造一次替换，注释必须落回同一个基因。

    期望值由数据自身推出（密码子取自该 CDS 的编码序列），因此这条测试同时覆盖了
    **跨复制原点的 join`**（基因 A 等）与多段位置的坐标映射。
    """
    references = read_genbank(_REAL_GENBANK)
    target = references.get("NC_001422")
    model = GeneModel.build(references)

    translations = translate_features(target)
    assert len(translations) == 11

    for translation, feature in zip(translations, target.features_of("CDS")):
        position = _position_of_offset(feature.location, 3)  # 第 2 个密码子的第 1 位
        reference_base = target.sequence[position - 1]
        call_base = next(base for base in "ACGT" if base != reference_base)
        effects = model.annotate(
            Substitution(target.seq_id, position, reference_base, call_base)
        )

        matching = [effect for effect in effects if effect.gene == translation.gene]
        assert matching, f"{translation.gene} 第 2 个密码子的变异没被注释到"
        for effect in matching:
            assert effect.effect in _EFFECT_KINDS
            assert effect.seq_id == target.seq_id
            assert (effect.nucleotide_index, effect.codon_index) == (4, 2)
            assert effect.reference_codon == translation.sequence[3:6]
