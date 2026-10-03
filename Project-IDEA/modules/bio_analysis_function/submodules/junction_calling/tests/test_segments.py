"""分段的定点用例：>2 bp 空位拆段、软剪裁不算、段内插入算、坐标与起点锚。"""

from __future__ import annotations

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.submodules.junction_calling import (
    MIN_SPLIT_GAP,
    AlignmentSegment,
    segments_of,
)

_REVERSE_FLAG = 16


def _record(cigar: str, *, position: int = 1, reverse: bool = False, name: str = "r") -> SamRecord:
    parsed = parse_cigar(cigar)
    sequence = "A" * parsed.query_length
    return SamRecord(
        query_name=name,
        flag=_REVERSE_FLAG if reverse else 0,
        reference_name="chr",
        position=position,
        mapping_quality=60,
        cigar=parsed,
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities="I" * parsed.query_length,
    )


def test_split_threshold_is_two_bases() -> None:
    assert MIN_SPLIT_GAP == 2


def test_a_plain_match_is_one_segment() -> None:
    (segment,) = segments_of(_record("10M", position=5))
    assert (segment.query_start, segment.query_end) == (0, 9)
    assert (segment.reference_start, segment.reference_end) == (4, 13)
    assert segment.query_length == 10
    assert segment.reference_length == 10


def test_a_short_deletion_stays_inside_one_segment() -> None:
    # 2D 不比阈值大：整段还是一段，但参考跨度含那 2 个缺失碱基
    (segment,) = segments_of(_record("5M2D5M", position=5))
    assert (segment.query_start, segment.query_end) == (0, 9)
    assert (segment.reference_start, segment.reference_end) == (4, 15)
    assert segment.reference_length == 12


def test_a_long_deletion_splits_the_alignment() -> None:
    first, second = segments_of(_record("5M3D5M", position=5))
    assert (first.query_start, first.query_end) == (0, 4)
    assert (first.reference_start, first.reference_end) == (4, 8)
    assert (second.query_start, second.query_end) == (5, 9)
    assert (second.reference_start, second.reference_end) == (12, 16)


def test_a_long_insertion_splits_and_the_bases_stay_with_the_left_side() -> None:
    first, second = segments_of(_record("5M3I5M", position=5))
    assert (first.query_start, first.query_end) == (0, 4)
    assert (first.reference_start, first.reference_end) == (4, 8)
    assert (second.query_start, second.query_end) == (8, 12)
    assert (second.reference_start, second.reference_end) == (9, 13)


def test_internal_short_insertion_stays_inside_the_segment() -> None:
    # 1I 没超过阈值：段内插了一个 read 碱基，段的 query 区间跟着延长
    (segment,) = segments_of(_record("5M1I5M", position=5))
    assert (segment.query_start, segment.query_end) == (0, 10)
    assert (segment.reference_start, segment.reference_end) == (4, 13)


def test_soft_clipped_bases_do_not_belong_to_any_segment() -> None:
    (segment,) = segments_of(_record("3S7M", position=5))
    assert (segment.query_start, segment.query_end) == (3, 9)
    assert (segment.reference_start, segment.reference_end) == (4, 10)


def test_soft_clip_before_a_split_shifts_the_second_segment() -> None:
    first, second = segments_of(_record("3S3M3D3M", position=5))
    assert (first.query_start, first.query_end) == (3, 5)
    assert (first.reference_start, first.reference_end) == (4, 6)
    assert (second.query_start, second.query_end) == (6, 8)
    assert (second.reference_start, second.reference_end) == (10, 12)


def test_unmapped_records_and_missing_sequence_give_nothing() -> None:
    unmapped = _record("10M", position=5)
    assert segments_of(unmapped)[0].query_length == 10

    record = SamRecord(
        query_name="x",
        flag=4,
        reference_name="*",
        position=0,
        mapping_quality=0,
        cigar=parse_cigar("10M"),
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence="A" * 10,
        qualities="I" * 10,
    )
    assert segments_of(record) == ()


def test_a_record_without_matched_bases_gives_nothing() -> None:
    assert segments_of(_record("5I")) == ()


def test_start_anchor_looks_in_the_reads_own_direction() -> None:
    forward = AlignmentSegment("r", "chr", 0, 9, 100, 109, False)
    reverse = AlignmentSegment("r", "chr", 0, 9, 100, 109, True)
    assert forward.start_anchor == 100
    assert reverse.start_anchor == 109


def test_query_overlap_counts_the_shared_read_bases() -> None:
    left = AlignmentSegment("r", "chr", 0, 9, 100, 109, False)
    right = AlignmentSegment("r", "chr", 6, 15, 200, 209, False)
    assert left.query_overlap(right) == 4
    assert right.query_overlap(left) == 4

    apart = AlignmentSegment("r", "chr", 11, 20, 200, 209, False)
    assert left.query_overlap(apart) == 0
