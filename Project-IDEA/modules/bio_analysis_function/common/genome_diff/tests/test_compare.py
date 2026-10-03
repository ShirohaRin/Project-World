"""比较多份 `.gd` 的用例。

数据用分片 B 的构造函数造（顺带把两个分片串起来验一遍）。重点钉住三件事：
**同一位置不同碱基是两条突变**、**各样本的频率不会串**、**证据行不参与比较**。
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from modules.bio_analysis_function.common.genome_diff import (
    COMPARISON_COLUMNS,
    GenomeDiff,
    GenomeDiffHeader,
    GenomeDiffRecord,
    MutationEntry,
    assemble_diff,
    compare_files,
    compare_samples,
    deletion_record,
    insertion_record,
    read_alignment_evidence,
    substitution_record,
    write_genome_diff,
)


def _diff(*records: GenomeDiffRecord) -> GenomeDiff:
    """把若干突变记录装配成一份 `.gd`（编号交给装配器）。"""
    return assemble_diff([MutationEntry(mutation=record) for record in records])


def _snp(position: int, base: str, frequency: float, *, seq_id: str = "chr") -> GenomeDiffRecord:
    return substitution_record(
        seq_id=seq_id, position=position, call_base=base, frequency=frequency
    )


# ---------------------------------------------------------------------------
# 共有 / 独有
# ---------------------------------------------------------------------------


def test_mutation_in_two_samples_is_shared() -> None:
    table = compare_samples(
        [
            ("0K", _diff(_snp(100, "T", 1.0), _snp(200, "A", 1.0))),
            ("10K", _diff(_snp(100, "T", 1.0), _snp(300, "G", 0.5))),
        ]
    )

    rows = {row.key.position: row for row in table.rows}
    assert rows[100].samples == ("0K", "10K")
    assert rows[100].is_shared is True
    assert rows[100].sample_count == 2
    assert rows[100].first_sample == "0K"
    assert rows[200].samples == ("0K",)
    assert rows[200].is_shared is False

    assert [row.key.position for row in table.shared()] == [100]
    assert [row.key.position for row in table.unique_to("0K")] == [200]
    assert [row.key.position for row in table.unique_to("10K")] == [300]
    assert [row.key.position for row in table.rows_of("10K")] == [100, 300]


def test_same_position_with_different_bases_are_two_mutations() -> None:
    """同一个位置上的 A→C 与 A→G 是两条不同的突变——这是比较的基础。"""
    table = compare_samples(
        [("s1", _diff(_snp(100, "T", 1.0))), ("s2", _diff(_snp(100, "G", 1.0)))]
    )

    assert len(table.rows) == 2
    assert all(not row.is_shared for row in table.rows)
    assert {row.samples for row in table.rows} == {("s1",), ("s2",)}
    assert {row.key.signature for row in table.rows} == {"T", "G"}


def test_same_position_with_different_insertions_are_two_mutations() -> None:
    table = compare_samples(
        [
            ("s1", _diff(insertion_record(seq_id="chr", position=100, inserted="AGT", frequency=1.0))),
            ("s2", _diff(insertion_record(seq_id="chr", position=100, inserted="AGC", frequency=1.0))),
        ]
    )

    assert len(table.rows) == 2
    assert {row.key.signature for row in table.rows} == {"AGT", "AGC"}


def test_deletion_length_is_part_of_the_identity() -> None:
    """同一位置、不同长度的缺失也是两条突变（DEL 的身份靠长度那一列）。"""
    table = compare_samples(
        [
            ("s1", _diff(deletion_record(seq_id="chr", position=100, length=1, frequency=1.0))),
            ("s2", _diff(deletion_record(seq_id="chr", position=100, length=4, frequency=1.0))),
        ]
    )

    assert len(table.rows) == 2


def test_different_reference_sequences_do_not_collide() -> None:
    table = compare_samples(
        [
            ("s1", _diff(_snp(100, "T", 1.0, seq_id="chr"), _snp(100, "T", 1.0, seq_id="plasmid"))),
        ]
    )

    assert len(table.rows) == 2
    assert {row.key.seq_id for row in table.rows} == {"chr", "plasmid"}


def test_rows_are_sorted_by_reference_then_position() -> None:
    table = compare_samples(
        [
            (
                "s1",
                _diff(
                    _snp(300, "T", 1.0, seq_id="plasmid"),
                    _snp(300, "T", 1.0),
                    _snp(100, "T", 1.0),
                ),
            )
        ]
    )

    assert [(row.key.seq_id, row.key.position) for row in table.rows] == [
        ("chr", 100),
        ("chr", 300),
        ("plasmid", 300),
    ]


# ---------------------------------------------------------------------------
# 频率不串、属性保留
# ---------------------------------------------------------------------------


def test_frequency_is_kept_per_sample() -> None:
    table = compare_samples(
        [
            ("early", _diff(_snp(100, "T", 1.0))),
            ("late", _diff(_snp(100, "T", 0.25))),
        ]
    )
    row = table.rows[0]

    assert row.samples == ("early", "late")
    assert row.frequencies == {"early": 1.0, "late": 0.25}
    # 代表记录是第一次遇到的那条（保留它的频率与属性）
    assert row.record.frequency == 1.0


def test_representative_record_keeps_extra_attributes() -> None:
    annotated = substitution_record(
        seq_id="chr",
        position=100,
        call_base="T",
        frequency=1.0,
        attributes=(("gene_name", "araJ"),),
    )
    table = compare_samples([("s1", _diff(annotated))])

    assert table.rows[0].record.attribute("gene_name") == "araJ"


def test_evidence_records_are_not_compared() -> None:
    """证据行不参与比较：它们在样本之间本来就可能不一样，拿来对齐没有意义。"""
    with_evidence = assemble_diff(
        [
            MutationEntry(
                mutation=_snp(100, "T", 1.0),
                evidence=(
                    read_alignment_evidence(
                        seq_id="chr", position=100, reference_base="C", call_base="T"
                    ),
                ),
            )
        ]
    )
    assert len(with_evidence.evidence()) == 1

    table = compare_samples([("s1", with_evidence), ("s2", _diff(_snp(100, "T", 1.0)))])

    assert len(table.rows) == 1
    assert table.rows[0].samples == ("s1", "s2")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_has_one_column_per_sample() -> None:
    table = compare_samples(
        [("0K", _diff(_snp(100, "T", 1.0), _snp(200, "A", 1.0))), ("10K", _diff(_snp(100, "T", 0.5)))]
    )

    lines = table.to_csv().splitlines()
    assert lines[0] == ",".join([*COMPARISON_COLUMNS, "0K", "10K"])
    assert lines[1] == "SNP,chr,100,T,2,0K,1.0,0.5"      # 两个样本都有，各写各的频率
    assert lines[2] == "SNP,chr,200,A,1,0K,1.0,"         # 只在 0K 里有，10K 那格空着


def test_csv_writes_na_when_a_frequency_is_missing() -> None:
    """有出现、但拿不到频率（上游写了 ``NA``）时单元格写 ``NA``，而不是留空。

    （``JC`` 那种证据行不参与比较，所以这里用一条带 ``frequency=NA`` 的突变记录。）
    """
    record = GenomeDiffRecord(
        type="SNP",
        id="1",
        evidence=(),
        seq_id="chr",
        position=100,
        fields=("T",),
        attributes=MappingProxyType({"frequency": "NA"}),
    )
    table = compare_samples([("s1", GenomeDiff(header=GenomeDiffHeader(), records=(record,)))])

    assert table.to_csv().splitlines()[1] == "SNP,chr,100,T,1,s1,NA"
    assert table.rows[0].frequencies == {"s1": None}


def test_csv_quotes_cells_that_contain_commas() -> None:
    record = GenomeDiffRecord(
        type="XXX",
        id="1",
        evidence=(),
        seq_id="chr",
        position=100,
        fields=("a,b",),
    )
    table = compare_samples([("s1", GenomeDiff(header=GenomeDiffHeader(), records=(record,)))])

    # 类型特有字段里有逗号 → 那一格加引号；频率没有（没写这个属性）→ 写 NA
    assert table.to_csv().splitlines()[1] == 'XXX,chr,100,"a,b",1,s1,NA'


# ---------------------------------------------------------------------------
# 边界与入口
# ---------------------------------------------------------------------------


def test_empty_inputs_are_handled() -> None:
    empty = compare_samples([])
    assert empty.samples == () and empty.rows == ()
    assert empty.to_csv() == ",".join(COMPARISON_COLUMNS) + "\n"

    no_mutations = compare_samples([("s1", GenomeDiff(header=GenomeDiffHeader()))])
    assert no_mutations.samples == ("s1",)
    assert no_mutations.rows == ()
    assert no_mutations.to_csv() == ",".join([*COMPARISON_COLUMNS, "s1"]) + "\n"


def test_duplicate_sample_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="重复"):
        compare_samples([("s1", _diff(_snp(100, "T", 1.0))), ("s1", _diff(_snp(100, "T", 1.0)))])


def test_lookup_of_an_unknown_sample_is_rejected() -> None:
    table = compare_samples([("s1", _diff(_snp(100, "T", 1.0)))])

    with pytest.raises(ValueError, match="没有名为"):
        table.unique_to("s2")
    with pytest.raises(ValueError, match="没有名为"):
        table.rows_of("s2")


def test_compare_files_reads_from_disk(tmp_path: Path) -> None:
    first = tmp_path / "early.gd"
    second = tmp_path / "late.gd"
    write_genome_diff(first, _diff(_snp(100, "T", 1.0), _snp(200, "A", 1.0)))
    write_genome_diff(second, _diff(_snp(100, "T", 1.0)))

    table = compare_files([("early", first), ("late", second)])

    assert [row.key.position for row in table.shared()] == [100]
    assert [row.key.position for row in table.unique_to("early")] == [200]
    assert len(table.rows) == 2
