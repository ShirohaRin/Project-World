"""构造记录的用例：**与我们能确证的上游产物逐字节比对**。

下面几条 ``_REAL_*`` 行是从真实 breseq 0.33.1 `output.gd`（aledb.org 的 ALE 数据库）里原样抄来的。
其中突发行只有 ``frequency`` 一个属性，格式简单，所以我们可以要求"构造出来的行与它**逐字节相同**"
——这是分片 B 最硬的验证：证明我们写的东西上游工具能直接读。证据行（``RA``）因为上游还带了一堆
我们算不出的统计量（``bias_e_value`` 等），只比对前 8 列。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from modules.bio_analysis_function.common.genome_diff import (
    GenomeDiff,
    GenomeDiffHeader,
    MutationEntry,
    assemble_diff,
    deletion_record,
    format_genome_diff,
    insertion_record,
    parse_genome_diff,
    read_alignment_evidence,
    substitution_record,
)

_REAL_SNP_LINE = "SNP\t5\t37\tNC_000913\t1269450\tT\tfrequency=1.04031563e-01"
_REAL_DEL_LINE = "DEL\t2\t35\tNC_000913\t701810\t1\tfrequency=1.16257668e-01"
_REAL_DEL_TWO_EVIDENCE_LINE = "DEL\t1\t61,71\tNC_000913\t257908\t776\tfrequency=1"
_REAL_INS_LINE = "INS\t4\t74\tNC_000913\t1159282\tAGT\tfrequency=8.36314739e-02"
_REAL_RA_SUBSTITUTION_PREFIX = "RA\t37\t.\tNC_000913\t1269450\t0\tC\tT\t"
_REAL_RA_INSERTION_PREFIX = "RA\t51\t.\tNC_000913\t3560455\t1\t.\tG\t"


def _formatted_line(record) -> str:
    diff = GenomeDiff(header=GenomeDiffHeader(), records=(record,))
    lines = format_genome_diff(diff).splitlines()
    assert len(lines) == 2
    return lines[1]


# ---------------------------------------------------------------------------
# 与真实产物逐字节比对
# ---------------------------------------------------------------------------


def test_substitution_record_reproduces_the_real_line() -> None:
    record = substitution_record(
        seq_id="NC_000913", position=1269450, call_base="T", frequency=0.104031563
    )

    assert _formatted_line(replace(record, id="5", evidence=("37",))) == _REAL_SNP_LINE


def test_deletion_record_reproduces_the_real_line() -> None:
    record = deletion_record(
        seq_id="NC_000913", position=701810, length=1, frequency=0.116257668
    )

    assert _formatted_line(replace(record, id="2", evidence=("35",))) == _REAL_DEL_LINE


def test_deletion_with_two_evidence_ids_reproduces_the_real_line() -> None:
    record = deletion_record(seq_id="NC_000913", position=257908, length=776, frequency=1.0)

    assert (
        _formatted_line(replace(record, id="1", evidence=("61", "71")))
        == _REAL_DEL_TWO_EVIDENCE_LINE
    )


def test_insertion_record_reproduces_the_real_line() -> None:
    record = insertion_record(
        seq_id="NC_000913", position=1159282, inserted="AGT", frequency=0.0836314739
    )

    assert _formatted_line(replace(record, id="4", evidence=("74",))) == _REAL_INS_LINE


def test_frequency_of_one_is_written_as_plain_one() -> None:
    # 上游对恰好等于 1 的频率写 `1`，其余写 8 位小数的科学计数
    assert _formatted_line(
        deletion_record(seq_id="chr", position=10, length=2, frequency=1.0)
    ).endswith("\tfrequency=1")


def test_substitution_evidence_prefix_matches_the_real_line() -> None:
    record = read_alignment_evidence(
        seq_id="NC_000913",
        position=1269450,
        reference_base="C",
        call_base="T",
        frequency=0.104031563,
        consensus_score=133.2,
        prediction="polymorphism",
    )
    line = _formatted_line(replace(record, id="37"))

    assert line.startswith(_REAL_RA_SUBSTITUTION_PREFIX)
    assert "\tfrequency=1.04031563e-01" in line
    assert "\tconsensus_score=133.2" in line
    assert line.endswith("\tprediction=polymorphism")


def test_insertion_evidence_uses_offset_and_a_dot_reference_base() -> None:
    """插入的第 1 个碱基：上游写 ``RA … 1 . G``（偏移 1、参考碱基是 ``.``）。"""
    record = read_alignment_evidence(
        seq_id="NC_000913", position=3560455, reference_base=".", call_base="G", offset=1
    )
    line = _formatted_line(replace(record, id="51"))

    assert line.startswith(_REAL_RA_INSERTION_PREFIX)
    assert record.fields == ("1", ".", "G")


def test_evidence_attributes_keep_the_order_given() -> None:
    record = read_alignment_evidence(
        seq_id="chr",
        position=10,
        reference_base="C",
        call_base="T",
        frequency=0.5,
        consensus_score=20.0,
        prediction="consensus",
        attributes=(("major_base", "C"), ("custom_note", "手工整理")),
    )

    assert list(record.attributes) == [
        "frequency",
        "consensus_score",
        "prediction",
        "major_base",
        "custom_note",
    ]
    assert record.attribute("custom_note") == "手工整理"


def test_mutation_records_accept_extra_annotation_attributes() -> None:
    record = substitution_record(
        seq_id="chr",
        position=10,
        call_base="T",
        frequency=1.0,
        attributes=(("gene_name", "araJ"), ("gene_product", "predicted transporter")),
    )

    assert list(record.attributes) == ["frequency", "gene_name", "gene_product"]


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_bad_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="位置必须"):
        substitution_record(seq_id="chr", position=0, call_base="T", frequency=1.0)
    with pytest.raises(ValueError, match="判定碱基"):
        substitution_record(seq_id="chr", position=1, call_base="N", frequency=1.0)
    with pytest.raises(ValueError, match="频率必须"):
        substitution_record(seq_id="chr", position=1, call_base="T", frequency=1.5)
    with pytest.raises(ValueError, match="频率必须"):
        substitution_record(seq_id="chr", position=1, call_base="T", frequency=-0.1)
    with pytest.raises(ValueError, match="缺失长度"):
        deletion_record(seq_id="chr", position=1, length=0, frequency=1.0)
    with pytest.raises(ValueError, match="插入序列"):
        insertion_record(seq_id="chr", position=1, inserted="", frequency=1.0)
    with pytest.raises(ValueError, match="插入序列"):
        insertion_record(seq_id="chr", position=1, inserted="AXT", frequency=1.0)
    with pytest.raises(ValueError, match="偏移不能为负"):
        read_alignment_evidence(
            seq_id="chr", position=1, reference_base="C", call_base="T", offset=-1
        )


# ---------------------------------------------------------------------------
# 装配：编号、顺序、证据回填
# ---------------------------------------------------------------------------


def _entry(position: int, evidence_positions: tuple[int, ...]) -> MutationEntry:
    return MutationEntry(
        mutation=substitution_record(
            seq_id="chr", position=position, call_base="T", frequency=1.0
        ),
        evidence=tuple(
            read_alignment_evidence(
                seq_id="chr", position=evidence_position, reference_base="C", call_base="T"
            )
            for evidence_position in evidence_positions
        ),
    )


def test_assemble_diff_numbers_mutations_first_then_evidence() -> None:
    diff = assemble_diff([_entry(100, (100, 101)), _entry(200, (200,))])

    # 编号：突变 1..N，证据接着 N+1..M（上游就是这么编的）
    assert [record.id for record in diff.records] == ["1", "2", "3", "4", "5"]
    # 顺序：先全部突变、再全部证据
    assert [record.type for record in diff.records] == ["SNP", "SNP", "RA", "RA", "RA"]
    # 证据列由装配器回填
    assert diff.records[0].evidence == ("3", "4")
    assert diff.records[1].evidence == ("5",)
    assert diff.records[2].evidence == ()  # 证据行自己没有上级


def test_assemble_diff_backfills_evidence_columns_correctly() -> None:
    diff = assemble_diff([_entry(100, (100, 101))])
    mutation = diff.mutations()[0]

    assert mutation.evidence == ("2", "3")
    resolved = diff.evidence_of(mutation)
    assert [record.position for record in resolved] == [100, 101]
    assert all(record.type == "RA" for record in resolved)


def test_assemble_diff_writes_header_entries_in_order() -> None:
    diff = assemble_diff(
        [_entry(100, ())],
        header_entries=(
            ("PROGRAM", "Project IDEA bio"),
            ("REFSEQ", "ref.gbk"),
            ("READSEQ", "reads_1.fq"),
            ("READSEQ", "reads_2.fq"),
        ),
    )

    assert diff.header.version == "1.0"
    assert diff.header.keys == ("PROGRAM", "REFSEQ", "READSEQ", "READSEQ")
    assert diff.header.values_of("READSEQ") == ("reads_1.fq", "reads_2.fq")
    text = format_genome_diff(diff)
    assert text.startswith("#=GENOME_DIFF\t1.0\n#=PROGRAM\tProject IDEA bio\n")
    assert "#=READSEQ\treads_1.fq\n#=READSEQ\treads_2.fq\n" in text


def test_assemble_diff_output_round_trips() -> None:
    diff = assemble_diff(
        [_entry(100, (100,)), _entry(300, ())],
        header_entries=(("PROGRAM", "Project IDEA bio"),),
    )

    assert parse_genome_diff(format_genome_diff(diff)) == diff
    # 再写一遍也一致（往返稳定）
    again = parse_genome_diff(format_genome_diff(diff))
    assert format_genome_diff(again) == format_genome_diff(diff)


def test_assemble_diff_without_entries_gives_a_header_only_diff() -> None:
    diff = assemble_diff([], header_entries=(("PROGRAM", "Project IDEA bio"),))

    assert diff.records == ()
    assert format_genome_diff(diff) == "#=GENOME_DIFF\t1.0\n#=PROGRAM\tProject IDEA bio\n"
