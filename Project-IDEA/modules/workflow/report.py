"""report.py：把一次工作流的产出渲染成 JSON 日志与自包含 HTML 报告。

对应上游 fastp 的 ``fastp.json``（机器可读的日志）与 ``fastp.html``（给人看的
报告）。上游那份 HTML 内嵌 JS 画图，本实现沿用 ``read_stats`` 的取舍
（见 ``bio_analysis_function/submodules/read_stats/report.py``）：**不执行任何脚本**，曲线直接画成
SVG，于是报告能离线打开、能进邮件附件，测试也能断言"图上确实有这么多个点"。

两份产物分工明确：

* :func:`build_workflow_json` 是**日志**——写进 log 目录，尽量把每一步的中间量
  都留下，方便复现与排障；
* :func:`render_workflow_html` 是**报告**——只呈现用户关心的结论，且**不带
  时间戳**，同一份数据每次渲染逐字节相同。

画图原语来自 :mod:`..common.svg_report`，工作流报告只是换了一套配色与标题。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Final

from ..bio_analysis_function.common.native.abi import NativeReadStats
from ..bio_analysis_function.common.svg_report import (
    STYLE,
    bar_chart,
    card,
    line_chart,
    section,
)
from .config import WorkflowOutcome

#: 过滤前后对比的配色：前灰后蓝，灰度打印也能区分。
_COMPARISON_COLORS: Final[dict[str, str]] = {
    "过滤前": "#9e9e9e",
    "过滤后": "#1565c0",
}

#: 过滤结果码 → 中文原因。与 ``abi.py`` 的 ``_BREAKDOWN_FIELDS`` 同口径。
_FAILURE_LABELS: Final[dict[int, str]] = {
    12: "N 碱基过多",
    16: "读长过短",
    17: "读长过长",
    20: "质量不合格",
    24: "复杂度过低",
}

#: 接头来源 → 中文说明（对应 ``AdapterDetectionReport`` 的 source 字段）。
_SOURCE_LABELS: Final[dict[str, str]] = {
    "known": "已知接头表",
    "kmer": "从数据拼接",
}


def _round(value: float) -> float:
    """比例统一保留 6 位小数，免得日志里拖着一串浮点尾数。"""
    return round(value, 6)


def _plain(value):
    """把 ``asdict`` 的产物里的 ``Path`` 换成字符串，得到纯 JSON 可序列化的对象。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _aggregate(
    stats1: NativeReadStats, stats2: NativeReadStats, paired: bool
) -> dict[str, float]:
    """把 R1/R2 两份统计合成一个"整体"口径（单端时只用 R1）。

    JSON 的 before/after 与页面的概览卡片都按这个口径取值，两者因此天然一致。
    """
    parts = (stats1, stats2) if paired else (stats1,)
    total_reads = sum(part.total_reads for part in parts)
    total_bases = sum(part.total_bases for part in parts)

    def rate(attribute: str) -> float:
        if not total_bases:
            return 0.0
        return sum(getattr(part, attribute) for part in parts) / total_bases

    return {
        "total_reads": total_reads,
        "total_bases": total_bases,
        "q20_rate": rate("q20_bases"),
        "q30_rate": rate("q30_bases"),
        "q40_rate": rate("q40_bases"),
        "gc_content": rate("gc_bases"),
        "mean_length": total_bases // total_reads if total_reads else 0,
        "read1_mean_length": stats1.mean_length,
        "read2_mean_length": stats2.mean_length,
    }


def _before_after(
    outcome: WorkflowOutcome,
) -> tuple[dict[str, float], dict[str, float]]:
    """过滤前 / 过滤后各一份整体口径。"""
    paired = outcome.paired
    before = _aggregate(outcome.pre_stats1, outcome.pre_stats2, paired)
    after = _aggregate(outcome.post_stats1, outcome.post_stats2, paired)
    return before, after


# ---------------------------------------------------------------------------
# JSON 日志
# ---------------------------------------------------------------------------


