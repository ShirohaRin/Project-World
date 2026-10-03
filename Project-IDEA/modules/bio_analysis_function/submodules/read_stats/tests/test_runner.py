"""文件级接口与报告序列化的测试。

除了"能读完一份文件"，重点守两件事：

1. **统计与逐条累加的结果一致**——文件级这一层不能悄悄改口径；
2. **JSON 是可解析的、且不丢信息**——它是给前端与存档用的，字段少一个就是缺数据。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.read_stats import (
    ReadStatsCollector,
    render_report,
    report_to_json,
    stat_fastq,
)

_DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = _DATA_DIR / "fastp_R1.fq"

_Q40 = chr(40 + 33)


def write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    """按 (名字, 序列, 质量符) 的三元组写一份 FASTQ。"""
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality_char in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality_char * len(sequence)}\n")


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------


def test_matches_manual_accumulation(tmp_path: Path) -> None:
    """文件级接口与"逐条喂进累加器"给出一模一样的结果。"""
    source = tmp_path / "reads.fq"
    write_fastq(
        source,
        [
            ("r1", "ACGTACGT", _Q40),
            ("r2", "TTTTGGGG", chr(20 + 33)),
            ("r3", "ACGTNACG", _Q40),
        ],
    )

    from_file = stat_fastq(source)

    collector = ReadStatsCollector()
    for record in read_fastq(source):
        collector.add(record.sequence, record.quality)
    manual = collector.summarize()

    assert from_file == manual


def test_real_data_is_readable() -> None:
    """共享真实数据（9 条，其中 1 条长度为 0）能正常统计。"""
    summary = stat_fastq(_READS)

    assert summary.total_reads == 9
    assert summary.total_bases > 0
    assert 0 in summary.length_counts
    assert 0.0 <= summary.gc_content <= 1.0
    assert 0.0 <= summary.q30_rate <= 1.0
    assert len(summary.quality_curves["mean"]) == summary.cycles


def test_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")

    summary = stat_fastq(source)

    assert summary.total_reads == 0
    assert summary.cycles == 0
    assert summary.kmer_counts == tuple([0] * 1024)
    assert sum(summary.kmer_counts) == 0


def test_gzip_input(tmp_path: Path) -> None:
    plain = tmp_path / "reads.fq"
    write_fastq(plain, [("r1", "ACGTACGT", _Q40)])
    source = tmp_path / "reads.fq.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())

    assert stat_fastq(source) == stat_fastq(plain)


def test_missing_input_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        stat_fastq(tmp_path / "absent.fq")


def test_broken_input_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        stat_fastq(broken)


def test_analysis_writes_nothing(tmp_path: Path) -> None:
    """本算法只读不写：跑完之后目录里还是只有输入那一个文件。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, [("r1", "ACGTACGT", _Q40)])

    stat_fastq(source)

    assert [item.name for item in tmp_path.iterdir()] == ["reads.fq"]


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def test_json_round_trip_carries_everything() -> None:
    summary = stat_fastq(_READS)

    payload = json.loads(report_to_json(summary))

    assert payload["total_reads"] == summary.total_reads
    assert payload["total_bases"] == summary.total_bases
    assert len(payload["kmer_counts"]) == 1024
    assert sum(payload["kmer_counts"]) > 0
    assert set(payload["curve_bases"]) == {"A", "T", "C", "G", "N"}
    for name in ("mean", "A", "T", "C", "G", "N"):
        assert len(payload["quality_curves"][name]) == summary.cycles
    for name in ("A", "T", "C", "G", "N", "GC"):
        assert len(payload["content_curves"][name]) == summary.cycles


def test_json_kmer_top_is_ranked_and_named() -> None:
    """``kmer_top`` 按计数降序，且带可读的碱基串（不然 1024 个数没法看）。"""
    summary = stat_fastq(_READS)

    payload = json.loads(report_to_json(summary))
    top = payload["kmer_top"]

    assert top, "真实数据里应当有 k-mer"
    counts = [item["count"] for item in top]
    assert counts == sorted(counts, reverse=True)
    assert all(len(item["kmer"]) == 5 for item in top)
    assert all(count > 0 for count in counts)


def test_json_is_plain_ascii_safe_and_parseable() -> None:
    """JSON 里不能出现 NaN / Infinity 之类的非标准字面量。"""
    summary = stat_fastq(_READS)

    text = report_to_json(summary)

    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["total_reads"] == summary.total_reads


def test_render_report_mentions_key_facts() -> None:
    summary = stat_fastq(_READS)

    text = render_report(summary)

    assert f"read 条数：{summary.total_reads}" in text
    assert "Q30 及以上" in text
    assert "GC 含量" in text
    assert "读长范围" in text


def test_render_report_on_empty_input_does_not_crash() -> None:
    collector = ReadStatsCollector()

    text = render_report(collector.summarize())

    assert "read 条数：0" in text
