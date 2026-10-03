"""CDS 翻译的定点用例 + 真实参考对拍。

密码表的正确性用**氨基酸简并度**独立核对（教科书上的表，不是抄实现自己的表）；
其余用例手写序列、期望值能手算。真实数据那组把 phiX174 的 11 个 CDS 翻译结果
与 NCBI 自己写在 ``/translation`` 里的蛋白逐条比对——这是最硬的证据。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Feature,
    Location,
    Part,
    ReferenceSequence,
    read_genbank,
)
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.mutation_annotation import (
    CODON_TABLE,
    cds_translation,
    translate,
    translate_cds,
    translate_features,
)

_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)

#: 标准遗传密码里每个氨基酸由几个密码子编码（含三个终止密码子）。用来独立核对密码表。
_DEGENERACY = {
    "F": 2, "L": 6, "I": 3, "M": 1, "V": 4, "S": 6, "P": 4, "T": 4, "A": 4,
    "Y": 2, "*": 3, "H": 2, "Q": 2, "N": 2, "K": 2, "D": 2, "E": 2, "C": 2,
    "W": 1, "R": 6, "G": 4,
}


def _cds_feature(start: int, end: int, *, strand: int = 1, **qualifiers: str) -> Feature:
    return Feature(
        kind="CDS",
        location=Location(parts=(Part(start, end, strand=strand),), operator="single"),
        qualifiers={key: (value,) for key, value in qualifiers.items()},
    )


# ---------------------------------------------------------------------------
# 密码表本身
# ---------------------------------------------------------------------------


def test_codon_table_has_the_textbook_degeneracy() -> None:
    """64 个密码子、每个氨基酸的密码子个数与教科书一致——用简并度独立核对，不抄实现。"""
    assert len(CODON_TABLE) == 64
    assert Counter(CODON_TABLE.values()) == _DEGENERACY
    assert set(CODON_TABLE) == {
        first + second + third
        for first in "TCAG"
        for second in "TCAG"
        for third in "TCAG"
    }


def test_representative_codons_translate_to_the_expected_residues() -> None:
    expected = {
        "ATG": "M", "TGG": "W", "TTT": "F", "GGG": "G", "CAT": "H",
        "CGT": "R", "TTG": "L", "GTG": "V", "TAA": "*", "TAG": "*", "TGA": "*",
    }
    for codon, residue in expected.items():
        assert CODON_TABLE[codon] == residue, f"密码子 {codon} 译错了"


# ---------------------------------------------------------------------------
# 裸序列翻译
# ---------------------------------------------------------------------------


def test_translate_ignores_the_incomplete_tail() -> None:
    assert translate("ATGGCCG") == "MA"  # 尾巴那个 G 不足一个密码子，忽略
    assert translate("ATGGCCGGG") == "MAG"


def test_translate_marks_codons_with_unknown_bases() -> None:
    assert translate("ATGNNN") == "MX"
    assert translate("ATGNAA") == "MX"


def test_translate_stop_symbol_is_configurable() -> None:
    assert translate("TAATAG") == "**"
    assert translate("TAATAG", stop_symbol="!") == "!!"
    assert translate("TAATAG", stop_symbol="") == ""


def test_translate_accepts_lower_case() -> None:
    assert translate("atgTAA") == "M*"


# ---------------------------------------------------------------------------
# CDS 翻译：起始、终止、长度
# ---------------------------------------------------------------------------


def test_complete_cds_translation() -> None:
    result = translate_cds("ATGGCTTAA", gene="g", seq_id="chr")

    assert result.protein == "MA"
    assert result.start_codon == "ATG"
    assert result.stop_codon == "TAA"
    assert result.nucleotide_length == 9
    assert result.residue_count == 2
    assert result.trailing_bases == 0
    assert result.strand == 1
    assert result.partial is False
    assert result.start_is_standard is True
    assert result.has_stop_codon is True
    assert result.has_internal_stop is False


def test_alternative_start_codons_are_translated_as_methionine() -> None:
    """GTG / TTG 作为起始时按 M 译（按标准表它们本来是 V / L）。"""
    for codon, standard_residue in (("GTG", "V"), ("TTG", "L"), ("ATT", "I")):
        assert translate(codon) == standard_residue  # 表里就是这些
        result = translate_cds(f"{codon}GCTTAA")
        assert result.protein == "MA"
        assert result.start_codon == codon
        assert result.start_is_alternative is True
        assert result.start_is_standard is False
        assert result.start_is_unknown is False


def test_a_start_codon_outside_the_known_set_is_flagged_not_hidden() -> None:
    result = translate_cds("AAAGCTTAA")
    assert result.start_codon == "AAA"
    assert result.start_is_unknown is True
    assert result.protein == "KA"  # 不硬塞一个 M，如实按表翻译


def test_complete_cds_without_a_stop_codon_is_reported_not_raised() -> None:
    """有的注释口径不把终止密码子算进 CDS——如实记录，不替它补、也不报错。"""
    result = translate_cds("ATGGCC")
    assert result.protein == "MA"
    assert result.stop_codon == ""
    assert result.has_stop_codon is False


def test_internal_stop_codons_show_up_in_the_protein() -> None:
    result = translate_cds("ATGTAAGCTTAA")
    assert result.protein == "M*A"
    assert result.has_internal_stop is True
    assert result.stop_codon == "TAA"


def test_length_not_a_multiple_of_three_raises_for_complete_cds() -> None:
    with pytest.raises(ValueError, match="3 的倍数"):
        translate_cds("ATGG")


def test_partial_cds_keeps_the_incomplete_tail_count() -> None:
    result = translate_cds("ATGG", partial=True)
    assert result.protein == "M"
    assert result.trailing_bases == 1
    assert result.partial is True


def test_empty_or_too_short_sequences_are_rejected() -> None:
    with pytest.raises(ValueError, match="为空"):
        translate_cds("")
    with pytest.raises(ValueError, match="不足一个密码子"):
        translate_cds("AT", partial=True)


# ---------------------------------------------------------------------------
# 从特征表翻译
# ---------------------------------------------------------------------------


def test_cds_translation_takes_the_sequence_from_the_location() -> None:
    reference = ReferenceSequence(
        seq_id="chr",
        sequence="AAAATGGCTTAA",  # 12 bp：1-3 是填充，4-12 是那个 CDS
        features=(_cds_feature(4, 12, gene="plus"),),
    )
    result = cds_translation(reference.features[0], reference)

    assert result.seq_id == "chr"
    assert result.gene == "plus"
    assert result.protein == "MA"
    assert result.strand == 1


def test_reverse_strand_cds_is_reverse_complemented() -> None:
    coding = "ATGGCTTAA"
    reference = ReferenceSequence(
        seq_id="chr",
        sequence=reverse_complement(coding),
        features=(_cds_feature(1, 9, strand=-1, gene="minus"),),
    )
    result = cds_translation(reference.features[0], reference)

    assert result.sequence == coding  # 负链取出来已经是与蛋白同向的
    assert result.protein == "MA"
    assert result.strand == -1


def test_mixed_strand_feature_is_rejected() -> None:
    reference = ReferenceSequence(seq_id="chr", sequence="AAATGGCTTAA")
    feature = Feature(
        kind="CDS",
        location=Location(
            parts=(Part(1, 3, strand=1), Part(6, 8, strand=-1)),
            operator="join",
            raw="join(1..3,complement(6..8))",
        ),
    )
    with pytest.raises(ValueError, match="链方向不一致"):
        cds_translation(feature, reference)


def test_translate_features_defaults_to_cds_and_can_filter_by_kind() -> None:
    """默认只翻 CDS；``kinds`` 参数能把别的编码类型也拉进来（这里用 mat_peptide 验证筛选生效）。"""
    reference = ReferenceSequence(
        seq_id="chr",
        sequence="AAA" + "ATGGCTTAA" + "GGGGGGGG" + "TTT" + "ATGGCTTAA" + "CCC" + "ATGGCTTAA",
        features=(
            _cds_feature(4, 12, gene="first"),
            Feature(
                kind="mat_peptide",
                location=Location(parts=(Part(24, 32, strand=1),), operator="single"),
            ),
            _cds_feature(36, 44, gene="second"),
        ),
    )

    default = translate_features(reference)
    assert [item.gene for item in default] == ["first", "second"]

    peptides = translate_features(reference, "mat_peptide")
    assert len(peptides) == 1
    assert peptides[0].protein == "MA"

    assert translate_features(reference, "rRNA") == ()  # 没有这种特征就是空的


# ---------------------------------------------------------------------------
# 真实参考对拍
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_phix174_cds_translations_match_the_annotated_proteins() -> None:
    """phiX174 的 11 个 CDS：我们翻译出来的蛋白要与 NCBI 写在 /translation 里的逐条一致。"""
    reference = read_genbank(_REAL_GENBANK)
    target = reference.get("NC_001422")
    translations = translate_features(target)

    assert len(translations) == 11
    for item in translations:
        assert item.nucleotide_length % 3 == 0, f"{item.gene} 的长度不是 3 的倍数"
        assert item.partial is False, f"{item.gene} 标成了部分序列"
        assert item.has_stop_codon, f"{item.gene} 没有终止密码子"
        assert not item.has_internal_stop, f"{item.gene} 蛋白里有内部终止密码子"
        assert item.protein.startswith("M"), f"{item.gene} 的蛋白没有以 M 开头"

    annotated = [feature.value("translation") for feature in target.features_of("CDS")]
    assert annotated and all(annotated), "参考文件里的 /translation 限定符没读到"
    assert [item.protein for item in translations] == annotated