def build_workflow_json(outcome: WorkflowOutcome) -> dict:
    """把一次工作流的全部统计整成一份可直接写盘的 JSON（各步作节）。

    形状参照上游 fastp 的 ``fastp.json``：顶层每个键是一个环节，键名尽量对齐，
    便于拿两份日志对照。之所以比 fastp 多留了若干"过程量"（各步改动量、阶段 A
    的判定），是因为本文件是写进 log 目录的排障材料，不是给用户看的报告。
    """
    config = outcome.config
    summary = outcome.summary
    before, after = _before_after(outcome)

    document: dict = {
        "summary": _summary_node(outcome, before, after),
        "scan": _scan_node(outcome),
        "filtering_result": {
            "filtered_reads": summary.filtered_reads,
            "filtered_rate": _round(summary.filtered_rate),
            # ``failures`` 的键是结果码整数，而 JSON 只接受字符串键，这里统一转换。
            "failures": {
                str(code): count for code, count in sorted(summary.failures.items())
            },
        },
        "normalization": {"normalized_reads": summary.normalized_reads},
        "index_filtering": {"filtered_reads": summary.index_filtered_reads},
        "umi": {"tagged_reads": summary.umi_tagged_reads},
        "quality_trimming": {"trimmed_reads": summary.trimmed_reads},
        "poly_trimming": {"trimmed_reads": summary.poly_trimmed_reads},
        "adapter_cutting": {
            "trimmed_reads": summary.adapter_trimmed_reads,
            "dimer_pairs": summary.adapter_dimer_pairs,
        },
    }

    if outcome.paired:
        document["base_correction"] = {
            "corrected_pairs": summary.corrected_pairs,
            "corrected_bases": summary.corrected_bases,
        }

    document["deduplication"] = {
        "duplicate_reads": summary.duplicate_reads,
        "duplicate_rate": _round(summary.duplicate_rate),
        "accuracy_level": summary.dedup_accuracy_level,
    }

    if config.merged:
        document["merging"] = {
            "merged_pairs": summary.pairs_merged,
            "gap_overlap_pairs": summary.gap_overlap_pairs,
        }

    if outcome.paired:
        document["insert_size"] = _insert_size_node(outcome)

    document["outputs"] = {
        "files": [str(path) for path in outcome.outputs],
        "split_files": [str(path) for path in outcome.split_files],
    }
    # ``command`` 用 dict 而不是拼好的命令行字符串：本层的配置项远多于一条命令
    # 行能表达的范围（分卷换算、polyG 自动判定等都发生在这里），拼成字符串反而
    # 会丢掉可复现所需的信息。
    document["command"] = _plain(asdict(config))
    return document


def _summary_node(
    outcome: WorkflowOutcome, before: dict[str, float], after: dict[str, float]
) -> dict:
    """读入 / 写出两份汇总，外加单双端口径。"""
    node = {
        "sequencing": "paired end" if outcome.paired else "single end",
        "before_filtering": _filtering_node(outcome, before),
        "after_filtering": _filtering_node(outcome, after),
    }
    if outcome.paired:
        node["unpaired_reads"] = outcome.summary.unpaired_reads
    return node


def _filtering_node(outcome: WorkflowOutcome, totals: dict[str, float]) -> dict:
    node = {
        "total_reads": totals["total_reads"],
        "total_bases": totals["total_bases"],
        "q20_rate": _round(totals["q20_rate"]),
        "q30_rate": _round(totals["q30_rate"]),
        "q40_rate": _round(totals["q40_rate"]),
        "gc_content": _round(totals["gc_content"]),
        "read1_mean_length": totals["read1_mean_length"],
    }
    if outcome.paired:
        node["read2_mean_length"] = totals["read2_mean_length"]
    return node


def _scan_node(outcome: WorkflowOutcome) -> dict:
    """阶段 A 的结论：二色判定、polyG 是否开启、检出的接头与采样量。"""
    scan = outcome.scan
    adapter = scan.adapter
    return {
        "read_count": scan.read_count,
        "is_two_color": scan.is_two_color,
        "poly_g_enabled": scan.poly_g_enabled,
        "adapter1": adapter.adapter1,
        "adapter1_source": adapter.source1,
        "adapter2": adapter.adapter2,
        "adapter2_source": adapter.source2,
        "sampled_reads": adapter.sampled_reads,
    }


def _insert_size_node(outcome: WorkflowOutcome) -> dict:
    """插入片段分布的峰值、判不出的条数与直方图。

    直方图**只写非零区间**：它每个长度一桶、桶数可达上百，整段写出来能把日志
    撑大几个数量级，而"哪些长度一个片段都没有"对排障没有信息量。口径与
    ``NativeReadStats`` 的各分布表一致（同样只保留非零项）。最后一项是
    "判不出或超上限"的溢出桶，不是真实长度，已单列到 ``unknown``。
    """
    histogram = outcome.insert_size_histogram
    return {
        "peak": outcome.summary.insert_size_peak,
        "unknown": outcome.summary.insert_size_unknown,
        "histogram": {
            str(length): count
            for length, count in enumerate(histogram[:-1])
            if count
        },
    }


