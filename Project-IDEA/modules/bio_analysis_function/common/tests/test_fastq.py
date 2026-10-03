"""公共层 FASTQ 读写与流式管道的测试。"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import (
    FastqRecord,
    FastqStreamSummary,
    is_gzip,
    process_fastq,
    read_fastq,
    write_fastq,
)

_SEQUENCE = b"ACGTACGTAC"
_QUALITY = b"IIIIIIIIII"


def _record(name: str) -> FastqRecord:
    return FastqRecord(name=name, sequence=_SEQUENCE, quality=_QUALITY)


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------


def test_reads_plain_fastq(tmp_path: Path) -> None:
    """基础读取：名字行去掉 '@'，序列与质量保持原始字节。"""
    path = tmp_path / "reads.fastq"
    path.write_bytes(
        b"@read1\n" + _SEQUENCE + b"\n+\n" + _QUALITY + b"\n"
        b"@read2 with spaces\n" + _SEQUENCE + b"\n+read2\n" + _QUALITY + b"\n"
    )

    records = list(read_fastq(path))

    assert [record.name for record in records] == ["read1", "read2 with spaces"]
    assert records[0].sequence == _SEQUENCE
    assert records[0].quality == _QUALITY
    assert records[0].length == 10


def test_reads_gzip_detected_by_content_not_extension(tmp_path: Path) -> None:
    """gzip 按魔数识别：文件名不带 .gz 也必须能解压。"""
    payload = b"@read1\n" + _SEQUENCE + b"\n+\n" + _QUALITY + b"\n"
    path = tmp_path / "reads.fastq"
    with gzip.open(path, "wb") as handle:
        handle.write(payload)

    records = list(read_fastq(path))

    assert len(records) == 1
    assert records[0].sequence == _SEQUENCE


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    """写出后再读回，内容应当完全一致。"""
    path = tmp_path / "out.fastq"
    written = write_fastq([_record("read1"), _record("read2")], path)

    assert written == 2
    records = list(read_fastq(path))
    assert [record.name for record in records] == ["read1", "read2"]
    assert all(record.sequence == _SEQUENCE for record in records)


def test_write_compress_then_read_round_trip(tmp_path: Path) -> None:
    """压缩写出后仍能被自动识别并读回。"""
    path = tmp_path / "out.fastq.gz"
    write_fastq([_record("read1")], path, compress=True)

    records = list(read_fastq(path))

    assert len(records) == 1
    assert records[0].quality == _QUALITY


def test_missing_file_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        list(read_fastq(tmp_path / "absent.fastq"))


def test_empty_file_yields_no_records(tmp_path: Path) -> None:
    path = tmp_path / "empty.fastq"
    path.write_bytes(b"")

    assert list(read_fastq(path)) == []


def test_incomplete_record_raises_with_line_number(tmp_path: Path) -> None:
    """记录只有 3 行时必须报错，并指出起始行号。"""
    path = tmp_path / "broken.fastq"
    path.write_bytes(b"@read1\n" + _SEQUENCE + b"\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        list(read_fastq(path))


def test_length_mismatch_raises(tmp_path: Path) -> None:
    """质量行短于序列行是 FASTQ 的硬性错误，不能静默截断。"""
    path = tmp_path / "mismatch.fastq"
    path.write_bytes(b"@read1\n" + _SEQUENCE + b"\n+\n" + b"III\n")

    with pytest.raises(ValueError, match="长度"):
        list(read_fastq(path))


def test_name_line_must_start_with_at(tmp_path: Path) -> None:
    path = tmp_path / "badname.fastq"
    path.write_bytes(b"read1\n" + _SEQUENCE + b"\n+\n" + _QUALITY + b"\n")

    with pytest.raises(ValueError, match="不以 '@' 开头"):
        list(read_fastq(path))


def test_is_gzip_uses_magic_not_extension(tmp_path: Path) -> None:
    """扩展名与内容不符时以内容为准。"""
    plain = tmp_path / "plain.fastq.gz"
    plain.write_bytes(b"@read1\n" + _SEQUENCE + b"\n+\n" + _QUALITY + b"\n")
    assert not is_gzip(plain)

    compressed = tmp_path / "compressed.fastq"
    with gzip.open(compressed, "wb") as handle:
        handle.write(b"@read1\n" + _SEQUENCE + b"\n+\n" + _QUALITY + b"\n")
    assert is_gzip(compressed)


# --------------------------------------------------------------------------
# 流式管道
# --------------------------------------------------------------------------


def _write(path: Path, records: list[tuple[str, bytes, bytes]]) -> None:
    payload = b"".join(
        b"@" + name.encode() + b"\n" + seq + b"\n+\n" + qual + b"\n"
        for name, seq, qual in records
    )
    path.write_bytes(payload)


def test_process_fastq_reports_statistics(tmp_path: Path) -> None:
    """管道统计：总条数、保留数、改动数、丢弃数与碱基数。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"ACGTACGTAC", _QUALITY), ("r2", b"ACGTACGTAC", _QUALITY)])
    output = tmp_path / "out.fq"

    def shorten_to_four(record: FastqRecord) -> FastqRecord | None:
        return FastqRecord(record.name, record.sequence[:4], record.quality[:4])

    summary = process_fastq(source, output, shorten_to_four)

    assert isinstance(summary, FastqStreamSummary)
    assert summary.total_reads == 2
    assert summary.kept_reads == 2
    assert summary.changed_reads == 2
    assert summary.dropped_reads == 0
    assert summary.bases_before == 20
    assert summary.bases_after == 8
    assert summary.bases_removed == 12
    assert summary.removal_rate == pytest.approx(0.6)
    assert output.read_bytes().count(b"\n") == 8


