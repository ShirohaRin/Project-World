from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.umi_processing import (
    UmiConfig,
)
from modules.bio_analysis_function.submodules.umi_processing import (
    process_umi_fastq as python_umi,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(), reason="原生库未编译；先运行 common/native/tools/compile.py"
)

UPSTREAM_NAME = "NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA"


def write_fastq(path: Path, records: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def compare_summaries(native, reference) -> None:
    assert native.total_reads == reference.total_reads
    assert native.reads_with_umi == reference.reads_with_umi
    assert native.trimmed_bases == reference.trimmed_bases
    assert native.input_bases == reference.input_bases
    assert native.output_bases == reference.output_bases


def test_native_matches_python_single_end_and_threads(tmp_path: Path) -> None:
    """单端：跨批次（1500 条）逐字节对拍，且线程数不影响结果。"""
    records = [(f"read{index}", b"ACGTACGT" + b"GGGGTTTT") for index in range(1500)]
    source = tmp_path / "r1.fq"
    write_fastq(source, records)
    native_one = tmp_path / "n-one.fq"
    native_many = tmp_path / "n-many.fq"
    python_out = tmp_path / "python.fq"

    one = abi.process_umi_fastq(
        source, native_one, location="read1", length=8, threads=1
    )
    many = abi.process_umi_fastq(
        source, native_many, location="read1", length=8, threads=4
    )
    reference = python_umi(
        source, python_out, config=UmiConfig(location="read1", length=8)
    )

    assert native_one.read_bytes() == python_out.read_bytes()
    assert native_many.read_bytes() == native_one.read_bytes()
    compare_summaries(one, reference)
    compare_summaries(many, reference)
    assert one.total_reads == 1500
    assert one.reads_with_umi == 1500
    assert one.trimmed_bases == 1500 * 8


def test_native_matches_python_paired_end(tmp_path: Path) -> None:
    """双端：index1 模式（不剪序列）与 per_read 模式（剪序列）都对拍。"""
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    write_fastq(source1, [(UPSTREAM_NAME, b"ACGTACGT" + b"GGGG")] * 700)
    write_fastq(source2, [(UPSTREAM_NAME, b"TGCATGCA" + b"TTTT")] * 700)

    for location, length in (("index1", 0), ("per_read", 8)):
        native1 = tmp_path / f"n1-{location}.fq"
        native2 = tmp_path / f"n2-{location}.fq"
        python1 = tmp_path / f"p1-{location}.fq"
        python2 = tmp_path / f"p2-{location}.fq"

        native = abi.process_umi_fastq(
            source1, native1,
            read2_path=source2, output2_path=native2,
            location=location, length=length, threads=2,
        )
        reference = python_umi(
            source1, python1,
            read2_path=source2, output2_path=python2,
            config=UmiConfig(location=location, length=length),
        )

        assert native1.read_bytes() == python1.read_bytes()
        assert native2.read_bytes() == python2.read_bytes()
        compare_summaries(native, reference)
        assert native.total_reads == 1400  # 700 对，按 read 计
        assert native.reads_with_umi == 1400


def test_native_prefix_and_delimiter(tmp_path: Path) -> None:
    source = tmp_path / "r1.fq"
    write_fastq(source, [("read1", b"ACGTACGTGGGG")])
    native_out = tmp_path / "n.fq"
    python_out = tmp_path / "p.fq"
    settings = {"location": "read1", "length": 8, "prefix": "UMI", "delimiter": "/"}

    abi.process_umi_fastq(source, native_out, **settings)
    python_umi(
        source,
        python_out,
        config=UmiConfig(location="read1", length=8, prefix="UMI", delimiter="/"),
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert list(read_fastq(native_out))[0].name == "read1/UMI_ACGTACGT"


def test_native_gzip_and_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "输入" / "结果"
    folder.mkdir(parents=True)
    plain = folder / "一.fq"
    write_fastq(plain, [("中文", b"ACGTACGTGGGG")])
    gz = folder / "一.fq.gz"
    with gzip.open(gz, "wb") as handle:
        handle.write(plain.read_bytes())
    output = folder / "结果.fq"

    summary = abi.process_umi_fastq(gz, output, location="read1", length=8)

    assert summary.reads_with_umi == 1
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert list(read_fastq(output))[0].name == "中文:ACGTACGT"


def test_native_deletes_partial_outputs_when_pair_counts_differ(
    tmp_path: Path,
) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"
    write_fastq(source1, [("a", b"ACGTACGT"), ("b", b"ACGTACGT")])
    write_fastq(source2, [("a", b"TGCATGCA")])

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.process_umi_fastq(
            source1, output1, read2_path=source2, output2_path=output2, threads=2
        )

    assert not output1.exists()
    assert not output2.exists()


def test_native_rejects_half_given_paired_paths(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    write_fastq(source1, [("a", b"ACGTACGT")])

    with pytest.raises(ValueError, match="必须同时给出"):
        abi.process_umi_fastq(source1, tmp_path / "o1.fq", read2_path=tmp_path / "r2.fq")


def test_native_rejects_empty_paths_and_bad_location(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        abi.process_umi_fastq("", "")
    with pytest.raises(ValueError, match="未知的 UMI 来源"):
        abi.process_umi_fastq(tmp_path / "r1.fq", tmp_path / "o.fq", location="nowhere")
