from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.sequences import complement, reverse_complement
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    UPSTREAM_TEST_VECTOR,
)
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    correct_paired_fastq as python_correct,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(), reason="原生库未编译；先运行 common/native/tools/compile.py"
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


def other_base(base: int) -> int:
    return ord("A") if base != ord("A") else ord("C")


def correction_pairs(count: int, seed: int) -> tuple[list, list]:
    """造 ``count`` 对，每对在重叠区第 0 位埋一个"R2 低质量 + 错配"。"""
    records1: list[tuple[str, bytes, bytes]] = []
    records2: list[tuple[str, bytes, bytes]] = []
    for index in range(count):
        fragment = random_sequence(100, seed + index)
        read1 = fragment[:80]
        read2 = bytearray(reverse_complement(fragment[20:100]))
        read2[79] = other_base(complement(read1[20]))
        quality2 = bytearray([GOOD] * len(read2))
        quality2[79] = BAD
        records1.append((f"r{index}/1", read1, bytes([GOOD] * len(read1))))
        records2.append((f"r{index}/2", bytes(read2), bytes(quality2)))
    return records1, records2


def test_native_reproduces_upstream_vector(tmp_path: Path) -> None:
    """上游 BaseCorrector::test() 的向量也走原生：序列与质量都要逐位一致。"""
    vector = UPSTREAM_TEST_VECTOR
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, [("name", vector["read1"], vector["quality1"])])
    write_fastq(source2, [("name", vector["read2"], vector["quality2"])])
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"

    summary = abi.correct_paired_fastq(
        source1,
        source2,
        output1,
        output2,
        diff_limit=vector["diff_limit"],
        require=vector["require"],
        diff_percent_limit=vector["diff_percent_limit"],
    )

    corrected1 = list(read_fastq(output1))[0]
    corrected2 = list(read_fastq(output2))[0]
    assert corrected1.sequence == vector["expect_read1"]
    assert corrected1.quality == vector["expect_quality1"]
    assert corrected2.sequence == vector["expect_read2"]
    assert corrected2.quality == vector["expect_quality2"]
    assert summary.corrected_bases == 2
    assert summary.corrected_reads == 2


def test_native_matches_python_and_threads(tmp_path: Path) -> None:
    """跨批次（900 对）+ 混入无错配的 read 对：输出逐字节一致、统计一致、线程无关。"""
    records1, records2 = correction_pairs(900, seed=1)
    for index in range(20):
        fragment = random_sequence(100, seed=9000 + index)
        records1.append((f"p{index}/1", fragment[:80], bytes([GOOD] * 80)))
        records2.append(
            (f"p{index}/2", reverse_complement(fragment[20:100]), bytes([GOOD] * 80))
        )
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, records1)
    write_fastq(source2, records2)

    native_one = (tmp_path / "n1-one.fq", tmp_path / "n2-one.fq")
    native_many = (tmp_path / "n1-many.fq", tmp_path / "n2-many.fq")
    python_out = (tmp_path / "p1.fq", tmp_path / "p2.fq")

    one = abi.correct_paired_fastq(source1, source2, *native_one, threads=1)
    many = abi.correct_paired_fastq(source1, source2, *native_many, threads=4)
    reference = python_correct(source1, source2, *python_out)

    assert native_one[0].read_bytes() == python_out[0].read_bytes()
    assert native_one[1].read_bytes() == python_out[1].read_bytes()
    assert native_many[0].read_bytes() == native_one[0].read_bytes()
    assert native_many[1].read_bytes() == native_one[1].read_bytes()
    assert many == one
    assert (
        many.total_pairs,
        many.corrected_pairs,
        many.corrected_reads,
        many.corrected_bases,
        many.input_bases,
        many.output_bases,
    ) == (
        reference.total_pairs,
        reference.corrected_pairs,
        reference.corrected_reads,
        reference.corrected_bases,
        reference.input_bases,
        reference.output_bases,
    )
    # 校正不改长度：前后碱基数相等，且没有丢掉任何 read。
    assert many.input_bases == many.output_bases
    assert many.total_pairs == len(list(read_fastq(native_one[0])))


def test_native_gzip_and_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "双端输入" / "结果"
    folder.mkdir(parents=True)
    fragment = random_sequence(100, seed=11)
    plain1 = folder / "一.fq"
    plain2 = folder / "二.fq"
    write_fastq(plain1, [("中文/1", fragment[:80], bytes([GOOD] * 80))])
    write_fastq(
        plain2,
        [("中文/2", reverse_complement(fragment[20:100]), bytes([GOOD] * 80))],
    )
    gz1 = folder / "一.fq.gz"
    gz2 = folder / "二.fq.gz"
    for source, target in ((plain1, gz1), (plain2, gz2)):
        with gzip.open(target, "wb") as handle:
            handle.write(source.read_bytes())
    output1 = folder / "校正一.fq"
    output2 = folder / "校正二.fq"

    summary = abi.correct_paired_fastq(gz1, gz2, output1, output2)

    assert summary.total_pairs == 1
    assert output1.read_bytes()[:2] == b"\x1f\x8b"
    assert output2.read_bytes()[:2] == b"\x1f\x8b"
    assert list(read_fastq(output1))[0].sequence == fragment[:80]


def test_native_deletes_both_partial_outputs_when_pair_counts_differ(
    tmp_path: Path,
) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"
    write_fastq(source1, [("a", b"A" * 80, b"E" * 80), ("b", b"A" * 80, b"E" * 80)])
    write_fastq(source2, [("a", b"C" * 80, b"E" * 80)])

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.correct_paired_fastq(source1, source2, output1, output2, threads=4)

    assert not output1.exists()
    assert not output2.exists()


def test_native_rejects_empty_paths() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        abi.correct_paired_fastq("", "", "", "")
