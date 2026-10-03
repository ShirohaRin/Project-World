"""接受判据的定点用例：四条支撑判据各自的门槛、位置哈希分下限、与真实几何的对照。

造候选时**每条都用三个不同的起点**：位置哈希分默认下限是 3，达不到会额外多出一条
``pos_hash_score`` 失败项，把要验的那条判据盖住。
"""

from __future__ import annotations

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.junction_calling import (
    AcceptanceSettings,
    JunctionCandidate,
    JunctionKey,
    SupportingRead,
    accept_junctions,
    call_junctions,
    evaluate_candidate,
    evaluate_junctions,
)


def _candidate(
    reads: list[tuple[bool, int]],
    *,
    index: int = 0,
    anchors: tuple[int, ...] = (1, 2, 3),
) -> JunctionCandidate:
    """``reads`` 是 ``(是否负链, 两侧独有碱基数的较小值)`` 的列表。"""
    if len(anchors) != len(reads):
        anchors = tuple(range(1, len(reads) + 1))
    key = JunctionKey("chr", index, "", "chr", 1000 + index)
    support = tuple(
        SupportingRead(
            query_name=f"r{index}-{position}",
            anchor=anchor,
            is_reverse=reverse,
            unique_first=extension,
            unique_second=extension,
        )
        for position, ((reverse, extension), anchor) in enumerate(zip(reads, anchors))
    )
    return JunctionCandidate(key=key, sequence="A" * 10, support=support)


def test_a_well_supported_junction_is_accepted() -> None:
    candidate = _candidate([(False, 20), (False, 25), (True, 18), (True, 30)])
    evidence = evaluate_candidate(candidate)
    assert evidence.accepted
    assert evidence.failures == ()
    assert evidence.skew_score is None  # 对应上游产物里的 NT
    assert (evidence.read_count, evidence.pos_hash_score) == (4, 4)
    assert (evidence.plus_reads, evidence.minus_reads) == (2, 2)
    assert (evidence.plus_longest_extension, evidence.minus_longest_extension) == (25, 30)
    assert evidence.longest_extension == 30


def test_reads_on_one_strand_only_are_rejected() -> None:
    candidate = _candidate([(False, 30), (False, 30), (False, 30)])
    evidence = evaluate_candidate(candidate)
    # 只有正链：判据 1 不过；判据 3 要求"每条链上都有" read，于是它也不过
    assert evidence.failures == ("both_strands", "extension_per_strand")
    assert evidence.minus_reads == 0


def test_the_fourteen_base_rule_needs_one_read_covering_both_sides() -> None:
    enough = _candidate([(False, 20), (True, 16), (False, 20)])
    assert evaluate_candidate(enough).accepted

    too_short = _candidate([(False, 13), (True, 13), (False, 13)])
    assert evaluate_candidate(too_short).failures == ("extension_each_side",)


def test_each_strand_needs_a_nine_base_extension() -> None:
    candidate = _candidate([(False, 20), (True, 8), (False, 15)])
    evidence = evaluate_candidate(candidate)
    # 整体最长 20 过了"一侧 14"那条，但负链最长只有 8 → 判据 3 不过
    assert evidence.failures == ("extension_per_strand",)
    assert (evidence.plus_longest_extension, evidence.minus_longest_extension) == (20, 8)


def test_the_shortest_side_rule_is_checked_on_its_own() -> None:
    relaxed = AcceptanceSettings(
        min_extension_each_side=2, min_extension_per_strand=2, min_extension_any=3
    )
    candidate = _candidate([(False, 2), (True, 2), (False, 2)])
    assert evaluate_candidate(candidate, settings=relaxed).failures == ("extension_any",)


def test_position_hash_score_has_a_floor() -> None:
    # 四条 read 全从同一个位置压过来 → 位置哈希分 1
    pile = _candidate(
        [(False, 30), (False, 30), (True, 30), (True, 30)], anchors=(7, 7, 7, 7)
    )
    assert evaluate_candidate(pile).failures == ("pos_hash_score",)

    spread = _candidate([(False, 30), (False, 30), (True, 30)], anchors=(1, 2, 3))
    assert evaluate_candidate(spread).accepted


def test_settings_can_relax_the_acceptance() -> None:
    candidate = _candidate([(False, 6), (True, 6), (False, 6)])
    assert not evaluate_candidate(candidate).accepted
    relaxed = AcceptanceSettings(
        min_extension_each_side=5,
        min_extension_per_strand=5,
        min_extension_any=3,
        min_pos_hash_score=3,
    )
    assert evaluate_candidate(candidate, settings=relaxed).accepted


def test_empty_support_fails_every_criterion() -> None:
    evidence = evaluate_candidate(_candidate([]))
    assert evidence.failures == (
        "both_strands",
        "extension_each_side",
        "extension_per_strand",
        "extension_any",
        "pos_hash_score",
    )
    assert evidence.longest_extension == 0


def test_both_strands_can_be_waived() -> None:
    # 判据 3 本身要求"每条链上都有"——只在连判据 1 一起放宽时才谈得上绕过它
    candidate = _candidate([(False, 30), (False, 30), (False, 30)])
    settings = AcceptanceSettings(require_both_strands=False, min_extension_per_strand=0)
    assert evaluate_candidate(candidate, settings=settings).accepted


def test_accept_junctions_keeps_only_the_passing_ones() -> None:
    good = _candidate([(False, 30), (True, 30), (False, 20)], index=0)
    weak = _candidate([(False, 5), (True, 5), (False, 5)], index=1)
    assert len(evaluate_junctions([good, weak])) == 2
    accepted = accept_junctions([good, weak])
    assert [evidence.candidate.key.left_breakpoint for evidence in accepted] == [0]


# ---------------------------------------------------------------------------
# 与真实几何对照
# ---------------------------------------------------------------------------


def _reference() -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence="ACGT" * 40)])


def _record(name: str, cigar: str, *, position: int, reverse: bool = False) -> SamRecord:
    parsed = parse_cigar(cigar)
    return SamRecord(
        query_name=name,
        flag=16 if reverse else 0,
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


def _deletion_reads(starts: range) -> list[SamRecord]:
    """跨过参考 30..32 这个 3 bp 缺失的 read（左段盖到 29、右段从 33 起）。"""
    return [
        _record(f"read-{start}", f"{30 - start}M3D{10 + start}M", position=start + 1)
        for start in starts
    ]


def test_candidates_from_real_geometry_are_evaluated() -> None:
    candidates = call_junctions(
        _deletion_reads(range(25)), _reference(), max_cumulative_fraction=1.0
    )
    evidence = evaluate_candidate(candidates[0])
    # 两侧独有碱基数的较小值是 min(30−start, 10+start)，start=10 时两边都 20 → 最大 20
    assert evidence.pos_hash_score == 25
    assert evidence.longest_extension == 20
    # 全是正链 → 判据 1 不过，判据 3 也跟着不过（这正是上游要求两条链的原因）
    assert evidence.failures == ("both_strands", "extension_per_strand")
