"""文件级接口的测试。

重点守四件事：

1. **只评估模式不产出任何文件**，且不该动同名旧文件；
2. **去重保留第一次出现的那条**（顺序相关，不是"随机留一条"）；
3. **双端成对丢弃**，两份输出始终对齐；
4. 失败时**所有半成品都被删除**。

所有用例都显式传 ``buffer_bytes``：默认档位要 1 GiB 位图，测试没必要花那个内存。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.deduplication import deduplicate_fastq

_Q40 = chr(40 + 33)  # 'I'，质量字符（本算法不看质量，填个合法的即可）

#: 测试统一的位图大小（64 KiB）。
_SMALL_BUFFER = 1 << 16

_DUPLICATED = "ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT"
_OTHER = "TTTTGGGGCCCCAAAATTTTGGGGCCCCAAAATTTTGGGG"
_THIRD = "GATTACAGATTACAGATTACAGATTACAGATTACAGATTA"


def write_fastq_file(path: Path, records: list[tuple[str, str]]) -> None:
    """按 (名字, 序列) 写一份 FASTQ，质量统一用 Q40。"""
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence in records:
            handle.write(f"@{name}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


def sample_records() -> list[tuple[str, str]]:
    """四条记录：第 1 与第 3 条序列相同，另两条各自唯一。"""
    return [
        ("read_1", _DUPLICATED),
        ("read_2", _OTHER),
        ("read_3", _DUPLICATED),  # 与 read_1 重复
        ("read_4", _THIRD),
    ]


# ---------------------------------------------------------------------------
# 单端
# ---------------------------------------------------------------------------


def test_analysis_only_writes_nothing(tmp_path: Path) -> None:
    """不给输出路径 = 只评估：不产出文件，也没丢任何东西。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    summary = deduplicate_fastq(source, buffer_bytes=_SMALL_BUFFER)

    assert summary.dedup is False
    assert summary.total_reads == 4
    assert summary.duplicate_reads == 1
    assert summary.kept_reads == 4  # 没丢东西
    assert summary.dropped_reads == 0
    assert summary.duplicate_rate == 0.25
    # 目录里只有输入那一个文件。
    assert sorted(item.name for item in tmp_path.iterdir()) == ["reads.fq"]


def test_analysis_only_leaves_existing_file_alone(tmp_path: Path) -> None:
    """只评估时不能去删一个同名的既有文件（它跟本次运行毫无关系）。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    bystander = tmp_path / "unrelated.fq"
    bystander.write_text("keep me\n", encoding="ascii")

    deduplicate_fastq(source, buffer_bytes=_SMALL_BUFFER)

    assert bystander.read_text(encoding="ascii") == "keep me\n"


def test_dedup_keeps_first_occurrence(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "deduped.fq"

    summary = deduplicate_fastq(
        source, output, buffer_bytes=_SMALL_BUFFER
    )

    assert summary.dedup is True
    assert summary.total_reads == 4
    assert summary.duplicate_reads == 1
    assert summary.dropped_reads == 1
    assert summary.kept_reads == 3
    # 留下的是"第一次出现"的那条（read_1），不是 read_3。
    assert [record.name for record in read_fastq(output)] == [
        "read_1",
        "read_2",
        "read_4",
    ]


def test_dedup_bases_follow_what_is_written(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "deduped.fq"

    summary = deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    written = sum(record.length for record in read_fastq(output))
    assert summary.bases_after == written
    assert summary.bases_before == 4 * len(_DUPLICATED)
    assert summary.bases_before - summary.bases_after == len(_DUPLICATED)


def test_dedup_does_not_rewrite_sequences(tmp_path: Path) -> None:
    """去重只决定去留，写出的记录与输入逐字节一致。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, [("keep", _OTHER), ("drop", _OTHER)])
    output = tmp_path / "deduped.fq"

    deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert output.read_bytes() == (
        f"@keep\n{_OTHER}\n+\n{_Q40 * len(_OTHER)}\n".encode("ascii")
    )


def test_all_equal_reads_keep_only_one(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, [(f"read_{index}", _DUPLICATED) for index in range(5)])
    output = tmp_path / "deduped.fq"

    summary = deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert (summary.total_reads, summary.duplicate_reads, summary.kept_reads) == (5, 4, 1)
    assert len(list(read_fastq(output))) == 1


