"""文件级过滤接口的测试。

除了统计口径，重点守两件容易出错的事：

1. **只写通过的 read**，且写出的内容与输入逐字节一致（过滤不改序列）；
2. **失败时删掉半成品**，不留一个看似完整、实则是残片的输出文件。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.read_filtering.algorithm import (
    FAIL_LENGTH,
    FAIL_N_BASE,
    FAIL_QUALITY,
    FAIL_TOO_LONG,
    ReadFilterConfig,
)
from modules.bio_analysis_function.submodules.read_filtering.runner import filter_fastq

_Q40 = chr(40 + 33)  # 'I'，默认阈值之上的高质量
_Q10 = chr(10 + 33)  # '+'，明显低质量


def write_fastq_file(path: Path, records: list[tuple[str, str, str]]) -> None:
    """按 (名字, 序列, 质量符) 的三元组写一份 FASTQ。"""
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality_char in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality_char * len(sequence)}\n")


def sample_records() -> list[tuple[str, str, str]]:
    """四条记录，分别命中：通过、低质量、N 过多、过短。"""
    return [
        ("good", "A" * 151, _Q40),
        ("low_quality", "A" * 151, _Q10),
        ("many_n", "N" * 7 + "A" * 144, _Q40),
        ("too_short", "A" * 10, _Q40),
    ]


def test_summary_counts_each_reason(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "passed.fq"

    summary = filter_fastq(source, output)

    assert summary.total_reads == 4
    assert summary.kept_reads == 1
    assert summary.dropped_reads == 3
    assert summary.failures == {FAIL_QUALITY: 1, FAIL_N_BASE: 1, FAIL_LENGTH: 1}
    # 失败明细之和必须等于丢弃数，否则统计口径就散了。
    assert sum(summary.failures.values()) == summary.dropped_reads


def test_failed_output_collects_dropped_reads_with_reason_tag(tmp_path: Path) -> None:
    """给了 failed_output_path 时，被丢弃的 read 原样写进那个文件，名字后追加原因标签。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    passed = tmp_path / "passed.fq"
    failed = tmp_path / "failed.fq"

    summary = filter_fastq(source, passed, failed_output_path=failed)

    assert summary.kept_reads == 1
    assert summary.dropped_reads == 3
    assert [item.name for item in read_fastq(passed)] == ["good"]

    dropped = list(read_fastq(failed))
    assert [item.name for item in dropped] == [
        "low_quality failed_quality_filter",
        "many_n failed_too_many_n_bases",
        "too_short failed_too_short",
    ]
    # 序列与质量必须与输入逐字节一致——过滤只判定、不改写。
    assert [item.sequence for item in dropped] == [
        b"A" * 151,
        b"N" * 7 + b"A" * 144,
        b"A" * 10,
    ]
    assert len(dropped) == summary.dropped_reads


def test_failed_output_follows_input_compression(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    plain = tmp_path / "plain.fq"
    write_fastq_file(plain, sample_records())
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    passed = tmp_path / "passed.fq"
    failed = tmp_path / "failed.fq"

    filter_fastq(source, passed, failed_output_path=failed)

    assert passed.read_bytes()[:2] == b"\x1f\x8b"
    assert failed.read_bytes()[:2] == b"\x1f\x8b"


def test_without_failed_output_nothing_extra_is_written(tmp_path: Path) -> None:
    """不传 failed_output_path 时行为与从前一致：只产出一个文件。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    passed = tmp_path / "passed.fq"

    summary = filter_fastq(source, passed)

    assert summary.dropped_reads == 3
    # 目录里只有输入与那一个输出，没有多出失败文件。
    assert sorted(item.name for item in passed.parent.iterdir()) == [
        "passed.fq",
        "reads.fq",
    ]


def test_output_keeps_only_passing_reads(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "passed.fq"

    filter_fastq(source, output)

    records = list(read_fastq(output))
    assert [record.name for record in records] == ["good"]
    assert records[0].sequence == b"A" * 151
    assert records[0].quality == (_Q40 * 151).encode("ascii")


def test_bases_are_counted_before_and_after(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "passed.fq"

    summary = filter_fastq(source, output)

    assert summary.bases_before == 151 * 3 + 10
    assert summary.bases_after == 151
    assert summary.bases_removed == 151 * 2 + 10


def test_too_long_reason(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, [("long", "A" * 300, _Q40)])
    output = tmp_path / "passed.fq"

    summary = filter_fastq(source, output, config=ReadFilterConfig(max_length=250))

    assert summary.failures == {FAIL_TOO_LONG: 1}


def test_all_filters_disabled_still_drops_empty_reads(tmp_path: Path) -> None:
    """零长度 read 是格式层面的失败，关掉所有过滤也仍然会被丢弃。"""
    source = tmp_path / "reads.fq"
    write_fastq_file(source, [("empty", "", _Q40), ("short", "A", _Q40)])
    output = tmp_path / "passed.fq"

    config = ReadFilterConfig(
        enabled_quality=False, enabled_length=False, enabled_complexity=False
    )
    summary = filter_fastq(source, output, config=config)

    assert summary.failures == {FAIL_LENGTH: 1}
    assert [record.name for record in read_fastq(output)] == ["short"]


def test_kept_rate_and_render(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())
    output = tmp_path / "passed.fq"

    summary = filter_fastq(source, output)

    assert summary.kept_rate == pytest.approx(0.25)
    rendered = summary.render()
    assert "通过 read 数：1" in rendered
    # 报告里的原因顺序固定，便于与 fastp 报告逐项对照。
    assert [label for label, _ in summary.reason_counts()] == [
        "failed_quality_filter",
        "failed_too_many_n_bases",
        "failed_too_short",
    ]


def test_empty_input_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")
    output = tmp_path / "passed.fq"

    summary = filter_fastq(source, output)

    assert summary.total_reads == 0
    assert summary.kept_rate == 0.0
    assert output.read_bytes() == b""


def test_gzip_input_follows_input_compression(tmp_path: Path) -> None:
    plain = tmp_path / "reads.fq"
    write_fastq_file(plain, sample_records())
    source = tmp_path / "reads.fq.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    output = tmp_path / "passed.fq"  # 扩展名故意不写 .gz

    summary = filter_fastq(source, output)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert gzip.decompress(output.read_bytes()) == b"@good\n" + b"A" * 151 + b"\n+\n" + (
        _Q40 * 151
    ).encode("ascii") + b"\n"
    assert summary.kept_reads == 1


def test_explicit_compression_choice(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    compressed = tmp_path / "out_gz"  # 名字里不写 .gz，压缩与否只看参数
    filter_fastq(source, compressed, compress=True)
    assert compressed.read_bytes()[:2] == b"\x1f\x8b"

    plain = tmp_path / "out_plain.gz"  # 名字写了 .gz，但参数要求不压缩
    filter_fastq(source, plain, compress=False)
    assert plain.read_bytes()[:2] != b"\x1f\x8b"


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        filter_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_format_error_removes_partial_output(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下残缺的输出文件。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        filter_fastq(broken, output)

    assert not output.exists()


def test_parent_directories_are_created(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq_file(source, sample_records())

    output = tmp_path / "nested" / "deeper" / "passed.fq"
    filter_fastq(source, output)

    assert output.exists()