# ---------------------------------------------------------------------------
# HTML 报告
# ---------------------------------------------------------------------------


def render_workflow_html(outcome: WorkflowOutcome) -> str:
    """渲染呈现给用户的汇总 HTML 报告。

    结构：概览卡片 → 过滤结果 → 三条前后对比曲线（质量 / 含量 / 读长）→
    各步改动量 → 插入片段（双端）→ 阶段 A 结论 → 输出文件清单。
    报告**不带时间戳**，同一份数据两次渲染逐字节相同。
    """
    _, after = _before_after(outcome)

    sections = [
        section("过滤结果", _failure_table(outcome)),
        section("质量曲线（过滤前后）", _quality_body(outcome)),
        section("碱基含量（GC 占比，过滤前后）", _content_body(outcome)),
        section("读长分布（过滤前后）", _length_body(outcome)),
        section("各步改动量", _step_table(outcome)),
    ]
    if outcome.paired:
        sections.append(section("插入片段长度分布", _insert_size_body(outcome)))
    sections.append(section("阶段 A 结论（预扫描）", _scan_body(outcome)))
    sections.append(section("输出文件清单", _outputs_body(outcome)))

    title = outcome.config.report_title
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{escape(title)}</h1>\n"
        '<p class="meta">本报告由 Project IDEA 生物方法模块生成；'
        "内容不含时间戳，同一份数据每次渲染的结果完全相同。</p>\n"
        f'<div class="cards">{"".join(_overview_cards(outcome, after))}</div>\n'
        f'{"".join(sections)}\n'
        "</body>\n</html>\n"
    )


def _overview_cards(outcome: WorkflowOutcome, after: dict[str, float]) -> list[str]:
    summary = outcome.summary
    return [
        card("读入 read 数", f"{summary.total_reads:,}"),
        card("写出 read 数", f"{summary.output_reads:,}"),
        card("保留比例", f"{outcome.retention_rate:.2%}"),
        card("读入碱基数", f"{summary.total_bases:,}"),
        card("写出碱基数", f"{summary.output_bases:,}"),
        card("Q20 及以上（过滤后）", f"{after['q20_rate']:.2%}"),
        card("Q30 及以上（过滤后）", f"{after['q30_rate']:.2%}"),
        card("GC 含量（过滤后）", f"{after['gc_content']:.2%}"),
        card("平均读长（过滤后）", str(after["mean_length"])),
    ]


def _failure_table(outcome: WorkflowOutcome) -> str:
    failures = outcome.summary.failures
    if not failures:
        return '<p class="empty">没有被过滤掉的 read。</p>'
    rows = [
        (_failure_label(code), f"{count:,}")
        for code, count in sorted(failures.items())
    ]
    return _table(("过滤原因", "read 数"), rows)


def _failure_label(code: int) -> str:
    """结果码 → 中文原因；遇到本层还不认识的新码就照实写出来。"""
    return _FAILURE_LABELS.get(code, f"结果码 {code}")


def _comparison_groups(outcome: WorkflowOutcome):
    """要做前后对比的几组统计：单端一组，双端 R1 / R2 各一组。"""
    if outcome.paired:
        return (
            ("R1", outcome.pre_stats1, outcome.post_stats1),
            ("R2", outcome.pre_stats2, outcome.post_stats2),
        )
    return (("", outcome.pre_stats1, outcome.post_stats1),)


def _comparison_chart(
    before: Sequence[float],
    after: Sequence[float],
    *,
    y_label: str,
    y_max: float | None = None,
) -> str:
    """过滤前后画在同一张图上，横轴取较长的那条。

    **短的那条不补 0、直接在自己的长度处收线**：过滤后读长会变短，补 0 会把
    "这些位置本来就没有碱基"画成"这些位置的值是 0"，在质量与 GC 两张图上都
    会被误读成"后面质量为 0 / GC 为 0"。``line_chart`` 支持各条曲线长度不同，
    横坐标按最长的那条换算，所以收线是安全的。
    """
    length = max(len(before), len(after), 1)
    return line_chart(
        {"过滤前": tuple(before), "过滤后": tuple(after)},
        y_label=y_label,
        colors=_COMPARISON_COLORS,
        y_max=y_max,
        x_labels=("1", str(length)),
    )


