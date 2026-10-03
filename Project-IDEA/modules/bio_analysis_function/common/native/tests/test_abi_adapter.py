"""原生接头检测的正确性测试：与 Python 实现逐位对拍。

这个算法的两段式决定了测试策略：

- **第一段（已知接头表）** 的结果是"表里哪一条 + 命中多少条 read"，
  两侧都必须给出同一答案（包括并列时的取舍）。它的 Python 版成本正比于
  "表长 × read 数"，因此这里的对拍用**缩表**（表长从 234 降到几条）；
  默认的全表另有一条只在原生侧跑的用例（Python 跑不动，见 5.4.4.5）。
- **第二段（k-mer + 前缀树）** 用空表跳过第一段，直接对拍。
  样本很小时会得到退化的 10 碱基种子，正好是这条路径最有代表性的产物。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.known_adapters import KNOWN_ADAPTERS
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.adapter_detection import (
    AdapterDetectionConfig,
    detect_adapter_from_file,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

BASES = "ACGT"
TRUSEQ = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
#: 缩表：只留几条以 TruSeq 开头的接头（对拍用，控制 Python 侧耗时）。
SMALL_TABLE = tuple(
    adapter
    for adapter in KNOWN_ADAPTERS
    if adapter.startswith("AGATCGGAAGAGCACACGTCTGAACTCCAGTC")
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"


def write_fastq(path: Path, sequences: list[bytes]) -> None:
    with path.open("wb") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(b"@read%d\n" % index)
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def make_sequences(
    count: int,
    adapter: str | None,
    adapter_reads: int,
    *,
    seed: int = 20260917,
) -> list[bytes]:
    """造一批 read：前 ``adapter_reads`` 条带接头（插入片段长度随机），其余是纯基因组序列。"""
    rng = random.Random(seed)
    sequences: list[bytes] = []
    for index in range(count):
        if adapter is not None and index < adapter_reads:
            insert = "".join(rng.choices(BASES, k=rng.randint(50, 130)))
            sequences.append((insert + adapter)[:151].encode("ascii"))
        else:
            sequences.append("".join(rng.choices(BASES, k=151)).encode("ascii"))
    return sequences


# --------------------------------------------------------------------------
# 第一段：已知接头表
# --------------------------------------------------------------------------


def test_native_matches_python_with_reduced_table(tmp_path: Path) -> None:
    """缩表下的完整对拍：接头、来源、命中条数、采样规模都必须一致。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(500, TRUSEQ, 25))

    native = abi.adapter_detect_fastq(source, adapters=SMALL_TABLE, min_reads=1)
    python_result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)
    )

    assert native.adapter == python_result.adapter == TRUSEQ
    assert native.source == python_result.source == "known"
    assert native.sampled_reads == python_result.sampled_reads == 500
    assert native.sampled_bases == python_result.sampled_bases
    assert native.seed_count == python_result.seed_count  # 命中条数
    assert native.seed_fold == pytest.approx(python_result.seed_fold, abs=1e-3)
    assert native.detected


def test_native_full_table_hits_known_adapter(tmp_path: Path) -> None:
    """默认的全表（234 条）也要能命中——这是实际使用的那条路径。

    只断言原生侧的结果：同样的输入交给 Python 版要跑好几秒（表长 × read 数），
    对拍用上面的缩表用例覆盖。
    """
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(500, TRUSEQ, 25))

    native = abi.adapter_detect_fastq(source, min_reads=1)

    assert native.adapter == TRUSEQ
    assert native.source == "known"


def test_native_matches_python_when_table_is_empty(tmp_path: Path) -> None:
    """空表 = 跳过第一段：走第二段的退化路径，两侧结果一致。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(300, TRUSEQ, 40))

    native = abi.adapter_detect_fastq(source, adapters=(), min_reads=1)
    python_result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=())
    )

    assert native.adapter == python_result.adapter
    assert native.source == python_result.source == "kmer"
    assert native.seed_sequence == python_result.seed_sequence
    assert native.seed_count == python_result.seed_count


def test_native_matches_python_when_nothing_is_found(tmp_path: Path) -> None:
    """干净数据（无接头）两侧都报"没检测到"，且原因一致。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(2000, None, 0))

    native = abi.adapter_detect_fastq(source, adapters=(), min_reads=1)
    python_result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=())
    )

    assert native.adapter is None
    assert not native.detected
    assert native.reason == python_result.reason


def test_native_reports_sample_too_small(tmp_path: Path) -> None:
    """真实数据只有 9 条，两侧都要如实报"样本不足"，措辞一致。"""
    native = abi.adapter_detect_fastq(_READS)
    python_result = detect_adapter_from_file(_READS)

    assert native.adapter is None
    assert native.sampled_reads == 9
    assert native.reason == python_result.reason
    assert "样本不足" in native.reason


# --------------------------------------------------------------------------
# 采样、压缩与路径
# --------------------------------------------------------------------------


def test_native_respects_sampling_limits(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(300, TRUSEQ, 30))

    native = abi.adapter_detect_fastq(source, adapters=(), min_reads=1, max_reads=100)

    assert native.sampled_reads == 100


def test_native_handles_gzip_input(tmp_path: Path) -> None:
    """gzip 输入按魔数识别（扩展名故意不写 .gz）。"""
    plain = tmp_path / "reads.fq"
    write_fastq(plain, make_sequences(500, TRUSEQ, 25))
    packed = tmp_path / "reads.dat"
    with gzip.open(packed, "wb") as handle:
        handle.write(plain.read_bytes())

    native = abi.adapter_detect_fastq(packed, adapters=SMALL_TABLE, min_reads=1)

    assert native.adapter == TRUSEQ


def test_native_handles_non_ascii_path(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据"
    folder.mkdir()
    source = folder / "样本1.fq"
    write_fastq(source, make_sequences(500, TRUSEQ, 25))

    native = abi.adapter_detect_fastq(source, adapters=SMALL_TABLE, min_reads=1)

    assert native.adapter == TRUSEQ


# --------------------------------------------------------------------------
# 错误处理
# --------------------------------------------------------------------------


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        abi.adapter_detect_fastq(tmp_path / "absent.fq")


def test_native_reports_format_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        abi.adapter_detect_fastq(broken)


def test_native_rejects_invalid_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="key_length"):
        abi.adapter_detect_fastq(_READS, key_length=13)
    with pytest.raises(ValueError, match="min_reads"):
        abi.adapter_detect_fastq(_READS, min_reads=0)
    with pytest.raises(ValueError, match="max_adapter_length"):
        abi.adapter_detect_fastq(_READS, max_adapter_length=200)
    with pytest.raises(ValueError, match="shift_tail"):
        abi.adapter_detect_fastq(_READS, shift_tail=-1)
