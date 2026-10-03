"""文件级接头裁剪的测试：三种"接头来源"、压缩、真实数据上的不误伤。"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.adapter_detection import AdapterDetectionConfig
from modules.bio_analysis_function.common.known_adapters import KNOWN_ADAPTERS
from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.adapter_trimming import (
    trim_adapter_fastq,
)

TRUSEQ = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
BASES = "ACGT"
DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"
#: 缩表：自动检测模式下用它，避免纯 Python 检测扫 234 条表（见 5.4.4.5）。
SMALL_TABLE = tuple(
    adapter
    for adapter in KNOWN_ADAPTERS
    if adapter.startswith("AGATCGGAAGAGCACACGTCTGAACTCCAGTC")
)


def write_fastq(path: Path, sequences: list[bytes], quality_char: bytes = b"I") -> None:
    with path.open("wb") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(b"@read%d\n" % index)
            handle.write(sequence + b"\n+\n")
            handle.write(quality_char * len(sequence) + b"\n")


def make_sequences(count: int, adapter_reads: int, *, seed: int = 20260917) -> list[bytes]:
    """前 ``adapter_reads`` 条带 TruSeq 接头（插入片段长度随机），其余是纯基因组序列。"""
    rng = random.Random(seed)
    sequences: list[bytes] = []
    for index in range(count):
        if index < adapter_reads:
            insert = "".join(rng.choices(BASES, k=rng.randint(50, 130)))
            sequences.append((insert + TRUSEQ)[:151].encode("ascii"))
        else:
            sequences.append("".join(rng.choices(BASES, k=151)).encode("ascii"))
    return sequences


# --------------------------------------------------------------------------
# 手动指定
# --------------------------------------------------------------------------


def test_manual_adapter_trims_only_reads_with_adapter(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(200, 40))
    output = tmp_path / "trimmed.fq"

    summary = trim_adapter_fastq(source, output, adapter=TRUSEQ)

    assert summary.adapter_source == "given"
    assert summary.adapter == TRUSEQ
    assert summary.total_reads == 200
    assert summary.trimmed_reads == 40
    assert summary.dropped_reads == 0
    assert summary.trimmed_bases > 0
    assert summary.bases_after == summary.bases_before - summary.trimmed_bases
    # 报告里能看到切下来的接头序列
    assert summary.top_adapters[0][0].startswith("AGATCGGAAGAGC")

    # 写出的 read 都不含接头了
    for record in read_fastq(output):
        assert TRUSEQ.encode("ascii") not in record.sequence


def test_untouched_reads_are_written_verbatim(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, [b"ACGT" * 40])
    output = tmp_path / "out.fq"

    summary = trim_adapter_fastq(source, output, adapter=TRUSEQ)

    assert summary.trimmed_reads == 0
    assert output.read_bytes() == b"@read0\n" + b"ACGT" * 40 + b"\n+\n" + b"I" * 160 + b"\n"


# --------------------------------------------------------------------------
# 候选表与自动检测
# --------------------------------------------------------------------------


def test_candidate_list_mode(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(100, 20))
    output = tmp_path / "out.fq"

    summary = trim_adapter_fastq(
        source, output, adapters=["TTTTTTTTTTTT", TRUSEQ]
    )

    assert summary.adapter_source == "list"
    # 20 条带接头的 read 全部被裁；另外有 1 条随机序列被"头部巧合"命中——
    # 上游的插入/缺失分支锚定在 read 开头（见 README 的已知限制），
    # 随机序列偶尔会撞上，因此这里把实测值钉住（数据是定种子的，结果可复现）。
    assert summary.trimmed_reads == 21


def test_auto_detect_mode_uses_detected_adapter(tmp_path: Path) -> None:
    """自动检测：先用公共层的接头检测找出接头，再按它裁剪。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_sequences(200, 60))
    output = tmp_path / "out.fq"

    summary = trim_adapter_fastq(
        source,
        output,
        auto_detect=True,
        detect_config=AdapterDetectionConfig(min_reads=100, adapters=SMALL_TABLE),
    )

    assert summary.adapter_source == "detected"
    assert summary.adapter in SMALL_TABLE
    assert summary.detection is not None
    assert summary.detection.detected
    assert summary.trimmed_reads > 0


def test_auto_detect_without_result_writes_input_untouched(tmp_path: Path) -> None:
    """检测不出接头时不裁，但输出文件照常写出（内容与输入一致）。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, [b"ACGT" * 40, b"TTTT" * 20])
    output = tmp_path / "out.fq"

    summary = trim_adapter_fastq(
        source,
        output,
        auto_detect=True,
        detect_config=AdapterDetectionConfig(min_reads=1000),  # 样本不足 → 不会检出接头
    )

    assert summary.adapter_source is None
    assert summary.trimmed_reads == 0
    assert output.read_bytes() == bytearray(source.read_bytes())
    assert "未执行裁剪" in summary.render()


# --------------------------------------------------------------------------
# 参数、压缩与错误
# --------------------------------------------------------------------------


def test_requires_an_adapter_source(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, [b"ACGT" * 40])

    with pytest.raises(ValueError, match="必须指定接头来源"):
        trim_adapter_fastq(source, tmp_path / "out.fq")


def test_adapter_and_adapters_are_mutually_exclusive(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, [b"ACGT" * 40])

    with pytest.raises(ValueError, match="不能同时给"):
        trim_adapter_fastq(
            source, tmp_path / "out.fq", adapter=TRUSEQ, adapters=["ACGTACGT"]
        )


def test_gzip_input_follows_input_compression(tmp_path: Path) -> None:
    plain = tmp_path / "reads.fq"
    write_fastq(plain, make_sequences(100, 20))
    packed = tmp_path / "reads.fq.gz"
    with gzip.open(packed, "wb") as handle:
        handle.write(plain.read_bytes())

    output = tmp_path / "out.fq"  # 扩展名故意不写 .gz
    summary = trim_adapter_fastq(packed, output, adapter=TRUSEQ)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.trimmed_reads == 21  # 同上：20 条真接头 + 1 条头部巧合命中


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        trim_adapter_fastq(tmp_path / "absent.fq", tmp_path / "out.fq", adapter=TRUSEQ)


def test_real_data_is_not_touched_by_a_plausible_adapter(tmp_path: Path) -> None:
    """真实 DNA 数据上不该误伤：这批数据没有 TruSeq 接头残留。"""
    output = tmp_path / "out.fq"

    summary = trim_adapter_fastq(_READS, output, adapter=TRUSEQ)

    assert summary.total_reads == 9
    assert summary.trimmed_reads == 0
    # 逐条比对：输出与输入完全一致
    assert [r.sequence for r in read_fastq(output)] == [
        r.sequence for r in read_fastq(_READS)
    ]