def test_process_fastq_drops_records_returning_none(tmp_path: Path) -> None:
    """transform 返回 None 的记录不写出，但计入 dropped。"""
    source = tmp_path / "in.fq"
    _write(source, [("drop_me", b"ACGT", b"IIII"), ("keep_me", b"ACGT", b"IIII")])
    output = tmp_path / "out.fq"

    def drop_first(record: FastqRecord) -> FastqRecord | None:
        return None if record.name == "drop_me" else record

    summary = process_fastq(source, output, drop_first)

    assert summary.total_reads == 2
    assert summary.dropped_reads == 1
    assert summary.kept_reads == 1
    assert summary.bases_after == 4
    assert [record.name for record in read_fastq(output)] == ["keep_me"]


def test_process_fastq_unchanged_records_are_not_counted_as_changed(tmp_path: Path) -> None:
    """序列未改动时不计入 changed_reads。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"ACGT", b"IIII")])

    summary = process_fastq(source, tmp_path / "out.fq", lambda record: record)

    assert summary.changed_reads == 0
    assert summary.bases_removed == 0


def test_process_fastq_compression_follows_input_not_extension(tmp_path: Path) -> None:
    """默认压缩方式跟随输入，与输出文件名的扩展名无关。"""
    gz_source = tmp_path / "gz_in.fq.gz"
    write_fastq([_record("r1")], gz_source, compress=True)
    misleading_plain = tmp_path / "out.fq"
    process_fastq(gz_source, misleading_plain, lambda record: record)
    assert is_gzip(misleading_plain)

    plain_source = tmp_path / "plain_in.fq"
    write_fastq([_record("r1")], plain_source, compress=False)
    misleading_gz = tmp_path / "out.fq.gz"
    process_fastq(plain_source, misleading_gz, lambda record: record)
    assert not is_gzip(misleading_gz)


def test_process_fastq_removes_partial_output_on_failure(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下看似完整实则残缺的输出文件。"""
    source = tmp_path / "broken.fq"
    source.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        process_fastq(source, output, lambda record: record)

    assert not output.exists()


def test_process_fastq_rejects_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        process_fastq(tmp_path / "absent.fq", tmp_path / "out.fq", lambda record: record)


def test_process_fastq_handles_empty_input(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")
    output = tmp_path / "out.fq"

    summary = process_fastq(source, output, lambda record: record)

    assert summary.total_reads == 0
    assert summary.removal_rate == 0.0
    assert output.exists()


def test_summary_render_contains_key_numbers() -> None:
    summary = FastqStreamSummary(
        total_reads=10,
        kept_reads=8,
        changed_reads=3,
        dropped_reads=2,
        bases_before=200,
        bases_after=150,
    )

    text = summary.render()

    assert "输入 read 数：10" in text
    assert "丢弃 read 数：2" in text
    assert "25.00%" in text
