"""HTML 报告渲染的测试。

渲染这类东西最容易"看起来对了其实不对"，所以这里守的是几条**硬性质**：

1. 报告**不执行任何脚本**（没有 ``<script>``）、**不引用外部资源**
   （没有 ``http://`` / ``src=``）——它必须是能离线打开、能进邮件附件的静态文件；
2. 同一份数据渲染两次**逐字节相同**（所以默认不带时间戳）；
3. 图上真的有那么多点（不是"画了个空壳"）；
4. 用户给进来的文本一律转义（标题里带 ``<`` 不能把页面搞坏）。
"""

from __future__ import annotations

import re
from pathlib import Path

from modules.bio_analysis_function.submodules.read_stats import stat_fastq
from modules.bio_analysis_function.submodules.read_stats.report import render_html

_DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = _DATA_DIR / "fastp_R1.fq"

_POLYLINE_POINTS = re.compile(r'points="([^"]*)"')


def _point_counts(html: str) -> list[int]:
    """每条折线的点数。"""
    return [len(points.split()) for points in _POLYLINE_POINTS.findall(html)]


# ---------------------------------------------------------------------------
# 硬性质
# ---------------------------------------------------------------------------


def test_report_is_self_contained() -> None:
    html = render_html(stat_fastq(_READS))

    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "<script" not in html.lower()          # 不执行任何脚本
    assert "http://" not in html and "https://" not in html
    assert " src=" not in html                     # 不引用外部资源
    assert html.rstrip().endswith("</html>")


def test_report_is_reproducible() -> None:
    """默认不带时间戳，所以同一份数据两次渲染必须逐字节相同。"""
    summary = stat_fastq(_READS)

    assert render_html(summary) == render_html(summary)


def test_timestamp_only_when_asked() -> None:
    summary = stat_fastq(_READS)

    assert "2050-01-01" not in render_html(summary)
    assert "2050-01-01" in render_html(summary, generated_at="2050-01-01 00:00")


def test_charts_have_the_right_number_of_points() -> None:
    """质量与含量各 6 条线；整体质量曲线的点数必须等于 cycle 数。"""
    summary = stat_fastq(_READS)

    html = render_html(summary)
    quality_section = html.split("按测序位置的碱基含量")[0]

    counts = _point_counts(quality_section)
    assert len(counts) == 6                      # mean + A/T/C/G/N
    assert max(counts) == summary.cycles


def test_user_supplied_text_is_escaped() -> None:
    summary = stat_fastq(_READS)

    html = render_html(summary, title="<b>危险</b>", extra_cards=[("<x>", "1 & 2")])

    assert "<b>危险</b>" not in html
    assert "&lt;b&gt;" in html
    assert "1 &amp; 2" in html


# ---------------------------------------------------------------------------
# 内容
# ---------------------------------------------------------------------------


def test_summary_cards_show_the_numbers() -> None:
    summary = stat_fastq(_READS)

    html = render_html(summary)

    assert f"{summary.total_reads:,}" in html
    assert f"{summary.q30_rate:.2%}" in html
    assert f"{summary.gc_content:.2%}" in html


def test_extra_cards_and_sections_are_merged() -> None:
    summary = stat_fastq(_READS)

    html = render_html(
        summary,
        extra_cards=[("重复率", "12.5%")],
        extra_sections=[("处理前后对比", "<p>这里是流程层补的内容</p>")],
    )

    assert "重复率" in html and "12.5%" in html
    assert "处理前后对比" in html
    assert "这里是流程层补的内容" in html


def test_kmer_section_can_be_disabled() -> None:
    summary = stat_fastq(_READS)

    assert "5-mer 频次" in render_html(summary)
    assert "5-mer 频次" not in render_html(summary, kmer_limit=0)


def test_kmer_table_lists_at_most_the_limit() -> None:
    summary = stat_fastq(_READS)

    html = render_html(summary, kmer_limit=3)
    section = html.split("5-mer 频次")[1]

    assert section.count("<code>") == 3


def test_large_length_distribution_is_truncated_with_a_note() -> None:
    """读长种类很多时只画前若干个，并且**必须**说明被截断了。"""
    from dataclasses import replace

    summary = replace(
        stat_fastq(_READS),
        length_counts={length: length for length in range(1, 400)},
    )

    html = render_html(summary)

    assert "图中只画了出现最多的" in html
    assert html.count("<rect") < 400


# ---------------------------------------------------------------------------
# 空数据
# ---------------------------------------------------------------------------


def test_empty_input_still_renders() -> None:
    from modules.bio_analysis_function.submodules.read_stats import ReadStatsCollector

    html = render_html(ReadStatsCollector().summarize())

    assert html.startswith("<!DOCTYPE html>")
    assert "没有数据，无法作图。" in html
    assert "read 条数" in html


def test_all_n_data_renders_without_kmer_rows() -> None:
    """整条全 N 的数据没有 k-mer，那一节要给出说明而不是空表。"""
    from modules.bio_analysis_function.submodules.read_stats import ReadStatsCollector

    collector = ReadStatsCollector()
    collector.add(b"N" * 40, b"I" * 40)

    html = render_html(collector.summarize())

    assert "没有出现任何 5-mer" in html
