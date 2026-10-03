"""quality_trimming 文件级入口的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import (
    FastqRecord,
    is_gzip,
    read_fastq,
    write_fastq,
)
from modules.bio_analysis_function.submodules.quality_trimming import (
    QualityCutConfig,
    trim_fastq,
)

_HIGH = b"I"  # Q40
_LOW = b"#"   # Q2
_TAIL_CUT = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20)


def _write(path: Path, records: list[tuple[str, bytes, bytes]]) -> None:
    """把一个 (名字, 序列, 质量) 列表写成 FASTQ。"""
    payload = b"".join(
        b"@" + name.encode() + b"\n" + seq + b"\n+\n" + qual + b"\n"
        for name, seq, qual in records
    )
    path.write_bytes(payload)


def test_trims_whole_file_and_reports_summary(tmp_path: Path) -> None:
    """文件级剪切：20bp 中后 8 个是 Q2，cut_tail 后应保留 11bp。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 20, _HIGH * 12 + _LOW * 8)])
    output = tmp_path / "out.fq"

    summary = trim_fastq(source, output, config=_TAIL_CUT)

    assert summary.total_reads == 1
    assert summary.kept_reads == 1
    assert summary.changed_reads == 1
    assert summary.dropped_reads == 0
    assert summary.bases_before == 20
    assert summary.bases_after == 11
    assert summary.bases_removed == 9
    assert summary.removal_rate == pytest.approx(0.45)

    written = list(read_fastq(output))
    assert len(written) == 1
    assert written[0].sequence == b"C" * 11
    assert written[0].quality == _HIGH * 11


def test_reads_that_need_no_change_are_written_verbatim(tmp_path: Path) -> None:
    """质量全部达标时不应改动序列，也不该计为 changed。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 20, _HIGH * 20)])
    output = tmp_path / "out.fq"

    summary = trim_fastq(source, output, config=_TAIL_CUT)

    assert summary.changed_reads == 0
    assert summary.bases_removed == 0
    assert list(read_fastq(output))[0].sequence == b"C" * 20


def test_dropped_reads_are_not_written(tmp_path: Path) -> None:
    """剪切后长度不合法的 read 被丢弃，不进入输出文件。"""
    source = tmp_path / "in.fq"
    # 第一条长度为 0，放不下任何窗口，必然被丢弃
    _write(source, [("empty", b"", b""), ("r2", b"C" * 20, _HIGH * 20)])
    output = tmp_path / "out.fq"

    summary = trim_fastq(source, output, config=_TAIL_CUT)

    assert summary.total_reads == 2
    assert summary.dropped_reads == 1
    assert summary.kept_reads == 1
    assert [record.name for record in read_fastq(output)] == ["r2"]


def test_output_compression_follows_input_not_extension(tmp_path: Path) -> None:
    """默认压缩方式跟随**输入**，与输出文件名的扩展名无关。

    这是个刻意的设计：上游 fastp 也按内容而非扩展名判断压缩，
    因此文件名可能骗人。下面两个用例故意使用与内容不符的扩展名，
    确保实现没有被文件名带偏。
    """
    gz_source = tmp_path / "gz_in.fq.gz"
    write_fastq([FastqRecord("r1", b"C" * 20, _HIGH * 20)], gz_source, compress=True)
    # 输入是 gzip，输出却写成纯文本扩展名：仍应压缩
    misleading_plain = tmp_path / "out.fq"
    trim_fastq(gz_source, misleading_plain, config=_TAIL_CUT)
    assert is_gzip(misleading_plain)

    plain_source = tmp_path / "plain_in.fq"
    write_fastq([FastqRecord("r1", b"C" * 20, _HIGH * 20)], plain_source, compress=False)
    # 输入是纯文本，输出却写成 gzip 扩展名：仍应是纯文本
    misleading_gz = tmp_path / "out.fq.gz"
    trim_fastq(plain_source, misleading_gz, config=_TAIL_CUT)
    assert not is_gzip(misleading_gz)

    assert list(read_fastq(misleading_gz))[0].sequence == b"C" * 20


def test_explicit_compression_overrides_input(tmp_path: Path) -> None:
    """显式指定 compress 时以它为准。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 20, _HIGH * 20)])

    output = tmp_path / "out.fq"
    trim_fastq(source, output, config=_TAIL_CUT, compress=True)

    assert is_gzip(output)
    assert list(read_fastq(output))[0].sequence == b"C" * 20


def test_creates_output_parent_directories(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 20, _HIGH * 20)])
    output = tmp_path / "nested" / "deep" / "out.fq"

    trim_fastq(source, output, config=_TAIL_CUT)

    assert output.exists()


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        trim_fastq(tmp_path / "absent.fq", tmp_path / "out.fq", config=_TAIL_CUT)


def test_partial_output_is_removed_on_failure(tmp_path: Path) -> None:
    """输入文件在记录中途截断时，不能留下看似完整实则残缺的输出文件。"""
    source = tmp_path / "broken.fq"
    source.write_bytes(b"@r1\nCCCC\n+\nIIII\n@r2\nCCCC\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        trim_fastq(source, output, config=_TAIL_CUT)

    assert not output.exists()


def test_empty_input_produces_empty_summary(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")
    output = tmp_path / "out.fq"

    summary = trim_fastq(source, output, config=_TAIL_CUT)

    assert summary.total_reads == 0
    assert summary.removal_rate == 0.0
    assert output.exists()


def test_fixed_trimming_applies_without_quality_cut(tmp_path: Path) -> None:
    """config 为 None 时只做固定位置修剪。"""
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"ACGTACGTAC", b"IIIIIIIIII")])
    output = tmp_path / "out.fq"

    summary = trim_fastq(source, output, front=2, tail=3)

    assert summary.bases_after == 5
    assert list(read_fastq(output))[0].sequence == b"GTACG"


def test_summary_render_contains_key_numbers(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    _write(source, [("r1", b"C" * 20, _HIGH * 12 + _LOW * 8)])

    summary = trim_fastq(source, tmp_path / "out.fq", config=_TAIL_CUT)
    text = summary.render()

    assert "输入 read 数：1" in text
    assert "丢弃 read 数：0" in text
    assert "45.00%" in text
