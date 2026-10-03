"""原生插入片段长度统计的对拍测试。

与 Python 版逐字段对拍：直方图**逐桶相等**、峰值相同、总数相同。
桶的边界（判不出 → 溢出桶、恰好等于上限留在自己的桶）是最容易写错的地方，
所以用例里专门造了这三种情形。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.paired_overlap import OverlapConfig
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.insert_size_distribution import (
    InsertSizeConfig,
    analyze_insert_size as python_analyze,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

_Q40 = chr(40 + 33)

#: 非周期模板：周期串会让两条 read 完全相同，重叠位置就不可信了。
_TEMPLATE = "".join(random.Random(20260922).choices("ACGT", k=400))


def write_paired(
    folder: Path, fragment_length: int, read_length: int, count: int
) -> tuple[Path, Path]:
    """造 ``count`` 对 read，让重叠分析推出来的片段长度就是 ``fragment_length``。"""
    template = _TEMPLATE[:fragment_length]
    read1_text = template[:read_length]
    read2_text = reverse_complement(template[-read_length:].encode()).decode()
    read1 = folder / "R1.fq"
    read2 = folder / "R2.fq"
    with read1.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            handle.write(f"@read{index}/1\n{read1_text}\n+\n{_Q40 * read_length}\n")
    with read2.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            handle.write(f"@read{index}/2\n{read2_text}\n+\n{_Q40 * read_length}\n")
    return read1, read2


def assert_same(native: abi.NativeInsertSizeSummary, python_summary) -> None:
    assert native.total_pairs == python_summary.total_pairs
    assert native.overlapped_pairs == python_summary.overlapped_pairs
    assert native.max_size == python_summary.max_size
    assert native.peak_size == python_summary.peak_size
    assert native.histogram == python_summary.histogram


def test_native_matches_python_on_located_peak(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, 160, 120, 5)

    native = abi.analyze_insert_size(read1, read2)
    python_summary = python_analyze(read1, read2)

    assert_same(native, python_summary)
    assert native.peak_size == 160


def test_native_matches_python_with_unoverlapped_pairs(tmp_path: Path) -> None:
    """掺入判不出片段长度的对：它们必须进同一个溢出桶。"""
    read1, read2 = write_paired(tmp_path, 160, 120, 4)
    unrelated1 = "ACGT" * 50
    unrelated2 = "TTTT" * 50
    with read1.open("a", encoding="ascii", newline="\n") as handle:
        handle.write(f"@x/1\n{unrelated1}\n+\n{_Q40 * len(unrelated1)}\n")
    with read2.open("a", encoding="ascii", newline="\n") as handle:
        handle.write(f"@x/2\n{unrelated2}\n+\n{_Q40 * len(unrelated2)}\n")

    native = abi.analyze_insert_size(read1, read2)
    python_summary = python_analyze(read1, read2)

    assert_same(native, python_summary)
    assert native.unknown_pairs >= 1


def test_native_matches_python_with_small_limit(tmp_path: Path) -> None:
    """把上限压到很小：溢出桶的分配逻辑（判据是严格大于）必须一致。"""
    read1, read2 = write_paired(tmp_path, 160, 120, 6)

    native = abi.analyze_insert_size(read1, read2, max_size=100)
    python_summary = python_analyze(
        read1, read2, config=InsertSizeConfig(max_size=100)
    )

    assert_same(native, python_summary)
    assert native.histogram[100] == 6  # 片段 160 超过上限 100 → 全进溢出桶


def test_native_matches_python_with_strict_overlap(tmp_path: Path) -> None:
    """收紧重叠要求会改变"判得出来"的比例，两侧必须同步变化。"""
    read1, read2 = write_paired(tmp_path, 160, 120, 5)

    native = abi.analyze_insert_size(read1, read2, require=100)
    python_summary = python_analyze(
        read1, read2, overlap=OverlapConfig(require=100)
    )

    assert_same(native, python_summary)


def test_native_reports_missing_input(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, 160, 120, 1)

    with pytest.raises(ValueError, match="不存在"):
        abi.analyze_insert_size(read1, tmp_path / "absent.fq")


def test_native_rejects_mismatched_counts(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, 160, 120, 3)
    text = read2.read_text(encoding="ascii").split("@read1/2")[0]
    read2.write_text(text, encoding="ascii")

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.analyze_insert_size(read1, read2)


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "片段统计"
    folder.mkdir(parents=True)
    read1, read2 = write_paired(folder, 160, 120, 3)

    native = abi.analyze_insert_size(read1, read2)
    python_summary = python_analyze(read1, read2)

    assert_same(native, python_summary)
