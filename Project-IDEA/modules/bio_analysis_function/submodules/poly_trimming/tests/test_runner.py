"""poly_trimming 文件级接口的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import (
    FastqRecord,
    FastqStreamSummary,
    is_gzip,
    read_fastq,
    write_fastq,
)
from modules.bio_analysis_function.submodules.poly_trimming import (
    PolyTrimConfig,
    trim_poly_fastq,
)

_POLY_X = PolyTrimConfig(enabled_poly_x=True)


def _write(path: Path, records: list[tuple[str, bytes, bytes]]) -> None:
    payload = b"".join(
        b"@" + name.encode() + b"\n" + seq + b"\n+\n" + qual + b"\n"
        for name, seq, qual in records
    )
    path.write_bytes(payload)


def test_trims_poly_tail_and_reports_summary(tmp_path: Path) -> None:
    """10 个 C 之后接 20 个 A：应当只剩 10 个 C。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 10 + b"A" * 20, b"I" * 30)])
    output = tmp_path / "out.fq"

    summary = trim_poly_fastq(source, output, config=_POLY_X)

    assert isinstance(summary, FastqStreamSummary)
    assert summary.total_reads == 1
    assert summary.kept_reads == 1
    assert summary.changed_reads == 1
    assert summary.bases_before == 30
    assert summary.bases_after == 10
    assert summary.bases_removed == 20

    written = list(read_fastq(output))[0]
    assert written.sequence == b"C" * 10


def test_quality_is_truncated_with_sequence(tmp_path: Path) -> None:
    """poly 修剪只改序列，但质量串必须同步截短，否则 FASTQ 记录会不自洽。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 10 + b"A" * 20, b"I" * 10 + b"#" * 20)])
    output = tmp_path / "out.fq"

    trim_poly_fastq(source, output, config=_POLY_X)

    written = list(read_fastq(output))[0]
    assert written.sequence == b"C" * 10
    assert written.quality == b"I" * 10
    assert written.length == len(written.quality)


def test_reads_without_poly_tail_are_written_verbatim(tmp_path: Path) -> None:
    """没有 poly 尾巴的 read 原样写出，且不计入 changed_reads。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"ACGTTGCA" * 3, b"I" * 24)])
    output = tmp_path / "out.fq"

    summary = trim_poly_fastq(source, output, config=_POLY_X)

    assert summary.changed_reads == 0
    assert summary.bases_removed == 0
    assert list(read_fastq(output))[0].sequence == b"ACGTTGCA" * 3


def test_no_read_is_dropped(tmp_path: Path) -> None:
    """本算法只裁剪不丢弃，dropped_reads 恒为 0。"""
    source = tmp_path / "in.fq"
    _write(
        source,
        [
            ("empty", b"", b""),
            ("poly", b"A" * 30, b"I" * 30),
        ],
    )
    output = tmp_path / "out.fq"

    summary = trim_poly_fastq(source, output, config=_POLY_X)

    assert summary.total_reads == 2
    assert summary.dropped_reads == 0
    assert summary.kept_reads == 2
    # 整条都是 polyA 的记录会被清空序列，但仍写出
    sequences = [record.sequence for record in read_fastq(output)]
    assert sequences == [b"", b""]


def test_nothing_enabled_keeps_content_identical(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 10 + b"A" * 20, b"I" * 30)])
    output = tmp_path / "out.fq"

    summary = trim_poly_fastq(source, output)

    assert summary.changed_reads == 0
    assert list(read_fastq(output))[0].sequence == b"C" * 10 + b"A" * 20


def test_compression_follows_input(tmp_path: Path) -> None:
    gz_source = tmp_path / "in.fq.gz"
    write_fastq([FastqRecord("r1", b"C" * 10 + b"A" * 20, b"I" * 30)], gz_source, compress=True)
    output = tmp_path / "out.fq"

    trim_poly_fastq(gz_source, output, config=_POLY_X)

    assert is_gzip(output)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        trim_poly_fastq(tmp_path / "absent.fq", tmp_path / "out.fq", config=_POLY_X)
