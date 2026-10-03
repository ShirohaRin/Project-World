"""应用突变的用例。

期望值全部能手算：合成参考是固定串，突变的坐标、新位置、最终序列都在注释里推过一遍。
最重要的是那条"三条突变混在一起、按降序应用"的用例——位移与顺序一旦写错，只有它会挂。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.genome_diff import (
    GenomeDiff,
    GenomeDiffHeader,
    GenomeDiffRecord,
    MutationEntry,
    assemble_diff,
    apply_diff,
    apply_mutations,
    deletion_record,
    insertion_record,
    read_alignment_evidence,
    substitution_record,
)


def _diff(*records: GenomeDiffRecord) -> GenomeDiff:
    return assemble_diff([MutationEntry(mutation=record) for record in records])


def _applied(sequence: str, *records: GenomeDiffRecord):
    return apply_mutations(sequence, records)


# ---------------------------------------------------------------------------
# 三类突变各自的行为
# ---------------------------------------------------------------------------


def test_substitution_replaces_one_base() -> None:
    result = _applied("ACGTACGT", substitution_record(
        seq_id="chr", position=3, call_base="T", frequency=1.0
    ))

    assert result.sequence == "ACTTACGT"
    assert result.length == 8
    assert result.mutations[0].position == 3
    assert result.mutations[0].shift == 0


def test_deletion_removes_the_bases_starting_at_the_position() -> None:
    # 3、4 两位是 "GT"
    result = _applied("ACGTACGT", deletion_record(
        seq_id="chr", position=3, length=2, frequency=1.0
    ))

    assert result.sequence == "ACACGT"
    assert result.length == 6
    assert result.mutations[0].position == 3


def test_insertion_goes_after_the_position() -> None:
    # 插在位置 2（"C"）之后、位置 3（"G"）之前
    result = _applied("ACGT", insertion_record(
        seq_id="chr", position=2, inserted="TT", frequency=1.0
    ))

    assert result.sequence == "ACTTGT"
    assert result.mutations[0].position == 3     # 插入的第一个碱基在新序列的第 3 位
    assert result.mutations[0].shift == 0


def test_insertion_at_the_last_base_is_appended() -> None:
    result = _applied("ACGT", insertion_record(
        seq_id="chr", position=4, inserted="AA", frequency=1.0
    ))

    assert result.sequence == "ACGTAA"
    assert result.mutations[0].position == 5


# ---------------------------------------------------------------------------
# 多条突变：顺序与位移
# ---------------------------------------------------------------------------


def test_mutations_are_applied_back_to_front_with_correct_shifts() -> None:
    """原序列 `AAACCCGGGTTT`：
    - 在位置 2 之后插入 "GG"（+2）
    - 从位置 5 起删 2 个碱基（"CC"，−2）
    - 位置 10 的 T 换成 A（0）

    按坐标升序算位移、按降序改序列。最终 = `AA` + `GG` + `AC` + `GGGA` + `TT` = `AAGGACGGGATT`。
    """
    result = _applied(
        "AAACCCGGGTTT",
        insertion_record(seq_id="chr", position=2, inserted="GG", frequency=1.0),
        deletion_record(seq_id="chr", position=5, length=2, frequency=1.0),
        substitution_record(seq_id="chr", position=10, call_base="A", frequency=1.0),
    )

    assert result.sequence == "AAGGACGGGATT"
    assert result.length == 12
    # mutations 按原坐标升序，位置是新坐标
    assert [(item.record.position, item.position, item.shift) for item in result.mutations] == [
        (2, 3, 0),    # 插入：新位置 = 2 + 0 + 1
        (5, 7, 2),    # 缺失：前面插了 2 个碱基
        (10, 10, 0),  # 替换：+2（插入）−2（缺失）= 0
    ]


def test_a_deletion_shifts_the_later_positions_left() -> None:
    result = _applied(
        "AAACCCGGGTTT",
        deletion_record(seq_id="chr", position=3, length=4, frequency=1.0),
        substitution_record(seq_id="chr", position=11, call_base="A", frequency=1.0),
    )

    # 删掉 3..6（"ACCC"）→ "AAGGGTTT"，再把原位置 11 的 T 换成 A（新坐标 11−4=7）
    assert result.sequence == "AAGGGTAT"
    assert result.mutations[1].position == 7
    assert result.mutations[1].shift == -4


def test_two_mutations_at_the_same_position_keep_input_order() -> None:
    first = deletion_record(seq_id="chr", position=3, length=1, frequency=1.0)
    second = substitution_record(seq_id="chr", position=3, call_base="C", frequency=1.0)

    result = apply_mutations("ACGTACGT", [first, second])

    # 先删掉位置 3 的 G（"ACTACGT"），再把新第 3 位的 T 换成 C（"ACCACGT"）
    assert result.sequence == "ACCACGT"
    assert [item.record.type for item in result.mutations] == ["DEL", "SNP"]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_deletion_crossing_the_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="越过参考末端"):
        apply_mutations(
            "ACGTACGT",
            [deletion_record(seq_id="chr", position=7, length=3, frequency=1.0)],
        )


def test_unsupported_types_are_rejected_with_their_name() -> None:
    mobile = GenomeDiffRecord(
        type="MOB",
        id="1",
        evidence=(),
        seq_id="chr",
        position=5,
        fields=("IS1", "-1", "8"),
    )
    with pytest.raises(ValueError, match="MOB"):
        apply_mutations("ACGTACGT", [mobile])


def test_evidence_records_are_rejected() -> None:
    with pytest.raises(ValueError, match="证据行"):
        apply_mutations(
            "ACGTACGT",
            [
                read_alignment_evidence(
                    seq_id="chr", position=3, reference_base="G", call_base="T"
                )
            ],
        )


def test_positions_outside_the_sequence_are_rejected() -> None:
    with pytest.raises(ValueError, match="超出序列长度"):
        apply_mutations(
            "ACGT", [substitution_record(seq_id="chr", position=9, call_base="A", frequency=1.0)]
        )


def test_a_bad_substitution_base_is_rejected() -> None:
    broken = GenomeDiffRecord(
        type="SNP", id="1", evidence=(), seq_id="chr", position=2, fields=("N",)
    )
    with pytest.raises(ValueError, match="新碱基不合法"):
        apply_mutations("ACGT", [broken])


def test_validation_happens_before_anything_is_applied() -> None:
    """一条越界就整体报错——不产出"改了一半"的序列。"""
    with pytest.raises(ValueError, match="超出序列长度"):
        apply_mutations(
            "ACGT",
            [
                substitution_record(seq_id="chr", position=2, call_base="T", frequency=1.0),
                substitution_record(seq_id="chr", position=99, call_base="T", frequency=1.0),
            ],
        )


def test_no_mutations_returns_the_same_sequence() -> None:
    result = apply_mutations("ACGTACGT", [])

    assert result.sequence == "ACGTACGT"
    assert result.mutations == ()
    assert result.seq_id == ""


# ---------------------------------------------------------------------------
# 整体应用一份 .gd
# ---------------------------------------------------------------------------


def test_apply_diff_handles_multiple_references() -> None:
    diff = _diff(
        substitution_record(seq_id="chr", position=3, call_base="T", frequency=1.0),
        deletion_record(seq_id="plasmid", position=2, length=1, frequency=1.0),
    )

    applied = apply_diff({"chr": "ACGTACGT", "plasmid": "TTTT"}, diff)

    assert [(item.seq_id, item.sequence) for item in applied] == [
        ("chr", "ACTTACGT"),
        ("plasmid", "TTT"),
    ]
    # 没被碰到的参考也照样返回（变的是它自己那条）
    assert applied[1].mutations[0].record.seq_id == "plasmid"


def test_apply_diff_rejects_mutations_pointing_at_an_unknown_reference() -> None:
    diff = _diff(substitution_record(seq_id="missing", position=1, call_base="T", frequency=1.0))

    with pytest.raises(ValueError, match="不在给定的参考集合里"):
        apply_diff({"chr": "ACGT"}, diff)


def test_end_to_end_from_a_constructed_diff() -> None:
    """用分片 B 造一份带证据的 `.gd`（替换 + 插入 + 缺失），应用到合成参考上，

    结果要与手算一致，且长度等于原长加减换量。证据行不参与应用。
    """
    reference = "AAACCCGGGTTT"
    diff = assemble_diff(
        [
            MutationEntry(
                mutation=substitution_record(
                    seq_id="chr", position=10, call_base="A", frequency=1.0
                ),
                evidence=(
                    read_alignment_evidence(
                        seq_id="chr",
                        position=10,
                        reference_base="T",
                        call_base="A",
                        frequency=1.0,
                    ),
                ),
            ),
            MutationEntry(
                mutation=insertion_record(
                    seq_id="chr", position=2, inserted="GG", frequency=1.0
                )
            ),
            MutationEntry(
                mutation=deletion_record(
                    seq_id="chr", position=5, length=2, frequency=1.0
                )
            ),
        ]
    )

    applied = apply_diff({"chr": reference}, diff)[0]

    assert applied.sequence == "AAGGACGGGATT"
    assert applied.length == len(reference) + 2 - 2
    assert len(applied.mutations) == 3  # 证据不算
    # mutations 按**原坐标**升序：插入(2)→新 3、缺失(5)→新 7、替换(10)→新 10
    assert [item.position for item in applied.mutations] == [3, 7, 10]
    assert [item.record.type for item in applied.mutations] == ["INS", "DEL", "SNP"]
    assert [item.shift for item in applied.mutations] == [0, 2, 0]


def test_applied_genome_attributes() -> None:
    result = apply_mutations(
        "ACGT",
        [substitution_record(seq_id="chr", position=1, call_base="T", frequency=1.0)],
        seq_id="chr",
    )

    assert (result.seq_id, result.length) == ("chr", 4)
    assert result.sequence == "TCGT"
    assert result.mutations[0].record.position == 1


def test_diff_header_is_not_needed_for_applying() -> None:
    """应用只看突变行；空文件（只有头）等于什么都不做。"""
    empty = GenomeDiff(header=GenomeDiffHeader(entries=(("PROGRAM", ("x",)),)))

    assert apply_diff({"chr": "ACGT"}, empty)[0].sequence == "ACGT"
