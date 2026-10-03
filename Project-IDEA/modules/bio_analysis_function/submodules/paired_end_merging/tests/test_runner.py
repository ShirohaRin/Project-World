from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_merging import (
    merge_paired_fastq,
)


def write_fastq(path: Path, records: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def test_runner_streams_pairs_writes_only_merged_and_follows_gzip(tmp_path: Path) -> None:
    fragment = b"ACGT" * 25
    r1 = [("one/1", fragment[:80]), ("two/1", b"A" * 80)]
    r2 = [("different/2", reverse_complement(fragment[20:100])), ("two/2", b"C" * 80)]
    plain_r1 = tmp_path / "r1.fq"
    plain_r2 = tmp_path / "r2.fq"
    write_fastq(plain_r1, r1)
    plain_r2.write_bytes(b"".join([]))
    with gzip.open(plain_r2, "wb") as handle:
        temp = tmp_path / "r2-plain.fq"
        write_fastq(temp, r2)
        handle.write(temp.read_bytes())
    output = tmp_path / "merged.fq"

    summary = merge_paired_fastq(plain_r1, plain_r2, output)

    assert summary.total_pairs == 2
    assert summary.merged_pairs == 1
    assert summary.unmerged_pairs == 1
    assert summary.input_bases == 320
    assert summary.output_bases == 80
    assert summary.gap_overlaps == 0
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    merged = list(read_fastq(output))
    assert [(item.name, item.sequence) for item in merged] == [("one/1 merged_80_0", fragment[:80])]


def test_runner_supports_chinese_paths(tmp_path: Path) -> None:
    source1 = tmp_path / "输入一.fq"
    source2 = tmp_path / "输入二.fq"
    output = tmp_path / "输出.fq"
    fragment = b"ACGT" * 25
    write_fastq(source1, [("r1", fragment)])
    write_fastq(source2, [("r2", reverse_complement(fragment))])

    summary = merge_paired_fastq(source1, source2, output)

    assert summary.merged_pairs == 1
    assert list(read_fastq(output))[0].sequence == fragment


def test_runner_deletes_partial_output_when_pair_counts_differ(tmp_path: Path) -> None:
    r1 = tmp_path / "r1.fq"
    r2 = tmp_path / "r2.fq"
    output = tmp_path / "merged.fq"
    write_fastq(r1, [("a", b"A" * 80), ("b", b"A" * 80)])
    write_fastq(r2, [("a", b"C" * 80)])

    with pytest.raises(ValueError, match="记录数不一致"):
        merge_paired_fastq(r1, r2, output)

    assert not output.exists()
