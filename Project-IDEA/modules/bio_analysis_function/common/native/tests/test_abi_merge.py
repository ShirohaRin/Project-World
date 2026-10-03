from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.paired_overlap import GAP_ONLY_VECTOR
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_merging import (
    PairedMergeConfig,
)
from modules.bio_analysis_function.submodules.paired_end_merging import (
    merge_paired_fastq as python_merge,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(), reason="原生库未编译；先运行 common/native/tools/compile.py"
)


def write_fastq(path: Path, records: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def gzip_copy(source: Path, destination: Path) -> None:
    with gzip.open(destination, "wb") as handle:
        handle.write(source.read_bytes())


def test_native_matches_python_and_threads(tmp_path: Path) -> None:
    fragment = b"ACGT" * 25
    r1 = [(f"r{index}/1", fragment[:80]) for index in range(1200)]
    r2 = [(f"r{index}/2", reverse_complement(fragment[20:100])) for index in range(1200)]
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, r1)
    write_fastq(source2, r2)

    native_one = tmp_path / "native-one.fq"
    native_many = tmp_path / "native-many.fq"
    python_output = tmp_path / "python.fq"
    one = abi.merge_paired_fastq(source1, source2, native_one, threads=1)
    many = abi.merge_paired_fastq(source1, source2, native_many, threads=4)
    reference = python_merge(source1, source2, python_output)

    assert native_one.read_bytes() == python_output.read_bytes()
    assert native_many.read_bytes() == native_one.read_bytes()
    assert (many.total_pairs, many.merged_pairs, many.unmerged_pairs) == (
        reference.total_pairs,
        reference.merged_pairs,
        reference.unmerged_pairs,
    )
    assert (many.input_bases, many.output_bases) == (
        reference.input_bases,
        reference.output_bases,
    )
    assert many.gap_overlaps == reference.gap_overlaps
    assert many == one


def test_native_gzip_and_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "双端输入" / "结果"
    folder.mkdir(parents=True)
    fragment = b"ACGT" * 25
    plain1 = folder / "一.fq"
    plain2 = folder / "二.fq"
    write_fastq(plain1, [("中文/1", fragment)])
    write_fastq(plain2, [("中文/2", reverse_complement(fragment))])
    gz1 = folder / "一.fq.gz"
    gz2 = folder / "二.fq.gz"
    gzip_copy(plain1, gz1)
    gzip_copy(plain2, gz2)
    output = folder / "合并.fq"

    summary = abi.merge_paired_fastq(gz1, gz2, output)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.merged_pairs == 1
    assert list(read_fastq(output))[0].sequence == fragment


def test_native_deletes_partial_output_when_pair_counts_differ(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output = tmp_path / "merged.fq"
    write_fastq(source1, [("a", b"A" * 80), ("b", b"A" * 80)])
    write_fastq(source2, [("a", b"C" * 80)])

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.merge_paired_fastq(source1, source2, output, threads=4)

    assert not output.exists()


def test_native_reverses_quality_and_reports_geometry_stats(tmp_path: Path) -> None:
    fragment = b"ACGT" * 25
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output = tmp_path / "merged.fq"
    quality1 = bytes(range(80, 160))
    quality2 = bytes(range(40, 120))
    with source1.open("wb") as handle:
        handle.write(b"@left\n" + fragment[:80] + b"\n+\n" + quality1 + b"\n")
    reverse2 = reverse_complement(fragment[20:100])
    with source2.open("wb") as handle:
        handle.write(b"@right\n" + reverse2 + b"\n+\n" + quality2 + b"\n")

    summary = abi.merge_paired_fastq(source1, source2, output, threads=1)
    merged = list(read_fastq(output))[0]

    assert summary.total_pairs == summary.merged_pairs == 1
    assert summary.unmerged_pairs == 0
    assert summary.input_bases == 160
    assert summary.output_bases == 80
    assert summary.gap_overlaps == 0
    assert merged.name == "left merged_80_0"
    assert merged.sequence == fragment[:80]
    assert merged.quality == quality1


def test_native_rejects_empty_paths() -> None:
    with pytest.raises(ValueError, match="路径不能为空"):
        abi.merge_paired_fastq("", "", "")


def test_native_counts_gap_merges_like_python(tmp_path: Path) -> None:
    vector = GAP_ONLY_VECTOR
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"
    write_fastq(source1, [("gap/1", vector["read1"])])
    write_fastq(source2, [("gap/2", vector["read2"])])
    settings = {
        "diff_limit": vector["diff_limit"],
        "require": vector["require"],
        "diff_percent_limit": 0.2,
        "allow_gap": True,
    }

    native = abi.merge_paired_fastq(source1, source2, native_output, threads=1, **settings)
    reference = python_merge(
        source1, source2, python_output, config=PairedMergeConfig(**settings)
    )

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native.gap_overlaps == reference.gap_overlaps == 1
    assert native.merged_pairs == reference.merged_pairs == 1
