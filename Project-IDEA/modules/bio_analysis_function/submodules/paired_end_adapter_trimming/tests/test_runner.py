from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    trim_paired_fastq,
)

ADAPTER = b"AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"


def random_sequence(length: int, seed: int) -> bytes:
    return bytes(random.Random(seed).choices(b"ACGT", k=length))


def write_fastq(path: Path, records: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def test_trims_pairs_and_writes_both_files(tmp_path: Path) -> None:
    """一对读到接头就裁，另一对没有接头则原样写出——两条输出始终成对。"""
    fragment = random_sequence(100, seed=1)
    through1 = fragment + ADAPTER[:20]
    through2 = reverse_complement(fragment) + reverse_complement(ADAPTER)[:20]
    plain1 = random_sequence(120, seed=2)
    plain2 = random_sequence(120, seed=3)
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"
    write_fastq(source1, [("p1/1", through1), ("p2/1", plain1)])
    write_fastq(source2, [("p1/2", through2), ("p2/2", plain2)])

    summary = trim_paired_fastq(source1, source2, output1, output2)

    assert summary.total_pairs == 2
    assert summary.trimmed_pairs == 1
    assert summary.trimmed_reads == 2
    assert summary.trimmed_bases == 40
    # 输入 2 对 × (120 + 120)；裁掉第一对的两段接头 20 + 20。
    assert summary.input_bases == 480
    assert summary.output_bases == 440
    assert summary.top_adapters[0][0] == ADAPTER[:20]
    assert summary.top_adapters[0][1] == 1

    trimmed1 = list(read_fastq(output1))
    trimmed2 = list(read_fastq(output2))
    assert [item.name for item in trimmed1] == ["p1/1", "p2/1"]
    assert [item.name for item in trimmed2] == ["p1/2", "p2/2"]
    assert trimmed1[0].sequence == fragment
    assert trimmed1[1].sequence == plain1
    assert trimmed2[1].sequence == plain2


def test_follows_gzip_from_either_input(tmp_path: Path) -> None:
    """任一输入是 gzip，两个输出都用 gzip（按魔数判断，不看扩展名）。"""
    fragment = random_sequence(100, seed=4)
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2-plain.fq"
    write_fastq(source1, [("p/1", fragment + ADAPTER[:20])])
    plain2 = tmp_path / "tmp.fq"
    write_fastq(
        plain2, [("p/2", reverse_complement(fragment) + reverse_complement(ADAPTER)[:20])]
    )
    with gzip.open(source2, "wb") as handle:
        handle.write(plain2.read_bytes())
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"

    summary = trim_paired_fastq(source1, source2, output1, output2)

    assert summary.trimmed_pairs == 1
    assert output1.read_bytes()[:2] == b"\x1f\x8b"
    assert output2.read_bytes()[:2] == b"\x1f\x8b"
    assert list(read_fastq(output1))[0].sequence == fragment


def test_supports_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "双端输入" / "结果"
    folder.mkdir(parents=True)
    fragment = random_sequence(90, seed=5)
    source1 = folder / "一.fq"
    source2 = folder / "二.fq"
    write_fastq(source1, [("中文/1", fragment + ADAPTER[:20])])
    write_fastq(
        source2,
        [("中文/2", reverse_complement(fragment) + reverse_complement(ADAPTER)[:20])],
    )

    summary = trim_paired_fastq(source1, source2, folder / "一.裁剪.fq", folder / "二.裁剪.fq")

    assert summary.trimmed_pairs == 1
    assert list(read_fastq(folder / "一.裁剪.fq"))[0].sequence == fragment


def test_deletes_both_partial_outputs_when_pair_counts_differ(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "out1.fq"
    output2 = tmp_path / "out2.fq"
    write_fastq(source1, [("a", b"A" * 80), ("b", b"A" * 80)])
    write_fastq(source2, [("a", b"C" * 80)])

    with pytest.raises(ValueError, match="记录数不一致"):
        trim_paired_fastq(source1, source2, output1, output2)

    assert not output1.exists()
    assert not output2.exists()


def test_reports_missing_input(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    write_fastq(source1, [("a", b"A" * 80)])

    with pytest.raises(ValueError, match="文件不存在"):
        trim_paired_fastq(source1, tmp_path / "missing.fq", tmp_path / "o1.fq", tmp_path / "o2.fq")
