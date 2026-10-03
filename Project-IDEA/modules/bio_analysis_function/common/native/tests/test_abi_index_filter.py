"""原生按 index 过滤的对拍测试。

对拍要求：**输出文件逐字节相同** + 四个统计字段相同。判定涉及名字解析，
所以用例里同时覆盖"名字里有 index"与"名字里没有 index"（后者是陷阱：
它会被任何非空黑名单全丢）。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.index_filtering import (
    IndexFilterConfig,
    filter_by_index_fastq as python_filter,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

_Q40 = chr(40 + 33)


def write_fastq(path: Path, records: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence in records:
            handle.write(f"@{name}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


def sample() -> list[tuple[str, str]]:
    """两条"该留"、两条"该丢"，外加一条名字里没有 index 的。"""
    return [
        ("keep1 1:N:0:GGGGGGGG+TTTTGGGG", "ACGT" * 10),
        ("drop1 1:N:0:ACGTACGT+CCCCCCCC", "TTTT" * 10),
        ("keep2 1:N:0:TTTTTTTT+GGGGGGGG", "GGGG" * 10),
        ("drop2 1:N:0:ACGTACGT+AAAAAAA", "CCCC" * 10),
        ("no_index_at_all", "AAAA" * 10),
    ]


def assert_same(native, python_summary) -> None:
    assert native.total_reads == python_summary.total_reads
    assert native.filtered_reads == python_summary.filtered_reads
    assert native.input_bases == python_summary.input_bases
    assert native.output_bases == python_summary.output_bases


def test_native_matches_python_single_end(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample())
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = abi.filter_by_index_fastq(
        source, native_out, blacklist1=["ACGTACGT"]
    )
    python_summary = python_filter(
        source,
        python_out,
        config=IndexFilterConfig(blacklist1=("ACGTACGT",)),
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert_same(native, python_summary)
    # 两条 drop + 一条没有 index 的（陷阱）都该被丢掉。
    assert native.filtered_reads == 3


def test_native_matches_python_with_threshold(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample())
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = abi.filter_by_index_fastq(
        source, native_out, blacklist1=["ACGTACGA"], threshold=1
    )
    python_summary = python_filter(
        source,
        python_out,
        config=IndexFilterConfig(blacklist1=("ACGTACGA",), threshold=1),
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert_same(native, python_summary)
    assert native.filtered_reads >= 2  # 至少那两条差一位的


def test_native_matches_python_paired(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, sample())
    write_fastq(read2, sample())
    native1 = tmp_path / "n_R1.fq"
    native2 = tmp_path / "n_R2.fq"
    python1 = tmp_path / "p_R1.fq"
    python2 = tmp_path / "p_R2.fq"

    native = abi.filter_by_index_fastq(
        read1,
        native1,
        read2_path=read2,
        output2_path=native2,
        blacklist1=["ACGTACGT"],
    )
    python_summary = python_filter(
        read1,
        python1,
        read2_path=read2,
        output2_path=python2,
        config=IndexFilterConfig(blacklist1=("ACGTACGT",)),
    )

    assert native1.read_bytes() == python1.read_bytes()
    assert native2.read_bytes() == python2.read_bytes()
    assert_same(native, python_summary)
    assert native.paired is True
    assert len(list(read_fastq(native1))) == len(list(read_fastq(native2)))


def test_native_without_blacklist_copies_everything(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, sample())
    output = tmp_path / "out.fq"

    native = abi.filter_by_index_fastq(source, output)

    assert output.read_bytes() == source.read_bytes()
    assert native.filtered_reads == 0
    assert native.input_bases == native.output_bases


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "拆样"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    write_fastq(source, sample())
    output = folder / "输出.fq"

    native = abi.filter_by_index_fastq(
        source, output, blacklist1=["ACGTACGT"]
    )

    assert output.exists()
    assert native.filtered_reads == 3


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        abi.filter_by_index_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_native_rejects_mismatched_paired_counts(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, sample())
    write_fastq(read2, sample()[:2])
    out1 = tmp_path / "o1.fq"
    out2 = tmp_path / "o2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.filter_by_index_fastq(
            read1, out1, read2_path=read2, output2_path=out2
        )

    assert not out1.exists() and not out2.exists()
