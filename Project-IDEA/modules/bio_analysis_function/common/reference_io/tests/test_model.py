"""数据模型（序列 / 位置 / 特征）的确定性用例。

这些用例不碰文件系统，只盯模型自己的不变式：坐标校验、链方向、按位置取序列，
以及 ``complement(join(...))`` 的段序语义——后者是最容易被写反的地方，单独钉住。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Feature,
    Location,
    Part,
    ReferenceFormatError,
    ReferenceSequence,
    ReferenceSet,
)

#: 与实现**互相独立**的反向互补，用于校验 extract 的结果（不用被测代码自己的工具）。
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _revcomp(text: str) -> str:
    return text.translate(_COMPLEMENT)[::-1]


def test_part_rejects_bad_coordinates() -> None:
    with pytest.raises(ReferenceFormatError):
        Part(start=0, end=10)
    with pytest.raises(ReferenceFormatError):
        Part(start=10, end=5)
    with pytest.raises(ReferenceFormatError):
        Part(start=1, end=10, strand=0)


def test_part_length_is_inclusive() -> None:
    assert Part(start=1, end=1).length == 1
    assert Part(start=10, end=20).length == 11


def test_location_basics() -> None:
    location = Location(parts=(Part(30, 40), Part(10, 20)), operator="join", raw="join(30..40,10..20)")
    assert location.start == 10
    assert location.end == 40
    assert location.length == 22
    assert location.strand == 1
    assert location.mixed_strand is False
    assert location.spans() == ((30, 40), (10, 20))
    assert location.complete is True


def test_location_rejects_empty_parts() -> None:
    with pytest.raises(ReferenceFormatError):
        Location(parts=())
    with pytest.raises(ReferenceFormatError):
        Location(parts=(Part(1, 2),), operator="concat")


def test_location_strand_raises_on_mixed_parts() -> None:
    location = Location(parts=(Part(1, 5, 1), Part(10, 15, -1)), operator="join")
    assert location.mixed_strand is True
    with pytest.raises(ReferenceFormatError):
        _ = location.strand


def test_location_overlaps() -> None:
    location = Location(parts=(Part(10, 20), Part(100, 110)), operator="join")
    assert location.overlaps(15, 16) is True
    assert location.overlaps(20, 100) is True
    assert location.overlaps(21, 99) is False
    assert location.overlaps(111, 200) is False


def test_extract_single_strand() -> None:
    sequence = "ACGTACGTAC"
    location = Location(parts=(Part(2, 5),), operator="single")
    assert location.extract(sequence) == "CGTA"


def test_extract_reverse_strand_takes_reverse_complement() -> None:
    sequence = "ACGTACGTAC"
    location = Location(parts=(Part(2, 5, -1),), operator="single")
    assert location.extract(sequence) == _revcomp(sequence[1:5])


def test_extract_join_keeps_biological_order() -> None:
    sequence = "AAAACCCCGGGGTTTT"
    location = Location(parts=(Part(1, 4), Part(13, 16)), operator="join")
    assert location.extract(sequence) == "AAAATTTT"


def test_extract_reverse_join_reverses_part_order() -> None:
    """``complement(join(a,b))`` 必须等于整体反向互补，而不是逐段反向互补后顺序不动。

    解析器把 ``complement(join(a,b))`` 变成"段序颠倒 + 每段链翻转"，因此这里直接
    用等价的位置表达来守这条语义。
    """
    sequence = "AAAACCCCGGGGTTTT"
    forward = Location(parts=(Part(1, 4), Part(13, 16)), operator="join", raw="join(1..4,13..16)")
    reverse = Location(
        parts=(Part(13, 16, -1), Part(1, 4, -1)),
        operator="join",
        raw="complement(join(1..4,13..16))",
    )
    assert reverse.extract(sequence) == _revcomp(forward.extract(sequence))
    assert reverse.extract(sequence) != _revcomp(sequence[0:4]) + _revcomp(sequence[12:16])


def test_extract_rejects_out_of_range() -> None:
    location = Location(parts=(Part(5, 50),), operator="single")
    with pytest.raises(ReferenceFormatError):
        location.extract("ACGT")


def test_feature_accessors() -> None:
    feature = Feature(
        kind="CDS",
        location=Location(parts=(Part(10, 30),), operator="single"),
        qualifiers={"gene": ("lacZ",), "product": ("beta-galactosidase",), "note": ("a", "b")},
        index=3,
    )
    assert feature.gene == "lacZ"
    assert feature.product == "beta-galactosidase"
    assert feature.pseudo is False
    assert feature.value("note") == "a"
    assert feature.values("note") == ("a", "b")
    assert feature.value("missing", "缺") == "缺"


def test_feature_gene_falls_back_to_locus_tag_and_pseudo_flag() -> None:
    feature = Feature(
        kind="CDS",
        location=Location(parts=(Part(1, 9),), operator="single"),
        qualifiers={"locus_tag": ("TAG_1",), "pseudo": ("",)},
        index=0,
    )
    assert feature.gene == "TAG_1"
    assert feature.pseudo is True


def test_reference_sequence_invariants() -> None:
    with pytest.raises(ReferenceFormatError):
        ReferenceSequence(seq_id="", sequence="ACGT")
    with pytest.raises(ReferenceFormatError):
        ReferenceSequence(seq_id="X", sequence="")
    with pytest.raises(ReferenceFormatError):
        ReferenceSequence(seq_id="X", sequence="AC GT")
    with pytest.raises(ReferenceFormatError):
        ReferenceSequence(seq_id="X", sequence="acgt")


def test_reference_sequence_rejects_feature_beyond_end() -> None:
    feature = Feature(
        kind="CDS",
        location=Location(parts=(Part(1, 99),), operator="single"),
        index=0,
    )
    with pytest.raises(ReferenceFormatError):
        ReferenceSequence(seq_id="X", sequence="ACGT" * 10, features=(feature,))


def test_reference_sequence_subsequence_and_extract() -> None:
    sequence = ReferenceSequence(seq_id="X", sequence="ACGTACGTAC", circular=True)
    assert sequence.length == 10
    assert sequence.subsequence(1, 4) == "ACGT"
    assert sequence.extract(Location(parts=(Part(3, 6, -1),), operator="single")) == _revcomp(
        sequence.sequence[2:6]
    )
    with pytest.raises(ReferenceFormatError):
        sequence.subsequence(0, 3)
    with pytest.raises(ReferenceFormatError):
        sequence.subsequence(5, 99)


def test_reference_sequence_features_of_filters_by_kind() -> None:
    cds = Feature(kind="CDS", location=Location(parts=(Part(1, 3),)), index=0)
    trna = Feature(kind="tRNA", location=Location(parts=(Part(4, 6),)), index=1)
    sequence = ReferenceSequence(seq_id="X", sequence="ACGTAC", features=(cds, trna))
    assert sequence.features_of() == (cds, trna)
    assert sequence.features_of("tRNA") == (trna,)
    assert sequence.features_of("rRNA") == ()


def test_reference_set_basics() -> None:
    first = ReferenceSequence(seq_id="chr", sequence="ACGT")
    second = ReferenceSequence(seq_id="plasmid", sequence="ACGTAC")
    reference = ReferenceSet.of([first, second])
    assert len(reference) == 2
    assert reference.ids == ("chr", "plasmid")
    assert reference.total_length == 10
    assert reference.feature_count == 0
    assert "chr" in reference
    assert reference.get("plasmid") is second
    assert [item.seq_id for item in reference] == ["chr", "plasmid"]


def test_reference_set_rejects_empty_and_duplicates() -> None:
    with pytest.raises(ReferenceFormatError):
        ReferenceSet(sequences=())
    duplicate = ReferenceSequence(seq_id="chr", sequence="ACGT")
    with pytest.raises(ReferenceFormatError):
        ReferenceSet(sequences=(duplicate, duplicate))


def test_reference_set_get_missing_reports_available_ids() -> None:
    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence="ACGT")])
    with pytest.raises(ReferenceFormatError) as error:
        reference.get("plasmid")
    assert "chr" in str(error.value)


def test_reference_set_features_of_spans_all_sequences() -> None:
    feature = Feature(kind="CDS", location=Location(parts=(Part(1, 3),)), index=0)
    reference = ReferenceSet.of(
        [
            ReferenceSequence(seq_id="chr", sequence="ACGTAC", features=(feature,)),
            ReferenceSequence(seq_id="plasmid", sequence="ACGTAC"),
        ]
    )
    assert reference.features_of("CDS") == (feature,)
