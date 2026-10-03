"""堆叠（pileup）的定点用例：CIGAR 消费、朝向、插入/缺失的挂靠、质量的解释。

这些用例全部**手写 SAM 记录**、期望值能手算，不调用比对器——堆叠是下游算法，
它的输入契约是公共层的 SAM，测试就应该只依赖那一层（顺带也证明"换一个比对器
产出的 SAM 照样能进这条链"）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.alignment_io import (
    SamHeader,
    SamRecord,
    parse_cigar,
    write_sam,
)
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.consensus_calling import iter_pileup

#: 32 bp 的已知参考：0-3 A、4-7 C、8-11 G、12-15 T，之后重复两轮。
_REFERENCE = "AAAACCCCGGGGTTTTACGTACGTACGTACGT"
_SEQ_ID = "chr"
_FLAG_REVERSE = 0x10
_FLAG_UNMAPPED = 0x4
_Q40 = "I"  # Phred+33：'I' = 73 - 33 = 40


def _reference() -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=_SEQ_ID, sequence=_REFERENCE)])


def _record(
    name: str,
    position: int,
    cigar: str,
    sequence: str,
    *,
    flag: int = 0,
    qualities: str = "*",
    mapq: int = 60,
    reference_name: str = _SEQ_ID,
) -> SamRecord:
    """手写一条 SAM 记录（``position`` 是 1-based，与 SAM 一致）。"""
    return SamRecord(
        query_name=name,
        flag=flag,
        reference_name=reference_name,
        position=position,
        mapping_quality=mapq,
        cigar=parse_cigar(cigar),
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities=qualities,
    )


def _columns(records, **kwargs) -> dict[int, object]:
    return {
        column.position: column
        for column in iter_pileup(_reference(), records, **kwargs)
    }


# ---------------------------------------------------------------------------
# 基本消费关系
# ---------------------------------------------------------------------------


def test_forward_read_produces_one_observation_per_position() -> None:
    record = _record("r1", position=5, cigar="5M", sequence=_REFERENCE[4:9], qualities=_Q40 * 5)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6, 7, 8]
    for offset, position in enumerate(range(4, 9)):
        column = columns[position]
        assert column.seq_id == _SEQ_ID
        assert column.reference_base == _REFERENCE[position]
        assert column.coverage == 1
        observation = column.bases[0]
        assert observation.query_name == "r1"
        assert observation.base == _REFERENCE[position]
        assert observation.quality == 40
        assert observation.mapq == 60
        assert observation.is_reverse is False
        assert observation.read_offset == offset
        assert observation.read_length == 5
        assert observation.is_ambiguous is False


def test_soft_clipped_bases_advance_the_offset_without_evidence() -> None:
    """软剪裁的碱基留在 SEQ 里、但不参与比对——它们不该产生任何位置证据。"""
    record = _record("clip", position=7, cigar="3S4M", sequence="TTT" + _REFERENCE[6:10], qualities=_Q40 * 7)
    columns = _columns([record])

    assert sorted(columns) == [6, 7, 8, 9]  # 没有 4、5、6 这些被剪裁的位置
    assert columns[6].bases[0].base == _REFERENCE[6]
    assert columns[6].bases[0].read_offset == 3  # 前 3 个碱基被剪掉了
    assert columns[6].bases[0].read_length == 7


def test_insertion_is_anchored_to_the_preceding_reference_position() -> None:
    # 4-6 是 CCC，插入 AA，再比 7-10 的 CGGG
    sequence = _REFERENCE[4:7] + "AA" + _REFERENCE[7:11]
    record = _record("ins", position=5, cigar="3M2I4M", sequence=sequence, qualities=_Q40 * 9)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6, 7, 8, 9, 10]
    assert all(column.coverage == 1 for column in columns.values())
    # 插入挂在它前面那个参考位置（6）上，且只有那一列有插入
    assert [position for position, column in columns.items() if column.insertions] == [6]
    insertion = columns[6].insertions[0]
    assert insertion.bases == "AA"
    assert insertion.qualities == (40, 40)
    assert insertion.read_offset == 3
    assert insertion.read_length == 9
    assert columns[7].insertions == ()
    # 插入之后的比对碱基仍然落在正确的参考位置上
    assert columns[7].bases[0].base == _REFERENCE[7]
    assert columns[7].bases[0].read_offset == 5


def test_deletion_lands_on_every_covered_position_with_the_next_base_quality() -> None:
    # 4-6 CCC，参考 7-8 缺两个碱基，再比 9-11 GGG
    sequence = _REFERENCE[4:7] + _REFERENCE[9:12]
    record = _record("del", position=5, cigar="3M2D3M", sequence=sequence, qualities="IIIIII")
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6, 7, 8, 9, 10, 11]
    for position in (7, 8):
        column = columns[position]
        assert column.coverage == 0
        assert len(column.deletions) == 1
        deletion = column.deletions[0]
        assert deletion.query_name == "del"
        assert deletion.length == 2
        # 缺失没有质量，用 read 里紧随其后的那个碱基的质量（下标 3）
        assert deletion.quality == 40
        assert deletion.read_offset == 3
    for position in (4, 5, 6, 9, 10, 11):
        assert columns[position].coverage == 1


def test_skipped_region_produces_nothing() -> None:
    """``N``（跳过的参考区）不承载任何证据——那两个位置上不会有列。"""
    sequence = _REFERENCE[4:7] + _REFERENCE[9:11]
    record = _record("skip", position=5, cigar="3M2N2M", sequence=sequence, qualities=_Q40 * 5)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6, 9, 10]  # 7、8 被 N 跳过，没有列
    assert all(column.coverage == 1 for column in columns.values())


def test_insertion_right_after_a_skipped_region_is_not_recorded() -> None:
    """紧跟在 ``N`` 后面的插入没有可挂靠的参考位置（``N`` 之前那段不承载证据），不记录。"""
    sequence = _REFERENCE[4:7] + "A" + _REFERENCE[9:11]
    record = _record("after-n", position=5, cigar="3M2N1I2M", sequence=sequence, qualities=_Q40 * 6)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6, 9, 10]
    assert columns[6].insertions == ()  # 紧接 N，挂不上去
    assert columns[10].insertions == ()


def test_trailing_insertion_anchors_to_the_last_aligned_position() -> None:
    """末尾多出的碱基挂在最后一个比对位置上（它有明确的参考坐标）。"""
    sequence = _REFERENCE[4:7] + "A"
    record = _record("tail", position=5, cigar="3M1I", sequence=sequence, qualities=_Q40 * 4)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6]
    assert [position for position, column in columns.items() if column.insertions] == [6]
    assert columns[6].insertions[0].bases == "A"


def test_leading_insertion_without_a_preceding_position_is_not_recorded() -> None:
    sequence = "AA" + _REFERENCE[4:7]
    record = _record("lead", position=5, cigar="2I3M", sequence=sequence, qualities=_Q40 * 5)
    columns = _columns([record])

    assert sorted(columns) == [4, 5, 6]
    assert all(not column.insertions for column in columns.values())
    assert columns[4].bases[0].read_offset == 2


# ---------------------------------------------------------------------------
# 朝向、质量与异常输入
# ---------------------------------------------------------------------------


def test_reverse_strand_record_is_taken_as_already_reference_oriented() -> None:
    """SAM 规定负链记录的 SEQ 存与参考同向的序列，所以堆叠**不做**任何翻转。"""
    record = _record(
        "rev",
        position=5,
        cigar="4M",
        sequence=_REFERENCE[4:8],
        flag=_FLAG_REVERSE,
        qualities=_Q40 * 4,
    )
    columns = _columns([record])

    for position in range(4, 8):
        observation = columns[position].bases[0]
        assert observation.base == _REFERENCE[position]  # 与参考一致，没有被翻转
        assert observation.is_reverse is True


def test_coverage_and_strand_counts() -> None:
    forward = _record("f", position=5, cigar="3M", sequence=_REFERENCE[4:7], qualities=_Q40 * 3)
    reverse = _record(
        "r",
        position=5,
        cigar="3M",
        sequence=_REFERENCE[4:7],
        flag=_FLAG_REVERSE,
        qualities=_Q40 * 3,
    )
    mismatch = _record("m", position=5, cigar="3M", sequence="GGG", qualities=_Q40 * 3)
    columns = _columns([forward, reverse, mismatch])

    assert columns[4].coverage == 3
    assert columns[4].forward_coverage == 2
    assert columns[4].reverse_coverage == 1
    assert columns[4].counts() == {"C": 2, "G": 1}


def test_missing_qualities_use_the_documented_default() -> None:
    record = _record("noqual", position=5, cigar="2M", sequence=_REFERENCE[4:6])
    default_zero = _columns([record])
    assert default_zero[4].bases[0].quality == 0

    with_default = _columns([record], default_quality=25)
    assert with_default[4].bases[0].quality == 25


def test_ambiguous_read_base_is_kept_and_flagged() -> None:
    record = _record("withn", position=5, cigar="3M", sequence="CNG", qualities=_Q40 * 3)
    columns = _columns([record])

    assert columns[4].counts() == {"C": 1}
    assert columns[5].bases[0].base == "N"
    assert columns[5].bases[0].is_ambiguous is True
    assert columns[6].counts() == {"G": 1}


def test_unmapped_and_sequenceless_records_are_skipped() -> None:
    # 未比对记录按 SAM 惯例写成 RNAME=* / POS=0 / CIGAR=* / SEQ=*
    unmapped = _record(
        "u",
        position=0,
        cigar="*",
        sequence="*",
        flag=_FLAG_UNMAPPED,
        qualities="*",
        reference_name="*",
    )
    assert _columns([unmapped]) == {}


def test_only_positions_with_evidence_are_emitted_in_order() -> None:
    first = _record("a", position=1, cigar="4M", sequence=_REFERENCE[0:4], qualities=_Q40 * 4)
    second = _record("b", position=9, cigar="4M", sequence=_REFERENCE[8:12], qualities=_Q40 * 4)
    columns = list(iter_pileup(_reference(), [first, second]))

    assert [column.position for column in columns] == [0, 1, 2, 3, 8, 9, 10, 11]


def test_unknown_reference_name_is_rejected() -> None:
    record = _record(
        "x", position=1, cigar="2M", sequence="AC", reference_name="plasmid", qualities="II"
    )
    with pytest.raises(ValueError, match="plasmid"):
        list(iter_pileup(_reference(), [record]))


def test_negative_default_quality_is_rejected() -> None:
    with pytest.raises(ValueError):
        list(iter_pileup(_reference(), [], default_quality=-1))


def test_reads_can_come_from_a_sam_file(tmp_path: Path) -> None:
    """输入既可以是记录序列，也可以是 SAM 文件——两者给出完全一样的堆叠。"""
    record = _record("file", position=5, cigar="5M", sequence=_REFERENCE[4:9], qualities=_Q40 * 5)
    path = tmp_path / "mapped.sam"
    header = SamHeader.from_sequences([(_SEQ_ID, len(_REFERENCE))])
    write_sam(path, header, [record])

    from_records = list(iter_pileup(_reference(), [record]))
    from_file = list(iter_pileup(_reference(), path))
    assert from_file == from_records
