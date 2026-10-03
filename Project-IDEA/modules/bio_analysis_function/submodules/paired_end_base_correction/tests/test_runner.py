from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.sequences import complement, reverse_complement
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    correct_paired_fastq,
)

GOOD = ord("E")
BAD = ord("/")


def random_sequence(length: int, seed: int) -> bytes:
    return bytes(random.Random(seed).choices(b"ACGT", k=length))


def write_fastq(path: Path, records: list[tuple[str, bytes, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence, quality in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(quality + b"\n")


def forward_pair(seed: int) -> tuple[bytes, bytes]:
    """片段 100、读长 80：``offset = 20``，重叠区第 i 位对应 R1[20+i] 与 R2[79-i]。"""
    fragment = random_sequence(100, seed)
    return fragment[:80], reverse_complement(fragment[20:100])


def other_base(base: int) -> int:
    return ord("A") if base != ord("A") else ord("C")


def test_corrects_pairs_and_writes_both_files(tmp_path: Path) -> None:
    """一对可修、一对无错配：两条输出始终成对，统计只算真正改过的那一对。"""
    read1, read2 = forward_pair(seed=1)
    broken = bytearray(read2)
    broken[79] = other_base(complement(read1[20]))
    quality1 = bytes([GOOD] * len(read1))
    quality2 = bytearray([GOOD] * len(read2))
    quality2[79] = BAD

    plain1 = random_sequence(80, seed=2)
    plain2 = reverse_complement(random_sequence(100, seed=2)[20:100])

    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"
    write_fastq(source1, [("p1/1", read1, quality1), ("p2/1", plain1, bytes([GOOD] * 80))])
    write_fastq(
        source2,
        [("p1/2", bytes(broken), bytes(quality2)), ("p2/2", plain2, bytes([GOOD] * 80))],
    )

    summary = correct_paired_fastq(source1, source2, output1, output2)

    assert summary.total_pairs == 2
    assert summary.corrected_pairs == 1
    assert summary.corrected_reads == 1
    assert summary.corrected_bases == 1
    # 校正不改长度，因此前后碱基数必须相等。
    assert summary.input_bases == summary.output_bases == 320

    records1 = list(read_fastq(output1))
    records2 = list(read_fastq(output2))
    assert [item.name for item in records1] == ["p1/1", "p2/1"]
    assert [item.name for item in records2] == ["p1/2", "p2/2"]
    # 第一条的 R2 被修正，R1 原样；第二条一对都没动。
    assert records1[0].sequence == read1
    assert records2[0].sequence[79] == complement(read1[20])
    assert records2[0].quality[79] == GOOD
    assert records1[1].sequence == plain1
    assert records2[1].sequence == plain2


def test_follows_gzip_from_either_input(tmp_path: Path) -> None:
    read1, read2 = forward_pair(seed=5)
    source1 = tmp_path / "r1.fq"
    plain2 = tmp_path / "tmp.fq"
    write_fastq(source1, [("p/1", read1, bytes([GOOD] * len(read1)))])
    write_fastq(plain2, [("p/2", read2, bytes([GOOD] * len(read2)))])
    source2 = tmp_path / "r2.fq.gz"
    with gzip.open(source2, "wb") as handle:
        handle.write(plain2.read_bytes())
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"

    summary = correct_paired_fastq(source1, source2, output1, output2)

    assert summary.total_pairs == 1
    assert output1.read_bytes()[:2] == b"\x1f\x8b"
    assert output2.read_bytes()[:2] == b"\x1f\x8b"
    assert list(read_fastq(output1))[0].sequence == read1


def test_supports_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "双端输入" / "结果"
    folder.mkdir(parents=True)
    read1, read2 = forward_pair(seed=6)
    source1 = folder / "一.fq"
    source2 = folder / "二.fq"
    write_fastq(source1, [("中文/1", read1, bytes([GOOD] * len(read1)))])
    write_fastq(source2, [("中文/2", read2, bytes([GOOD] * len(read2)))])

    summary = correct_paired_fastq(
        source1, source2, folder / "一.校正.fq", folder / "二.校正.fq"
    )

    assert summary.total_pairs == 1
    assert summary.corrected_pairs == 0
    assert list(read_fastq(folder / "一.校正.fq"))[0].sequence == read1


def test_deletes_both_partial_outputs_when_pair_counts_differ(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"
    write_fastq(source1, [("a", b"A" * 80, b"E" * 80), ("b", b"A" * 80, b"E" * 80)])
    write_fastq(source2, [("a", b"C" * 80, b"E" * 80)])

    with pytest.raises(ValueError, match="记录数不一致"):
        correct_paired_fastq(source1, source2, output1, output2)

    assert not output1.exists()
    assert not output2.exists()


def test_reports_missing_input(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    write_fastq(source1, [("a", b"A" * 80, b"E" * 80)])

    with pytest.raises(ValueError, match="文件不存在"):
        correct_paired_fastq(
            source1, tmp_path / "missing.fq", tmp_path / "o1.fq", tmp_path / "o2.fq"
        )
