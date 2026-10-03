"""覆盖度剖面的定点用例：差分数组算得对不对、哪些操作不贡献覆盖、unique 过滤。"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.missing_coverage import (
    build_coverage_profile,
    iter_coverage_profiles,
)

_SEQUENCE = "ACGTACGTAC"


def _reference(*sequences: tuple[str, str]) -> ReferenceSet:
    return ReferenceSet.of(
        ReferenceSequence(seq_id=seq_id, sequence=sequence) for seq_id, sequence in sequences
    )


def _record(
    name: str,
    position: int,
    cigar: str,
    *,
    sequence_length: int | None = None,
    mapq: int = 60,
    reference_name: str = "chr",
    flag: int = 0,
) -> SamRecord:
    """SEQUENCE 长度默认跟着 CIGAR 走（``SamRecord`` 要求两者一致）。"""
    parsed = parse_cigar(cigar)
    if sequence_length is None:
        sequence_length = parsed.query_length
    sequence = "*" if parsed.is_empty else "A" * sequence_length
    qualities = "*" if sequence == "*" else "I" * sequence_length
    return SamRecord(
        query_name=name,
        flag=flag,
        reference_name=reference_name,
        position=position,
        mapping_quality=mapq,
        cigar=parsed,
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities=qualities,
    )


def _depths(records: list[SamRecord], *, min_mapping_quality: int = 1) -> tuple[int, ...]:
    profiles = list(
        iter_coverage_profiles(
            _reference(("chr", _SEQUENCE)), records, min_mapping_quality=min_mapping_quality
        )
    )
    assert len(profiles) == 1
    return profiles[0].depths


def test_a_single_read_covers_its_aligned_span() -> None:
    # POS 是 1-based：POS 2 + 3M → 0-based 位置 1、2、3
    assert _depths([_record("a", 2, "3M")]) == (0, 1, 1, 1, 0, 0, 0, 0, 0, 0)


def test_deletions_and_skips_do_not_contribute_coverage() -> None:
    # 1M(位置0) 2D(位置1、2 没有碱基) 1M(位置3)
    assert _depths([_record("a", 1, "1M2D1M")]) == (1, 0, 0, 1, 0, 0, 0, 0, 0, 0)
    # N 同理：2M(0、1) 3N(2、3、4) 1M(5)
    assert _depths([_record("a", 1, "2M3N1M")]) == (1, 1, 0, 0, 0, 1, 0, 0, 0, 0)


def test_soft_clip_and_insertion_do_not_contribute_coverage() -> None:
    # 2S 没比上（只推进 read），2M 盖住位置 0、1
    assert _depths([_record("a", 1, "2S2M")]) == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
    # 1I 不消费参考：1M(0) 1I 1M(1)
    assert _depths([_record("a", 1, "1M1I1M")]) == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)


def test_edges_of_the_reference_are_zero_filled() -> None:
    profile = build_coverage_profile(
        _reference(("chr", _SEQUENCE)), [_record("a", 5, "2M")], "chr"
    )
    assert profile.length == len(_SEQUENCE)
    assert profile.depths == (0, 0, 0, 0, 1, 1, 0, 0, 0, 0)


def test_only_unique_alignments_count_by_default() -> None:
    records = [_record("unique", 1, "2M", mapq=60), _record("repeat", 1, "2M", mapq=0)]
    assert _depths(records) == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
    assert _depths(records, min_mapping_quality=0) == (2, 2, 0, 0, 0, 0, 0, 0, 0, 0)


def test_unmapped_records_and_missing_cigar_are_skipped() -> None:
    records = [
        _record("unmapped", 1, "2M", flag=4),
        _record("no-cigar", 1, "*"),
        _record("ok", 1, "2M"),
    ]
    assert _depths(records) == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)


def test_depths_accumulate_across_reads() -> None:
    profile = build_coverage_profile(
        _reference(("chr", "ACGT")),
        [_record("a", 1, "3M", sequence_length=3), _record("b", 2, "1M", sequence_length=1)],
        "chr",
    )
    assert profile.depths == (1, 2, 1, 0)
    assert profile.mean == pytest.approx(1.0)
    assert profile.variance == pytest.approx(0.5)
    assert profile.covered == 3
    assert profile.histogram() == {0: 1, 1: 2, 2: 1}


def test_every_reference_sequence_gets_a_profile_in_file_order() -> None:
    reference = _reference(("second", "ACGT"), ("first", "ACGT"))
    records = [
        _record("a", 1, "2M", reference_name="first", sequence_length=2),
        _record("b", 1, "2M", reference_name="second", sequence_length=2),
    ]
    profiles = list(iter_coverage_profiles(reference, records))
    assert [profile.seq_id for profile in profiles] == ["second", "first"]
    # second 只有 b 那条 read；first 只有 a 那条
    assert profiles[0].depths == (1, 1, 0, 0)
    assert profiles[1].depths == (1, 1, 0, 0)


def test_unknown_reference_name_is_an_error() -> None:
    with pytest.raises(ValueError, match="不在参考集合里"):
        _depths([_record("a", 1, "2M", reference_name="plasmid")])


def test_alignment_running_past_the_reference_end_is_an_error() -> None:
    with pytest.raises(ValueError, match="越出参考范围"):
        _depths([_record("a", 9, "4M", sequence_length=4)])


def test_build_single_profile_rejects_unknown_sequence() -> None:
    with pytest.raises(ValueError, match="没有 'plasmid'"):
        build_coverage_profile(_reference(("chr", "ACGT")), [], "plasmid")


def test_negative_min_mapping_quality_is_an_error() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        _depths([], min_mapping_quality=-1)