def _quality_body(outcome: WorkflowOutcome) -> str:
    parts = []
    for label, pre, post in _comparison_groups(outcome):
        if label:
            parts.append(f'<p class="note">{escape(label)}</p>')
        parts.append(
            _comparison_chart(
                pre.quality_curves.get("mean", ()),
                post.quality_curves.get("mean", ()),
                y_label="平均质量（Phred）",
            )
        )
    return "".join(parts)


def _content_body(outcome: WorkflowOutcome) -> str:
    parts = []
    for label, pre, post in _comparison_groups(outcome):
        if label:
            parts.append(f'<p class="note">{escape(label)}</p>')
        parts.append(
            _comparison_chart(
                pre.content_curves.get("GC", ()),
                post.content_curves.get("GC", ()),
                y_label="GC 占比",
                y_max=1.0,
            )
        )
    return "".join(parts)


def _length_body(outcome: WorkflowOutcome) -> str:
    parts = []
    for label, pre, post in _comparison_groups(outcome):
        prefix = f"{label}：" if label else ""
        parts.append(f'<p class="note">{escape(prefix + "过滤前")}</p>')
        parts.append(bar_chart(pre.length_counts, y_label="条数", x_label="读长"))
        parts.append(f'<p class="note">{escape(prefix + "过滤后")}</p>')
        parts.append(bar_chart(post.length_counts, y_label="条数", x_label="读长"))
    return "".join(parts)


def _step_table(outcome: WorkflowOutcome) -> str:
    """各步实际改动的条数；没开或没改动（计数为 0）的步骤不出现。"""
    summary = outcome.summary
    steps = [
        ("规范化", summary.normalized_reads),
        ("按 index 过滤", summary.index_filtered_reads),
        ("UMI 提取", summary.umi_tagged_reads),
        ("质量剪切", summary.trimmed_reads),
        ("poly 修剪", summary.poly_trimmed_reads),
        ("接头裁剪", summary.adapter_trimmed_reads),
    ]
    if outcome.paired:
        steps.append(("碱基校正", summary.corrected_pairs))
    steps.append(("去重", summary.duplicate_reads))
    rows = [(name, f"{count:,}") for name, count in steps if count]
    if not rows:
        return '<p class="empty">本次运行没有改动任何 read。</p>'
    return _table(("步骤", "改动条数"), rows)


def _insert_size_body(outcome: WorkflowOutcome) -> str:
    summary = outcome.summary
    # 最后一项是"判不出或超上限"的溢出桶，不是真实长度，不画进柱状图。
    histogram = {
        length: count
        for length, count in enumerate(outcome.insert_size_histogram[:-1])
        if count
    }
    note = (
        f'<p class="note">峰值 {summary.insert_size_peak}，'
        f"判不出的 {summary.insert_size_unknown:,} 对。</p>"
    )
    return note + bar_chart(histogram, y_label="read 对数", x_label="插入片段长度")


def _scan_body(outcome: WorkflowOutcome) -> str:
    scan = outcome.scan
    adapter = scan.adapter
    rows = [
        ("测序化学", "二色系统（自动开启 polyG）" if scan.is_two_color else "四色系统"),
        ("polyG 修剪", "开启" if scan.poly_g_enabled else "关闭"),
        ("接头 R1", _adapter_text(adapter.adapter1, adapter.source1)),
    ]
    if outcome.paired:
        rows.append(("接头 R2", _adapter_text(adapter.adapter2, adapter.source2)))
    rows.append(("采样 read 数", f"{adapter.sampled_reads:,}"))
    return _table(("结论项", "值"), rows)


def _adapter_text(sequence: str, source: str) -> str:
    if not sequence:
        return "未检测到"
    return f"{sequence}（来源：{_SOURCE_LABELS.get(source, source or '未知')}）"


def _outputs_body(outcome: WorkflowOutcome) -> str:
    if not outcome.outputs:
        return '<p class="empty">没有写出任何文件。</p>'
    body = _table(("输出文件",), [(str(path),) for path in outcome.outputs])
    if outcome.split_files:
        body += f'<p class="note">共分 {len(outcome.split_files)} 卷，产物见上表。</p>'
    return body


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{escape(name)}</th>" for name in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
