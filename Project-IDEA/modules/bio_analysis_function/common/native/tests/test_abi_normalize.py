"""原生 reads 规范化的对拍测试。

对拍要求：**输出文件逐字节相同**、五个统计字段相同。这里没有"近似"的余地——
转换是查表、改名是插一个空格，两侧必须一字不差。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.read_normalization import (
    NormalizeConfig,
    normalize_fastq as python_normalize,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)


def write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality}\n")


def sample_records() -> list[tuple[str, str, str]]:
    """四条记录：两条 MGI 形状的名字、两种质量区间。"""
    return [
        ("MGI-1/1", "ACGTACGTAC", "AAAAIIIIII"),
        ("MGI-2/1", "TTTTGGGGCC", "@@@@~~~~~~"),
        ("plain/3", "ACGTNACGTA", "5555??????"),
        ("no_slash", "GGGGCCCCTT", "IIIIIIIIII"),
    ]


def assert_same(native, python_summary) -> None:
    assert native.total_reads == python_summary.total_reads
    assert native.renamed_reads == python_summary.renamed_reads
    assert native.requantified_reads == python_summary.requantified_reads
    assert native.input_bases == python_summary.input_bases
    assert native.output_bases == python_summary.output_bases


def test_native_matches_python_with_phred64(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample_records())
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = abi.normalize_fastq(source, native_out, input_phred=64)
    python_summary = python_normalize(
        source, python_out, config=NormalizeConfig(input_phred=64)
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert_same(native, python_summary)
    assert native.requantified_reads == 4


def test_native_matches_python_with_fix_mgi(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample_records())
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = abi.normalize_fastq(source, native_out, fix_mgi=True)
    python_summary = python_normalize(
        source, python_out, config=NormalizeConfig(fix_mgi=True)
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert_same(native, python_summary)
    assert native.renamed_reads == 2  # 只有两条是 /1 结尾


def test_native_matches_python_with_both(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample_records())
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = abi.normalize_fastq(
        source, native_out, input_phred=64, fix_mgi=True
    )
    python_summary = python_normalize(
        source, python_out, config=NormalizeConfig(input_phred=64, fix_mgi=True)
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert_same(native, python_summary)


def test_native_default_changes_nothing(tmp_path: Path) -> None:
    """默认参数下输出必须与输入**逐字节相同**——它只把数据抄一遍。"""
    source = tmp_path / "in.fq"
    write_fastq(source, sample_records())
    output = tmp_path / "out.fq"

    native = abi.normalize_fastq(source, output)

    assert output.read_bytes() == source.read_bytes()
    assert native.renamed_reads == 0
    assert native.requantified_reads == 0
    assert native.input_bases == native.output_bases


def test_native_matches_python_paired(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, sample_records())
    write_fastq(read2, sample_records())
    native1 = tmp_path / "n_R1.fq"
    native2 = tmp_path / "n_R2.fq"
    python1 = tmp_path / "p_R1.fq"
    python2 = tmp_path / "p_R2.fq"
    settings = NormalizeConfig(input_phred=64, fix_mgi=True)

    native = abi.normalize_fastq(
        read1,
        native1,
        read2_path=read2,
        output2_path=native2,
        input_phred=64,
        fix_mgi=True,
    )
    python_summary = python_normalize(
        read1, python1, read2_path=read2, output2_path=python2, config=settings
    )

    assert native1.read_bytes() == python1.read_bytes()
    assert native2.read_bytes() == python2.read_bytes()
    assert_same(native, python_summary)
    assert native.total_reads == 8  # 双端按 read 条数计


def test_native_handles_gzip(tmp_path: Path) -> None:
    plain = tmp_path / "in.fq"
    write_fastq(plain, sample_records())
    source = tmp_path / "in.fq.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    output = tmp_path / "out.fq"

    abi.normalize_fastq(source, output, input_phred=64)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    # 第二条的质量是 "@@@@~~~~~~"：'@'(Q0) → '!'，'~'(Q62) → '_'
    assert [record.quality for record in read_fastq(output)][1] == b"!" * 4 + b"_" * 6


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "规范化"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    write_fastq(source, sample_records())
    output = folder / "输出.fq"

    native = abi.normalize_fastq(source, output, input_phred=64, fix_mgi=True)

    assert output.exists()
    assert native.requantified_reads == 4


def test_native_rejects_bad_input_phred(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample_records())

    with pytest.raises(ValueError, match="input_phred"):
        abi.normalize_fastq(source, tmp_path / "out.fq", input_phred=13)


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        abi.normalize_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_native_rejects_mismatched_paired_counts(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, sample_records())
    write_fastq(read2, sample_records()[:2])
    out1 = tmp_path / "o1.fq"
    out2 = tmp_path / "o2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.normalize_fastq(read1, out1, read2_path=read2, output2_path=out2)

    assert not out1.exists() and not out2.exists()
