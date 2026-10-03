"""原生接头裁剪的正确性测试：与 Python 实现逐字节对拍。

对拍口径与其它算法一致：**同一输入、同一组参数，两侧分别处理，输出文件必须
逐字节相同**，统计也必须相同。裁剪还多一层含义——输出一致同时说明"切点也算对了"。

上游自带的两组测试向量在这里以文件形式各走一遍，是最硬的证据。
原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.adapter_trimming import (
    AdapterTrimConfig,
)
from modules.bio_analysis_function.submodules.adapter_trimming import (
    trim_adapter_fastq as python_trim,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

BASES = "ACGT"
TRUSEQ = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

# 上游 AdapterTrimmer::test() 的两组向量
VECTOR_READ = b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG"
VECTOR_ADAPTER = "TTTTCCACGGGGATACTACTG"
VECTOR_MULTI_READ = (
    b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG"
    b"AAATTTCCCGGGAAATTTCCCGGGATCGATCGATCGATCGAATTCC"
)
VECTOR_MULTI_ADAPTERS = (
    "GCTAGCTAGCTAGCTA",
    "AAATTTCCCGGGAAATTTCCCGGG",
    "ATCGATCGATCGATCG",
    "AATTCCGGAATTCCGG",
)


def write_fastq(path: Path, sequences: list[bytes]) -> None:
    with path.open("wb") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(b"@read%d\n" % index)
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def make_sequences(
    count: int, adapter_reads: int, *, seed: int = 20260917
) -> list[bytes]:
    rng = random.Random(seed)
    sequences: list[bytes] = []
    for index in range(count):
        if index < adapter_reads:
            insert = "".join(rng.choices(BASES, k=rng.randint(50, 130)))
            sequences.append((insert + TRUSEQ)[:151].encode("ascii"))
        else:
            sequences.append("".join(rng.choices(BASES, k=151)).encode("ascii"))
    return sequences


def native_trim(source: Path, output: Path, adapters, **kwargs):
    return abi.trim_adapter_fastq(source, output, adapters=adapters, **kwargs)


# --------------------------------------------------------------------------
# 与 Python 逐字节对拍
# --------------------------------------------------------------------------


def test_matches_python_with_single_adapter(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(200, 40))
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = native_trim(source, native_out, [TRUSEQ])
    python_summary = python_trim(source, python_out, adapter=TRUSEQ)

    assert native_out.read_bytes() == python_out.read_bytes()
    assert native.changed_reads == python_summary.trimmed_reads
    assert native.total_reads == python_summary.total_reads
    assert native.kept_reads == native.total_reads  # 不丢 read
    assert native.dropped_reads == 0
    assert native.bases_before - native.bases_after == python_summary.trimmed_bases


def test_matches_python_with_upstream_vectors(tmp_path: Path) -> None:
    """上游两组自带向量各走一遍文件流程，两侧输出必须一致。"""
    for name, sequence, adapters in (
        ("single", VECTOR_READ, (VECTOR_ADAPTER,)),
        ("multi", VECTOR_MULTI_READ, VECTOR_MULTI_ADAPTERS),
    ):
        source = tmp_path / f"{name}.fq"
        write_fastq(source, [sequence])
        native_out = tmp_path / f"{name}_native.fq"
        python_out = tmp_path / f"{name}_python.fq"

        native = native_trim(source, native_out, list(adapters))
        python_summary = python_trim(source, python_out, adapters=list(adapters))

        assert native_out.read_bytes() == python_out.read_bytes(), name
        assert native.changed_reads == python_summary.trimmed_reads == 1, name


def test_matches_python_without_trimming(tmp_path: Path) -> None:
    """空表 = 不裁：输出与输入逐字节相同。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(100, 0))
    output = tmp_path / "out.fq"

    summary = native_trim(source, output, [])

    assert output.read_bytes() == source.read_bytes()
    assert summary.changed_reads == 0
    assert summary.total_reads == 100


def test_matches_python_on_real_data(tmp_path: Path) -> None:
    """真实数据上不该误伤：两侧都裁 0 条，输出与输入一致。"""
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = native_trim(_READS, native_out, [TRUSEQ])
    python_summary = python_trim(_READS, python_out, adapter=TRUSEQ)

    assert native_out.read_bytes() == python_out.read_bytes()
    assert native.changed_reads == python_summary.trimmed_reads
    assert [r.sequence for r in read_fastq(native_out)] == [
        r.sequence for r in read_fastq(_READS)
    ]


def test_gap_tolerance_can_be_switched_off(tmp_path: Path) -> None:
    """关掉插入/缺失容错后，两侧仍然一致（都只能靠等长比对命中）。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(100, 20))
    native_out = tmp_path / "native.fq"
    python_out = tmp_path / "python.fq"

    native = native_trim(source, native_out, [TRUSEQ], allow_one_gap=False)
    python_summary = python_trim(
        source,
        python_out,
        adapter=TRUSEQ,
        config=AdapterTrimConfig(allow_one_gap=False),
    )

    assert native_out.read_bytes() == python_out.read_bytes()
    assert native.changed_reads == python_summary.trimmed_reads


def test_gzip_input_follows_compression(tmp_path: Path) -> None:
    plain = tmp_path / "reads.fq"
    write_fastq(plain, make_sequences(100, 20))
    packed = tmp_path / "reads.fq.gz"
    with gzip.open(packed, "wb") as handle:
        handle.write(plain.read_bytes())

    output = tmp_path / "out.fq"  # 扩展名故意不写 .gz
    summary = native_trim(packed, output, [TRUSEQ])

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.changed_reads > 0


# --------------------------------------------------------------------------
# 错误处理
# --------------------------------------------------------------------------


def test_rejects_invalid_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="match_required"):
        native_trim(_READS, tmp_path / "out.fq", [TRUSEQ], match_required=0)


def test_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        native_trim(tmp_path / "absent.fq", tmp_path / "out.fq", [TRUSEQ])


def test_reports_format_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        native_trim(broken, tmp_path / "out.fq", [TRUSEQ])
