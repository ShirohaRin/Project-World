"""报告层：JSON 日志（给我们看）与自包含 HTML（给用户看）。

分工与 fastp 工作流那份报告一致（见上级目录 `workflow.md` 5.1）：

* :func:`build_breseq_json` 是**日志**——写进 log 目录，尽量留下每一步的中间量（阶段 A 的统计、
  比对构成、完整参数、每条突变的证据与注释），排障与调参靠它；
* :func:`render_breseq_html` 是**报告**——只呈现用户关心的结论：概览、突变表、频率分布、
  读段与比对概况、产物清单。

两份都**不带时间戳**，同一份数据每次渲染逐字节相同——报告本身也是可复现的产物。
HTML 内嵌样式、**不执行任何脚本、不引外部资源**，能离线打开、当邮件附件发；
绘图原语（居中柱状图与卡片）来自 `common/svg_report.py`，与 read_stats / fastp 报告共用一套。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any, Final

from ...bio_analysis_function.common.svg_report import STYLE, bar_chart, card, section
from .config import BreseqOutcome

__all__ = [
    "BRESEQ_LOG_NAME",
    "build_breseq_json",
    "render_breseq_html",
    "write_breseq_reports",
]

#: 日志 JSON 的文件名（写进 `log_dir`）。
BRESEQ_LOG_NAME = "breseq_workflow.json"

_TITLE = "breseq 参考比对与变异检测报告"

#: 两个判定档的中文名（报告里给用户看的）。
_PREDICTION_LABELS: Final[dict[str, str]] = {
    "consensus": "共识档（纯合）",
    "polymorphism": "多态档（混合）",
}

#: 表格与产物清单的样式（`svg_report.STYLE` 只管卡片与图，表格是本层新增的）。
_EXTRA_STYLE = """
table.data { border-collapse: collapse; background: #fff; font-size: 13px; }
table.data th, table.data td { border: 1px solid #e0e0e0; padding: 6px 10px; text-align: left; }
table.data th { background: #f5f5f5; font-weight: 600; }
table.data td.num, table.data th.num { text-align: right; font-variant-numeric: tabular-nums; }
ul.files { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
           padding: 12px 12px 12px 28px; margin: 0; }
"""


def _prediction_label(prediction: str) -> str:
    return _PREDICTION_LABELS.get(prediction, prediction)


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, numeric: Sequence[int] = ()) -> str:
    """一张朴素的表（数字列右对齐）。``numeric`` 给出需要右对齐的列序号。"""
    head = "".join(
        f'<th class="num">{escape(name)}</th>' if index in numeric else f"<th>{escape(name)}</th>"
        for index, name in enumerate(headers)
    )
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="num">{escape(value)}</td>'
            if index in numeric
            else f"<td>{escape(value)}</td>"
            for index, value in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return (
        '<table class="data"><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def build_breseq_json(outcome: BreseqOutcome) -> dict[str, Any]:
    """把一次 run 的结果摊成日志 JSON（键名尽量直白，便于与产物对照）。"""
    config = outcome.config
    return {
        "workflow": "breseq",
        "reference": [str(path) for path in config.reference],
        "reads": {
            "read1": str(config.read1),
            "read2": str(config.read2) if config.read2 is not None else None,
        },
        "scan": asdict(outcome.scan),
        "mapping": {
            "mapped": outcome.mapped_reads,
            "unmapped": outcome.unmapped_reads,
            "unique": outcome.unique_reads,
            "repeat": outcome.repeat_reads,
            "mapped_fraction": outcome.mapped_fraction,
        },
        "settings": {
            "mapping": asdict(config.mapping),
            "consensus": asdict(config.consensus),
            "polymorphism": (
                asdict(config.polymorphism) if config.polymorphism is not None else None
            ),
            "trim_read_ends": config.trim_read_ends,
            "default_quality": config.default_quality,
            "max_reads": config.max_reads,
        },
        "counts": {
            "variants": len(outcome.variants),
            "consensus": len(outcome.consensus_variants),
            "polymorphism": len(outcome.polymorphism_variants),
        },
        "variants": [
            {
                "seq_id": variant.seq_id,
                "position": variant.position,
                "type": variant.type,
                "reference_base": variant.reference_base,
                "call_base": variant.call_base,
                "frequency": variant.frequency,
                "prediction": variant.prediction,
                "score": variant.score,
                "annotation": variant.annotation,
            }
            for variant in outcome.variants
        ],
        "outputs": [str(path) for path in outcome.outputs],
    }


def _overview(outcome: BreseqOutcome) -> str:
    scan = outcome.scan
    return '<div class="cards">' + "".join(
        (
            card("参考序列", "、".join(scan.seq_ids) or "（无）"),
            card("参考长度", f"{scan.reference_length:,} bp"),
            card("读段数", f"{scan.reads:,}"),
            card("读长", f"{scan.shortest_read}–{scan.longest_read} bp"),
            card("比对上", f"{outcome.mapped_reads:,}"),
            card("比对率", f"{outcome.mapped_fraction:.1%}"),
            card("突变数", f"{len(outcome.variants)}"),
            card("共识档", f"{len(outcome.consensus_variants)}"),
            card("多态档", f"{len(outcome.polymorphism_variants)}"),
        )
    ) + "</div>"


def _variant_section(outcome: BreseqOutcome) -> str:
    if not outcome.variants:
        return '<p class="empty">没有检出与参考不一致的突变。</p>'
    headers = ("参考", "位置", "类型", "变化", "频率", "判定档", "打分", "注释")
    rows = [
        (
            variant.seq_id,
            f"{variant.position:,}",
            variant.type,
            f"{variant.reference_base}→{variant.call_base}",
            f"{variant.frequency:.3f}",
            _prediction_label(variant.prediction),
            f"{variant.score:.1f}",
            variant.annotation or "—",
        )
        for variant in outcome.variants
    ]
    return _table(headers, rows, numeric=(1, 4, 6))


def _frequency_section(outcome: BreseqOutcome) -> str:
    counts: dict[int, int] = {}
    for variant in outcome.variants:
        bucket = max(0, min(100, round(variant.frequency * 100)))
        counts[bucket] = counts.get(bucket, 0) + 1
    if not counts:
        return '<p class="empty">没有突变，画不出频率分布。</p>'
    return bar_chart(counts, y_label="突变数", x_label="频率 (%)")


def _reads_section(outcome: BreseqOutcome) -> str:
    scan = outcome.scan
    headers = ("项", "值")
    rows = [
        ("输入类型", "双端（按单端口径比对，不用配对信息）" if scan.read_pairs else "单端"),
        ("读段条数", f"{scan.reads:,}"),
        ("碱基数", f"{scan.bases:,}"),
        ("读长范围", f"{scan.shortest_read}–{scan.longest_read} bp"),
        ("平均读长", f"{scan.mean_read_length:.1f} bp"),
        ("参考特征数", f"{scan.feature_count:,}"),
        ("比对上", f"{outcome.mapped_reads:,}"),
        ("未比对上", f"{outcome.unmapped_reads:,}"),
        ("唯一命中", f"{outcome.unique_reads:,}"),
        ("多命中", f"{outcome.repeat_reads:,}"),
        ("读入是否被截断", "是（设置了 max_reads）" if scan.cropped else "否"),
    ]
    return _table(headers, rows)


def _files_section(outcome: BreseqOutcome) -> str:
    items = "".join(f"<li>{escape(str(path))}</li>" for path in outcome.outputs)
    return f'<ul class="files">{items}</ul>'


def render_breseq_html(outcome: BreseqOutcome) -> str:
    """渲染自包含 HTML 报告（无脚本、无外部资源、不含时间戳）。"""
    meta = "参考：" + "、".join(outcome.scan.seq_ids) + f"；读段：{outcome.scan.reads:,} 条"
    body = (
        f"<h1>{escape(_TITLE)}</h1>\n"
        f'<p class="meta">{escape(meta)}</p>\n'
        + section("概览", _overview(outcome))
        + section("突变", _variant_section(outcome))
        + section("突变频率分布", _frequency_section(outcome))
        + section("读段与比对", _reads_section(outcome))
        + section("产物", _files_section(outcome))
    )
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{escape(_TITLE)}</title>\n"
        f"<style>{STYLE}{_EXTRA_STYLE}</style>\n</head>\n<body>\n"
        f"{body}"
        "</body>\n</html>\n"
    )


def write_breseq_reports(
    outcome: BreseqOutcome,
    *,
    html_path: str | Path | None = None,
    log_dir: str | Path | None = None,
) -> tuple[Path | None, Path | None]:
    """按需写出两份报告，返回 ``(HTML 路径, JSON 路径)``（没写的是 ``None``）。"""
    written_html: Path | None = None
    written_log: Path | None = None
    if html_path is not None:
        target = Path(html_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_breseq_html(outcome), encoding="utf-8")
        written_html = target
    if log_dir is not None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / BRESEQ_LOG_NAME
        target.write_text(
            json.dumps(build_breseq_json(outcome), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written_log = target
    return written_html, written_log
