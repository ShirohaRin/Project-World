"""候选对的定点用例：五条判据各自的作用、连接序列怎么拼。

参考取 ``AAACCCGGGTTT`` 重复三遍（36 bp）——周期是 12，所以"第几个碱基是什么"一眼能数出来，
拼出来的连接序列能手写核对。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.junction_calling import (
    CandidateSettings,
    build_junction_sequence,
    iter_chimeric_pairs,
    longest_read_length,
)

_SEQUENCE = "AAACCCGGGTTT" * 3  # 36 bp


def _reference(sequence: str = _SEQUENCE, seq_id: str = "chr") -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=seq_id, sequence=sequence)])


def _record(
    name: str,
    position: int,
    cigar: str,
    *,
    sequence: str | None = None,
    reverse: bool = False,
) -> SamRecord:
    parsed = parse_cigar(cigar)
    if sequence is None:
        sequence = "A" * parsed.query_length
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
        sequence=sequence,
        qualities="I" * len(sequence),
    )


def _pairs(records: list[SamRecord], **kwargs):
    return [(record.query_name, pair) for record, pair in iter_chimeric_pairs(records, **kwargs)]


# ---------------------------------------------------------------------------
# 五条判据
# ---------------------------------------------------------------------------


def test_a_long_deletion_gives_one_chimeric_pair() -> None:
    # 25M3D15M：两段 read 区间是 [0,24] 与 [25,39]，合起来 40 > 单段最好 25 + 2
    pairs = _pairs([_record("r", 1, "25M3D15M")])
    assert len(pairs) == 1
    _, pair = pairs[0]
    assert (pair.first.query_start, pair.first.query_end) == (0, 24)
    assert (pair.second.query_start, pair.second.query_end) == (25, 39)
    assert pair.overlap == 0
    assert (pair.unique_first, pair.unique_second) == (25, 15)
    assert pair.union_length == 40
    assert pair.intervening == 0


def test_a_short_deletion_is_not_a_chimera() -> None:
    # 2D 不拆段：只有一段，谈不上"对"
    assert _pairs([_record("r", 1, "25M2D15M")]) == []


def test_the_first_segment_must_start_at_the_read_start() -> None:
    # 前面有 5S：靠前那段从 read 的第 6 个碱基才开始 → 判据 1 不满足
    assert _pairs([_record("r", 1, "5S25M3D10M")]) == []


def test_a_pair_must_beat_the_best_single_segment_by_more_than_two() -> None:
    # 8M3D8M：两段合起来 16，比最好的一段 8 多 8 —— 判据 2 过得去，
    # 但每段只有 8 个 read 碱基，判据 4 要求"其中一段 ≥ 10" → 不成立
    assert _pairs([_record("r", 1, "8M3D8M")]) == []


def test_each_side_needs_at_least_five_unique_read_bases() -> None:
    assert _pairs([_record("r", 1, "20M3D5M")]) != []  # 正好 5，边界的"至少"成立
    assert _pairs([_record("r", 1, "20M3D4M")]) == []  # 4 < 5


def test_the_intervening_read_bases_are_capped() -> None:
    # 15I：两段之间夹 15 个只属于 read 的碱基 → 20 以内，成立
    assert _pairs([_record("r", 1, "20M15I10M")]) != []
    # 25I：夹 25 个 → 超过 20，不成立
    assert _pairs([_record("r", 1, "20M25I10M")]) == []


def test_two_partial_alignments_of_one_read_form_a_pair() -> None:
    # 同一条 read（40 bp）的两条记录：前 25 bp 比到 [100,124]，后 15 bp 比到 [500,514]
    records = [
        _record("r", 101, "25M15S", sequence="A" * 40),
        _record("r", 501, "25S15M", sequence="A" * 40),
    ]
    pairs = _pairs(records)
    assert len(pairs) == 1
    _, pair = pairs[0]
    assert (pair.first.query_start, pair.first.query_end) == (0, 24)
    assert (pair.second.query_start, pair.second.query_end) == (25, 39)
    assert pair.intervening == 0


def test_settings_can_relax_the_criteria() -> None:
    records = [_record("r", 1, "8M3D8M")]
    assert _pairs(records) == []
    relaxed = CandidateSettings(min_unique_each=5, min_unique_one=8, coverage_margin=2)
    assert _pairs(records, settings=relaxed) != []


def test_longest_read_length_ignores_records_without_sequence() -> None:
    records = [_record("a", 1, "10M"), _record("b", 1, "40M")]
    assert longest_read_length(records) == 40
    assert longest_read_length([]) == 0


# ---------------------------------------------------------------------------
# 连接序列
# ---------------------------------------------------------------------------


def test_junction_sequence_joins_both_reference_sides() -> None:
    # 12M3D12M 在 36 bp 参考上：左段盖 [0,11]、缺失 12..14、右段从 15 开始
    (_, pair) = _pairs([_record("r", 1, "12M3D12M")])[0]
    sequence = build_junction_sequence(pair, "A" * 24, _reference(), flank=2)
    # 左：参考 10..11 = "TT"；中间没有 read 碱基；右：参考 15..16 = "CC"
    assert sequence == "TT" + "CC"


def test_intervening_read_bases_are_written_in_the_middle() -> None:
    read = "AAACCCGGGTTT" + "GGG" + "AAACCCGGGTTT"  # 27 bp：左 + 插入 + 右
    (_, pair) = _pairs([_record("r", 1, "12M3I12M", sequence=read)])[0]
    assert pair.intervening == 3
    sequence = build_junction_sequence(pair, read, _reference(), flank=2)
    # 左：参考 10..11 = "TT"；中间：read[12:15] = "GGG"；右：参考 12..13 = "AA"
    assert sequence == "TT" + "GGG" + "AA"


def test_flanking_reference_bases_are_clipped_at_the_sequence_edges() -> None:
    (_, pair) = _pairs([_record("r", 1, "12M3D12M")])[0]
    sequence = build_junction_sequence(pair, "A" * 24, _reference(), flank=5)
    # 左段结束在参考位置 11，要 5 个碱基取到 7..11 = "GGTTT"；
    # 右段从 15 起要 5 个取到 15..19 = "CCCGG"
    assert sequence == "GGTTT" + "CCCGG"


def test_mixed_strands_are_refused() -> None:
    records = [
        _record("r", 1, "12M12S", sequence="A" * 24),
        _record("r", 13, "12S12M", sequence="A" * 24, reverse=True),
    ]
    (_, pair) = _pairs(records)[0]
    with pytest.raises(ValueError, match="不在同一条链上"):
        build_junction_sequence(pair, "A" * 24, _reference(), flank=10)


def test_flank_must_be_positive() -> None:
    (_, pair) = _pairs([_record("r", 1, "12M3D12M")])[0]
    with pytest.raises(ValueError, match="必须为正"):
        build_junction_sequence(pair, "A" * 24, _reference(), flank=0)
