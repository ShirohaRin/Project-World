"""GenBank 读取的确定性用例 + 一份真实公开参考。

合成记录是**程序化生成**的（固定随机种子 → 每次跑完全一样），这样坐标与期望值
永远对得上，也不会把几百行碱基手抄进测试里。真实数据用的是 phiX174 全基因组
（见 ``tests/data/`` 与《测试数据资源登记》），用来验证解析器在真实文件上不崩、
长度与拓扑读得对、注释都落在序列范围内。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Part,
    ReferenceFormatError,
    parse_genbank,
    parse_location,
    read_genbank,
)

_SEED = 20260923
_LENGTH = 300
#: 与实现互相独立的反向互补，用于校验 extract 的结果。
_COMPLEMENT = str.maketrans("ACGT", "TGCA")

_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)


def _revcomp(text: str) -> str:
    return text.translate(_COMPLEMENT)[::-1]


def _synthetic_sequence(length: int = _LENGTH) -> str:
    generator = random.Random(_SEED)
    return "".join(generator.choice("ACGT") for _ in range(length))


def _origin_block(sequence: str, width: int = 60) -> str:
    return "\n".join(
        f"{start + 1:>9} {sequence[start:start + width].lower()}"
        for start in range(0, len(sequence), width)
    )


def _feature(key: str, location: str, *qualifiers: str) -> str:
    """按 GenBank 的列位拼一个特征块：特征名自第 6 列起，位置自第 22 列起。"""
    lines = [f"     {key:<16}{location}"]
    lines.extend(f"{'':21}/{qualifier}" for qualifier in qualifiers)
    return "\n".join(lines)


def _record(seq_id: str, sequence: str, topology: str, *features: str) -> str:
    head = [
        f"LOCUS       {seq_id:<22}{len(sequence):>6} bp    DNA     {topology} SYN 01-JAN-2026",
        "DEFINITION  Synthetic test reference for reference_io unit tests.",
        f"ACCESSION   {seq_id}",
        f"VERSION     {seq_id}.1",
        "KEYWORDS    .",
        "SOURCE      synthetic construct",
        "  ORGANISM  synthetic construct",
        "            other sequences; artificial sequences.",
        "FEATURES             Location/Qualifiers",
        f"     source          1..{len(sequence)}",
        '                     /organism="synthetic construct"',
    ]
    return "\n".join(head + list(features)) + f"\nORIGIN\n{_origin_block(sequence)}\n//\n"


_FEATURES = (
    _feature(
        "CDS",
        "10..90",
        'gene="geneA"',
        'locus_tag="TAG_0001"',
        'product="protein A"',
        'note="first"',
        'note="second"',
    ),
    _feature("CDS", "complement(100..180)", 'gene="geneB"', 'product="protein B"'),
    _feature("CDS", "complement(join(190..210,240..260))", 'gene="geneC"'),
    _feature("CDS", "join(211..239,261..290)", 'gene="geneD"'),
    _feature("repeat_region", "290..300", 'mobile_element_type="insertion sequence: IS1"'),
    _feature("mobile_element", "290..300", 'mobile_element_type="IS1"'),
    _feature("tRNA", "95..99", 'product="tRNA-Test"'),
    _feature("CDS", "<1..>40", 'gene="partialGene"', "pseudo"),
    _feature(
        "CDS",
        "complement(join(100..110,\n"
        f"{'':21}200..210))",
        'gene="wrappedGene"',
    ),
)


@pytest.fixture(scope="module")
def sequence() -> str:
    return _synthetic_sequence()


@pytest.fixture(scope="module")
def reference(sequence: str):
    return parse_genbank(_record("TEST_GENOME", sequence, "circular", *_FEATURES))


# ---------------------------------------------------------------------------
# 位置解析
# ---------------------------------------------------------------------------


def test_parse_location_single_position() -> None:
    location = parse_location("357")
    assert location.operator == "single"
    assert location.spans() == ((357, 357),)
    assert location.complete is True


def test_parse_location_range_and_partial_markers() -> None:
    assert parse_location("123..456").spans() == ((123, 456),)
    assert parse_location("<123..456").parts[0].partial is True
    assert parse_location("123..>456").parts[0].partial is True
    assert parse_location("123..456").complete is True


def test_parse_location_complement_and_join() -> None:
    assert parse_location("complement(1..10)").spans() == ((1, 10),)
    assert parse_location("complement(1..10)").strand == -1
    joined = parse_location("join(1..10,20..30)")
    assert joined.operator == "join"
    assert joined.spans() == ((1, 10), (20, 30))


def test_parse_location_complement_of_join_reverses_part_order() -> None:
    """最关键的一条：``complement(join(a,b))`` 要变成 ``[revcomp(b), revcomp(a)]``。"""
    location = parse_location("complement(join(190..210,240..260))")
    assert location.parts == (Part(240, 260, -1, False), Part(190, 210, -1, False))
    assert location.spans() == ((240, 260), (190, 210))


def test_parse_location_join_of_complement_keeps_order_and_mixes_strands() -> None:
    location = parse_location("join(complement(1..10),20..30)")
    assert location.spans() == ((1, 10), (20, 30))
    assert location.mixed_strand is True
    with pytest.raises(ReferenceFormatError):
        _ = location.strand


def test_parse_location_order_operator_and_trailing_comma() -> None:
    assert parse_location("order(1..5,10..15)").operator == "order"
    assert parse_location("join(1..10,)").spans() == ((1, 10),)


def test_parse_location_rejects_unsupported_or_broken_writing() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_location("")
    with pytest.raises(ReferenceFormatError):
        parse_location("1^2")
    with pytest.raises(ReferenceFormatError):
        parse_location("abc")
    with pytest.raises(ReferenceFormatError):
        parse_location("join(1..10,20..30")
    with pytest.raises(ReferenceFormatError):
        parse_location("complement(1..10")


# ---------------------------------------------------------------------------
# 合成记录
# ---------------------------------------------------------------------------


def test_record_header_and_sequence(reference, sequence: str) -> None:
    assert reference.ids == ("TEST_GENOME",)
    target = reference.get("TEST_GENOME")
    assert target.length == _LENGTH
    assert target.length == len(sequence)
    assert target.circular is True
    assert target.sequence == sequence.upper()
    assert "Synthetic test reference" in target.description
    assert reference.total_length == _LENGTH


def test_features_are_all_read_and_kinds_are_preserved(reference) -> None:
    target = reference.get("TEST_GENOME")
    kinds = [feature.kind for feature in target.features]
    assert kinds.count("CDS") == 6
    assert "repeat_region" in kinds
    assert "mobile_element" in kinds
    assert "tRNA" in kinds
    assert target.features[0].index == 0
    assert [feature.index for feature in target.features] == list(range(len(kinds)))


def test_forward_feature_coordinates_and_qualifiers(reference, sequence: str) -> None:
    feature = next(item for item in reference.get("TEST_GENOME").features if item.gene == "geneA")
    assert feature.kind == "CDS"
    assert feature.location.start == 10
    assert feature.location.end == 90
    assert feature.location.strand == 1
    assert feature.location.complete is True
    assert feature.product == "protein A"
    assert feature.values("note") == ("first", "second")
    assert reference.get("TEST_GENOME").extract(feature.location) == sequence[9:90]


def test_reverse_feature_is_reverse_complement(reference, sequence: str) -> None:
    feature = next(item for item in reference.get("TEST_GENOME").features if item.gene == "geneB")
    assert feature.location.strand == -1
    assert reference.get("TEST_GENOME").extract(feature.location) == _revcomp(sequence[99:180])


def test_reverse_join_feature_equals_reverse_complement_of_joined_slices(
    reference, sequence: str
) -> None:
    feature = next(item for item in reference.get("TEST_GENOME").features if item.gene == "geneC")
    assert feature.location.raw == "complement(join(190..210,240..260))"
    assert feature.location.spans() == ((240, 260), (190, 210))
    joined = sequence[189:210] + sequence[239:260]
    assert reference.get("TEST_GENOME").extract(feature.location) == _revcomp(joined)


def test_forward_join_feature_concatenates_in_order(reference, sequence: str) -> None:
    feature = next(item for item in reference.get("TEST_GENOME").features if item.gene == "geneD")
    assert reference.get("TEST_GENOME").extract(feature.location) == (
        sequence[210:239] + sequence[260:290]
    )


def test_wrapped_location_line_is_joined(reference, sequence: str) -> None:
    feature = next(
        item for item in reference.get("TEST_GENOME").features if item.gene == "wrappedGene"
    )
    assert feature.location.raw.replace("\n", "") == "complement(join(100..110,200..210))"
    assert feature.location.spans() == ((200, 210), (100, 110))
    assert reference.get("TEST_GENOME").extract(feature.location) == _revcomp(
        sequence[99:110] + sequence[199:210]
    )


def test_partial_and_pseudo_feature(reference) -> None:
    feature = next(
        item for item in reference.get("TEST_GENOME").features if item.gene == "partialGene"
    )
    assert feature.location.complete is False
    assert feature.location.start == 1
    assert feature.location.end == 40
    assert feature.pseudo is True


def test_repeat_and_mobile_element_annotations(reference) -> None:
    repeats = reference.features_of("repeat_region")
    assert len(repeats) == 1
    assert repeats[0].location.spans() == ((290, 300),)
    assert repeats[0].value("mobile_element_type") == "insertion sequence: IS1"
    assert reference.features_of("mobile_element")[0].value("mobile_element_type") == "IS1"


def test_multiple_records_in_one_file(sequence: str) -> None:
    text = _record("CHR", sequence, "circular", _feature("CDS", "1..30", 'gene="a"')) + _record(
        "PLASMID", sequence[:120], "linear", _feature("CDS", "1..30", 'gene="b"')
    )
    reference = parse_genbank(text)
    assert reference.ids == ("CHR", "PLASMID")
    assert reference.total_length == _LENGTH + 120
    assert reference.get("PLASMID").circular is False
    assert len(reference.features_of("CDS")) == 2


# ---------------------------------------------------------------------------
# 报错路径
# ---------------------------------------------------------------------------


def test_protein_record_is_rejected(sequence: str) -> None:
    text = "\n".join(
        [
            f"LOCUS       PROTEIN_TEST            {len(sequence):>6} aa    protein  linear 01-JAN-2026",
            "DEFINITION  a protein record",
            "ORIGIN",
            _origin_block(sequence),
            "//",
            "",
        ]
    )
    with pytest.raises(ReferenceFormatError, match="蛋白"):
        parse_genbank(text)


def test_contig_only_record_is_rejected() -> None:
    text = "\n".join(
        [
            "LOCUS       UNFINISHED              300 bp    DNA     linear 01-JAN-2026",
            "DEFINITION  unfinished genome",
            "CONTIG      join(AAAB01000001.1:1..300)",
            "//",
            "",
        ]
    )
    with pytest.raises(ReferenceFormatError, match="CONTIG"):
        parse_genbank(text)


def test_declared_length_mismatch_is_rejected(sequence: str) -> None:
    text = _record("MISMATCH", sequence, "circular").replace("300 bp", "400 bp", 1)
    with pytest.raises(ReferenceFormatError, match="长度"):
        parse_genbank(text)


def test_invalid_sequence_letter_is_rejected(sequence: str) -> None:
    broken = f"zz{sequence.lower()[2:]}"
    text = _record("BAD_LETTER", sequence, "circular").replace(
        _origin_block(sequence), f"{1:>9} {broken}"
    )
    with pytest.raises(ReferenceFormatError, match="非核酸字母"):
        parse_genbank(text)


def test_empty_and_headerless_input_are_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_genbank("")
    with pytest.raises(ReferenceFormatError):
        parse_genbank("DEFINITION  nothing here\n")


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ReferenceFormatError, match="不存在"):
        read_genbank(tmp_path / "nope.gbk")


def test_gzip_genbank_is_read(tmp_path: Path, sequence: str) -> None:
    path = tmp_path / "ref.gbk.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(_record("GZ", sequence, "circular"))
    reference = read_genbank(path)
    assert reference.get("GZ").length == _LENGTH


# ---------------------------------------------------------------------------
# 真实公开数据
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_genbank_phiX174() -> None:
    reference = read_genbank(_REAL_GENBANK)
    assert reference.ids == ("NC_001422",)
    target = reference.get("NC_001422")
    assert target.length == 5386
    assert target.circular is True
    assert target.sequence.isupper()
    assert "phiX174" in target.description

    coding = target.features_of("CDS")
    assert len(coding) == 11
    for feature in coding:
        assert feature.location.end <= target.length
        assert feature.location.length % 3 == 0
        assert feature.gene or feature.product


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_genbank_origin_spanning_feature() -> None:
    """phiX174 是环状基因组，基因 A 横跨复制原点——真实文件里就是这个写法。"""
    target = read_genbank(_REAL_GENBANK).get("NC_001422")
    spanning = next(
        feature for feature in target.features_of("CDS") if feature.location.operator == "join"
    )
    assert spanning.location.spans() == ((3981, 5386), (1, 136))
    assert spanning.location.strand == 1
    assert spanning.location.length == 1542
    assert target.extract(spanning.location) == (
        target.sequence[3980:5386] + target.sequence[0:136]
    )
