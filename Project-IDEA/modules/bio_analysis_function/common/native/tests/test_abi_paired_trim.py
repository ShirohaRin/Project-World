from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    PairedAdapterTrimConfig,
)
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    trim_paired_fastq as python_trim,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(), reason="原生库未编译；先运行 common/native/tools/compile.py"
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


def through_pairs(count: int, seed: int) -> tuple[list, list]:
    """造 ``count`` 对"片段 100 bp、读长 120 bp、两端都读穿"的 read。"""
    records1: list[tuple[str, bytes]] = []
    records2: list[tuple[str, bytes]] = []
    for index in range(count):
        fragment = random_sequence(100, seed + index)
        records1.append((f"r{index}/1", fragment + ADAPTER[:20]))
        records2.append(
            (f"r{index}/2", reverse_complement(fragment) + reverse_complement(ADAPTER)[:20])
        )
    return records1, records2


def test_native_matches_python_and_threads(tmp_path: Path) -> None:
    """跨批次（900 对）+ 混入不重叠的 read 对：输出逐字节一致、统计一致、线程无关。"""
    records1, records2 = through_pairs(900, seed=1)
    for index in range(20):
        records1.append((f"plain{index}/1", random_sequence(120, 5000 + index)))
        records2.append((f"plain{index}/2", random_sequence(120, 6000 + index)))
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, records1)
    write_fastq(source2, records2)

    native_one = (tmp_path / "n1-one.fq", tmp_path / "n2-one.fq")
    native_many = (tmp_path / "n1-many.fq", tmp_path / "n2-many.fq")
    python_out = (tmp_path / "p1.fq", tmp_path / "p2.fq")

    one = abi.trim_paired_adapter_fastq(source1, source2, *native_one, threads=1)
    many = abi.trim_paired_adapter_fastq(source1, source2, *native_many, threads=4)
    reference = python_trim(source1, source2, *python_out)

    assert native_one[0].read_bytes() == python_out[0].read_bytes()
    assert native_one[1].read_bytes() == python_out[1].read_bytes()
    assert native_many[0].read_bytes() == native_one[0].read_bytes()
    assert native_many[1].read_bytes() == native_one[1].read_bytes()
    assert many == one
    assert (
        many.total_pairs,
        many.trimmed_pairs,
        many.input_bases,
        many.output_bases,
        many.trimmed_bases,
    ) == (
        reference.total_pairs,
        reference.trimmed_pairs,
        reference.input_bases,
        reference.output_bases,
        reference.trimmed_bases,
    )
    # 没裁到的 read 对也要原样出现在两份输出里，不能丢。
    assert many.total_pairs == len(list(read_fastq(native_one[0])))


def test_native_matches_python_with_front_trimmed(tmp_path: Path) -> None:
    """头部补偿量也走原生：R1 剪掉 10 个碱基的情形与 Python 逐字节一致。"""
    records1: list[tuple[str, bytes]] = []
    records2: list[tuple[str, bytes]] = []
    for index in range(64):
        fragment = random_sequence(100, seed=200 + index)
        records1.append((f"r{index}/1", (fragment + ADAPTER[:20])[10:]))
        records2.append(
            (f"r{index}/2", reverse_complement(fragment) + reverse_complement(ADAPTER)[:20])
        )
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, records1)
    write_fastq(source2, records2)
    native_out = (tmp_path / "n1.fq", tmp_path / "n2.fq")
    python_out = (tmp_path / "p1.fq", tmp_path / "p2.fq")
    settings = {"front_trimmed1": 10}

    native = abi.trim_paired_adapter_fastq(
        source1, source2, *native_out, threads=1, **settings
    )
    reference = python_trim(
        source1, source2, *python_out, config=PairedAdapterTrimConfig(**settings)
    )

    assert native_out[0].read_bytes() == python_out[0].read_bytes()
    assert native_out[1].read_bytes() == python_out[1].read_bytes()
    assert native.trimmed_pairs == reference.trimmed_pairs == 64
    assert native.trimmed_bases == reference.trimmed_bases


def test_native_gzip_and_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "双端输入" / "结果"
    folder.mkdir(parents=True)
    fragment = random_sequence(100, seed=11)
    plain1 = folder / "一.fq"
    plain2 = folder / "二.fq"
    write_fastq(plain1, [("中文/1", fragment + ADAPTER[:20])])
    write_fastq(
        plain2,
        [("中文/2", reverse_complement(fragment) + reverse_complement(ADAPTER)[:20])],
    )
    gz1 = folder / "一.fq.gz"
    gz2 = folder / "二.fq.gz"
    for source, target in ((plain1, gz1), (plain2, gz2)):
        with gzip.open(target, "wb") as handle:
            handle.write(source.read_bytes())
    output1 = folder / "裁剪一.fq"
    output2 = folder / "裁剪二.fq"

    summary = abi.trim_paired_adapter_fastq(gz1, gz2, output1, output2)

    assert summary.trimmed_pairs == 1
    assert output1.read_bytes()[:2] == b"\x1f\x8b"
    assert output2.read_bytes()[:2] == b"\x1f\x8b"
    assert list(read_fastq(output1))[0].sequence == fragment


def test_native_deletes_both_partial_outputs_when_pair_counts_differ(
    tmp_path: Path,
) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"
    write_fastq(source1, [("a", b"A" * 120), ("b", b"A" * 120)])
    write_fastq(source2, [("a", b"C" * 120)])

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.trim_paired_adapter_fastq(source1, source2, output1, output2, threads=4)

    assert not output1.exists()
    assert not output2.exists()


def test_native_rejects_empty_paths() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        abi.trim_paired_adapter_fastq("", "", "", "")
