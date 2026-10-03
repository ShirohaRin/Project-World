"""原生质量统计的正确性测试：与 Python 实现逐位对拍。

统计的产物是**曲线**，所以"对拍"这件事比别的算法更细：不只是几个总数相等，
而是**每一条曲线的每一个点都完全相等**（double 的 `==`，不留容差）。
两侧用的是同一套运算顺序（同样的整数累加、同样的除法），因此这个要求是能达到的——
达不到就说明有一侧的某个累加或某个分桶写错了。

原生库未编译时整个文件跳过（而不是失败）。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.read_stats import stat_fastq

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

_Q40 = chr(40 + 33)


def _write_synthetic(path: Path, count: int, seed: int) -> None:
    """造一份"读长不一、质量随位置衰减、掺 N"的合成 FASTQ。

    读长不一能验证 cycle 数与"短 read 不拉平长 read"的口径；
    掺 N 能走到 k-mer 的"从头重算"那条分支。
    """
    rng = random.Random(seed)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            length = rng.choice([60, 80, 100, 120, 151])
            sequence = list(rng.choices("ACGT", k=length))
            if index % 13 == 0:
                sequence[rng.randrange(length)] = "N"
            quality = "".join(
                chr(33 + max(2, 40 - int(35 * (position / (length - 1)) ** 1.8)))
                for position in range(length)
            )
            handle.write(f"@read{index}\n{''.join(sequence)}\n+\n{quality}\n")


def _assert_same_stats(
    native: abi.NativeReadStats, python_summary
) -> None:
    """逐字段比较两侧统计，曲线要求**逐点完全相等**。"""
    assert native.total_reads == python_summary.total_reads
    assert native.total_bases == python_summary.total_bases
    assert native.q20_bases == python_summary.q20_bases
    assert native.q30_bases == python_summary.q30_bases
    assert native.q40_bases == python_summary.q40_bases
    assert native.gc_bases == python_summary.gc_bases
    assert native.mean_length == python_summary.mean_length
    assert native.cycles == python_summary.cycles

    assert set(native.quality_curves) == set(python_summary.quality_curves)
    for name, curve in native.quality_curves.items():
        reference = python_summary.quality_curves[name]
        assert len(curve) == len(reference), f"质量曲线 {name} 长度不一致"
        assert curve == reference, f"质量曲线 {name} 逐点不一致"

    assert set(native.content_curves) == set(python_summary.content_curves)
    for name, curve in native.content_curves.items():
        reference = python_summary.content_curves[name]
        assert len(curve) == len(reference), f"含量曲线 {name} 长度不一致"
        assert curve == reference, f"含量曲线 {name} 逐点不一致"

    assert native.quality_histogram == python_summary.quality_histogram
    assert native.kmer_counts == python_summary.kmer_counts
    assert native.length_counts == python_summary.length_counts


def test_native_matches_python_on_real_data() -> None:
    _assert_same_stats(abi.read_stats_fastq(_READS), stat_fastq(_READS))


def test_native_matches_python_on_synthetic_data(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.fq"
    _write_synthetic(source, 800, seed=20260922)

    native = abi.read_stats_fastq(source)
    python_summary = stat_fastq(source)

    _assert_same_stats(native, python_summary)
    # 数据确实走到了这几条路径上，否则对拍是空跑。
    assert native.cycles == 151
    assert len(native.length_counts) == 5
    assert sum(native.kmer_counts) > 0


def test_native_matches_python_when_reads_vary_in_length(tmp_path: Path) -> None:
    """读长参差时 cycle 的判定最容易差一位，单独测一条。"""
    source = tmp_path / "mixed.fq"
    _write_synthetic(source, 200, seed=7)

    native = abi.read_stats_fastq(source)
    python_summary = stat_fastq(source)

    _assert_same_stats(native, python_summary)
    assert min(native.length_counts) < native.cycles


def test_native_matches_python_with_heavy_n(tmp_path: Path) -> None:
    """整条都是 N 的数据：k-mer 全零，曲线也必须有定义（不能出 NaN）。"""
    source = tmp_path / "n_heavy.fq"
    with source.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(50):
            sequence = "N" * 40
            handle.write(f"@read{index}\n{sequence}\n+\n{_Q40 * 40}\n")

    native = abi.read_stats_fastq(source)
    python_summary = stat_fastq(source)

    _assert_same_stats(native, python_summary)
    assert sum(native.kmer_counts) == 0
    assert native.content_curves["N"] == (1.0,) * 40


def test_native_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")

    native = abi.read_stats_fastq(source)
    python_summary = stat_fastq(source)

    _assert_same_stats(native, python_summary)
    assert native.cycles == 0
    assert native.max_length == 0


def test_native_handles_gzip_input(tmp_path: Path) -> None:
    plain = tmp_path / "synthetic.fq"
    _write_synthetic(plain, 200, seed=11)
    source = tmp_path / "synthetic.fq.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())

    _assert_same_stats(abi.read_stats_fastq(source), stat_fastq(source))


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "统计"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    _write_synthetic(source, 100, seed=3)

    _assert_same_stats(abi.read_stats_fastq(source), stat_fastq(source))


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        abi.read_stats_fastq(tmp_path / "absent.fq")


def test_native_reports_broken_input(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        abi.read_stats_fastq(broken)
