"""打分与排序的定点用例：位置哈希分怎么数、最小重叠分怎么加、两条上限怎么截。"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.junction_calling import (
    JunctionCandidate,
    JunctionKey,
    SupportingRead,
    call_junctions,
    rank_candidates,
)

_REFERENCE = "ACGT" * 40  # 160 bp


def _reference() -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence=_REFERENCE)])


def _record(name: str, cigar: str, *, position: int) -> SamRecord:
    parsed = parse_cigar(cigar)
    return SamRecord(
        query_name=name,
        flag=0,
        reference_name="chr",
        position=position,
        mapping_quality=60,
        cigar=parsed,
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence="A" * parsed.query_length,
        qualities="I" * parsed.query_length,
    )


def _candidate(index: int, anchors: tuple[int, ...], min_unique: int) -> JunctionCandidate:
    key = JunctionKey(
        left_seq_id="chr",
        left_breakpoint=index,
        intervening="",
        right_seq_id="chr",
        right_breakpoint=1000 + index,
    )
    support = tuple(
        SupportingRead(
            query_name=f"r{index}-{position}",
            anchor=position,
            is_reverse=False,
            unique_first=min_unique,
            unique_second=min_unique,
        )
        for position in anchors
    )
    return JunctionCandidate(key=key, sequence="A" * 10, support=support)


# ---------------------------------------------------------------------------
# 两个分数
# ---------------------------------------------------------------------------


def test_pos_hash_counts_distinct_read_starts() -> None:
    candidate = _candidate(0, (10, 20, 30, 20), min_unique=5)
    assert candidate.read_count == 4
    assert candidate.pos_hash_score == 3  # 20 出现两次，只算一个位置


def test_min_overlap_sums_the_smaller_side_of_each_read() -> None:
    key = JunctionKey("chr", 0, "", "chr", 1)
    support = (
        SupportingRead("a", 10, False, unique_first=12, unique_second=7),
        SupportingRead("b", 20, False, unique_first=4, unique_second=9),
    )
    candidate = JunctionCandidate(key=key, sequence="A" * 10, support=support)
    assert candidate.min_overlap_score == 7 + 4


def test_key_label_is_readable() -> None:
    assert JunctionKey("chr", 5, "TT", "plasmid", 9).label == "chr:5|TT|plasmid:9"
    assert JunctionKey("chr", 5, "", "chr", 9).label == "chr:5|.|chr:9"


# ---------------------------------------------------------------------------
# 排序与截断
# ---------------------------------------------------------------------------


def test_ranking_is_by_pos_hash_then_min_overlap() -> None:
    three_low = _candidate(0, (1, 2, 3), min_unique=5)  # 位置哈希 3、最小重叠 15
    three_high = _candidate(1, (1, 2, 3), min_unique=9)  # 位置哈希 3、最小重叠 27
    two = _candidate(2, (1, 2), min_unique=9)  # 位置哈希 2
    ranked = rank_candidates([three_low, three_high, two], reference_length=10_000)
    assert [candidate.key.left_breakpoint for candidate in ranked] == [1, 0, 2]


def test_truncation_stops_at_the_candidate_count_limit() -> None:
    candidates = [_candidate(index, (index,), min_unique=5) for index in range(5)]
    ranked = rank_candidates(candidates, reference_length=10_000, max_candidates=2)
    assert len(ranked) == 2


def test_truncation_stops_at_the_length_budget() -> None:
    candidates = [_candidate(index, (1, 2, 3), min_unique=5) for index in range(5)]
    # 每条连接序列 10 bp，参考总长 100 → 预算 0.1×100 = 10 bp，只放得下第一条
    ranked = rank_candidates(
        candidates, reference_length=100, max_cumulative_fraction=0.1
    )
    assert len(ranked) == 1


def test_ranking_argument_validation() -> None:
    with pytest.raises(ValueError, match="参考总长必须为正"):
        rank_candidates([], reference_length=0)
    with pytest.raises(ValueError, match="候选数上限必须为正"):
        rank_candidates([], reference_length=100, max_candidates=0)
    with pytest.raises(ValueError, match="累计长度占比"):
        rank_candidates([], reference_length=100, max_cumulative_fraction=1.5)


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


def _deletion_reads(starts: range) -> list[SamRecord]:
    """造一批"跨过同一个 3 bp 缺失（参考 30..32）"的 read，起点各不相同。

    每条 read 长 40 bp：左边盖到参考 29 为止、跳过 30..32、右边从 33 起。
    起点 s 不同 → 位置哈希分应当等于不同起点的个数（缺口的参考位置始终不变）。
    """
    records = []
    for start in starts:
        left = 30 - start  # 左段长度：从 start 盖到 29
        right = 10 + start  # 右段长度：read 总长固定 40
        records.append(
            _record(f"read-{start}", f"{left}M3D{right}M", position=start + 1)
        )
    return records


def test_call_junctions_groups_reads_from_different_starts() -> None:
    candidates = call_junctions(
        _deletion_reads(range(25)),
        _reference(),
        max_cumulative_fraction=1.0,  # 玩具参考只有 160 bp，放宽预算才留得住候选
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.read_count == 25
    assert candidate.pos_hash_score == 25  # 25 个不同的起点
    assert candidate.key.left_breakpoint == 29
    assert candidate.key.right_breakpoint == 33
    assert candidate.key.intervening == ""


def test_the_same_read_counted_twice_is_one_supporting_read() -> None:
    records = _deletion_reads(range(3))
    records.extend(_deletion_reads(range(3)))  # 同名的三条 read 再来一遍
    candidates = call_junctions(records, _reference(), max_cumulative_fraction=1.0)
    assert candidates[0].read_count == 3


def test_no_reads_gives_no_candidates() -> None:
    assert call_junctions([], _reference()) == ()