def test_all_distinct_reads_are_kept(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(
        source,
        [(f"read_{index}", _DUPLICATED[: 20 + index] + "A" * index) for index in range(8)],
    )
    output = tmp_path / "deduped.fq"

    summary = deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert summary.duplicate_reads == 0
    assert len(list(read_fastq(output))) == 8


def test_empty_input(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")
    output = tmp_path / "deduped.fq"

    summary = deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert summary.total_reads == 0
    assert summary.duplicate_rate == 0.0
    assert summary.kept_rate == 0.0
    assert output.read_bytes() == b""


def test_output_parent_directory_is_created(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "结果" / "去重后" / "clean.fq"

    deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert output.exists()


def test_compress_follows_input(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    plain = tmp_path / "plain.fq"
    write_fastq_file(plain, sample_records())
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    output = tmp_path / "deduped.fq"

    deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert [record.name for record in read_fastq(output)] == [
        "read_1",
        "read_2",
        "read_4",
    ]


def test_compress_can_be_forced_off(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    plain = tmp_path / "plain.fq"
    write_fastq_file(plain, sample_records())
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    output = tmp_path / "deduped.fq"

    deduplicate_fastq(source, output, compress=False, buffer_bytes=_SMALL_BUFFER)

    assert output.read_bytes()[:1] == b"@"


# ---------------------------------------------------------------------------
# 双端
# ---------------------------------------------------------------------------


def write_paired(tmp_path: Path) -> tuple[Path, Path]:
    """三对 read，其中第 2 对与第 1 对完全相同。"""
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(
        read1,
        [("pair1/1", _DUPLICATED), ("pair2/1", _DUPLICATED), ("pair3/1", _THIRD)],
    )
    write_fastq_file(
        read2,
        [("pair1/2", _OTHER), ("pair2/2", _OTHER), ("pair3/2", _THIRD)],
    )
    return read1, read2


def test_paired_dedup_drops_both_sides(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path)
    output1 = tmp_path / "clean_R1.fq"
    output2 = tmp_path / "clean_R2.fq"

    summary = deduplicate_fastq(
        read1,
        output1,
        read2_path=read2,
        output2_path=output2,
        buffer_bytes=_SMALL_BUFFER,
    )

    assert summary.paired is True
    assert (summary.total_reads, summary.duplicate_reads, summary.kept_reads) == (3, 1, 2)
    assert [record.name for record in read_fastq(output1)] == ["pair1/1", "pair3/1"]
    assert [record.name for record in read_fastq(output2)] == ["pair1/2", "pair3/2"]


def test_paired_analysis_only_writes_nothing(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path)

    summary = deduplicate_fastq(
        read1, read2_path=read2, buffer_bytes=_SMALL_BUFFER
    )

    assert summary.paired is True
    assert summary.dedup is False
    assert summary.duplicate_reads == 1
    assert sorted(item.name for item in tmp_path.iterdir()) == ["R1.fq", "R2.fq"]


def test_paired_swapped_reads_are_not_duplicates(tmp_path: Path) -> None:
    """R1/R2 调换不算同一对——上游把两条首尾相接后哈希，位置不同。"""
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(read1, [("a/1", _DUPLICATED), ("b/1", _OTHER)])
    write_fastq_file(read2, [("a/2", _OTHER), ("b/2", _DUPLICATED)])
    output1 = tmp_path / "clean_R1.fq"
    output2 = tmp_path / "clean_R2.fq"

    summary = deduplicate_fastq(
        read1,
        output1,
        read2_path=read2,
        output2_path=output2,
        buffer_bytes=_SMALL_BUFFER,
    )

    assert summary.duplicate_reads == 0
    assert len(list(read_fastq(output1))) == 2


def test_paired_mismatched_record_counts_removes_both_outputs(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(read1, [("a/1", _DUPLICATED), ("b/1", _OTHER)])
    write_fastq_file(read2, [("a/2", _OTHER)])
    output1 = tmp_path / "clean_R1.fq"
    output2 = tmp_path / "clean_R2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        deduplicate_fastq(
            read1,
            output1,
            read2_path=read2,
            output2_path=output2,
            buffer_bytes=_SMALL_BUFFER,
        )

    assert not output1.exists()
    assert not output2.exists()


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


def test_missing_input_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        deduplicate_fastq(tmp_path / "absent.fq", buffer_bytes=_SMALL_BUFFER)
    with pytest.raises(ValueError, match="文件不存在"):
        deduplicate_fastq(
            tmp_path / "absent.fq",
            read2_path=tmp_path / "absent2.fq",
            buffer_bytes=_SMALL_BUFFER,
        )


def test_paired_output_paths_must_be_given_together(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path)
    output1 = tmp_path / "clean_R1.fq"
    output2 = tmp_path / "clean_R2.fq"

    with pytest.raises(ValueError, match="同时给出两份输出路径"):
        deduplicate_fastq(
            read1, output1, read2_path=read2, buffer_bytes=_SMALL_BUFFER
        )
    with pytest.raises(ValueError, match="同时给出两份输出路径"):
        deduplicate_fastq(
            read1, read2_path=read2, output2_path=output2, buffer_bytes=_SMALL_BUFFER
        )


def test_second_output_without_second_input_rejected(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    with pytest.raises(ValueError, match="第二份输出路径"):
        deduplicate_fastq(
            source,
            tmp_path / "out.fq",
            output2_path=tmp_path / "out2.fq",
            buffer_bytes=_SMALL_BUFFER,
        )


def test_broken_input_removes_partial_output(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下残缺的输出文件。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        deduplicate_fastq(broken, output, buffer_bytes=_SMALL_BUFFER)

    assert not output.exists()


def test_broken_input_removes_both_paired_outputs(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(read1, [("a/1", _DUPLICATED)])
    read2.write_bytes(b"@a/2\nACGT\n+\n")
    output1 = tmp_path / "clean_R1.fq"
    output2 = tmp_path / "clean_R2.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        deduplicate_fastq(
            read1,
            output1,
            read2_path=read2,
            output2_path=output2,
            buffer_bytes=_SMALL_BUFFER,
        )

    assert not output1.exists()
    assert not output2.exists()


def test_default_accuracy_levels_follow_mode(tmp_path: Path) -> None:
    """只评估取档位 1、去重取档位 3，大小都在 summary 里如实报出。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    analyzed = deduplicate_fastq(source, buffer_bytes=_SMALL_BUFFER)
    assert analyzed.accuracy_level == 1

    deduped = deduplicate_fastq(
        source, tmp_path / "out.fq", buffer_bytes=_SMALL_BUFFER
    )
    assert deduped.accuracy_level == 3


def test_render_mentions_mode(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    analyzed = deduplicate_fastq(source, buffer_bytes=_SMALL_BUFFER)
    assert "未去重" in analyzed.render()

    deduped = deduplicate_fastq(
        source, tmp_path / "out.fq", buffer_bytes=_SMALL_BUFFER
    )
    assert "未去重" not in deduped.render()
    assert "丢弃" in deduped.render()
