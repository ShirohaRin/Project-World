"""report.py：把质量统计渲染成一份**自包含**的 HTML 报告。

对应上游的 ``Stats::reportHtml*``（它输出一份内嵌 JS 的 HTML）。本实现的取舍
与上游不同，理由写在 `read_stats.md` 里：**不内嵌 JS，改用 SVG**。

--------------------------------------------------------------------------
为什么是 SVG 而不是 JS
--------------------------------------------------------------------------
上游的报告把数据塞进 JS 变量、由浏览器端脚本画图。本实现直接把图画成
**SVG 元素**，好处有三个：

1. 报告是一个**静态文件**——不执行任何脚本，邮件附件、内网页面、
   离线存档都不会被拦；
2. 用户能在浏览器里直接"另存为图片/PDF"，曲线依然是矢量；
3. 测试可以断言"图上确实有这么多个点"，不必跑 JS 引擎。

--------------------------------------------------------------------------
为什么报告不带时间戳
--------------------------------------------------------------------------
上游的报告里写了生成时间，这让**同一份数据每次生成的内容都不一样**。
本实现默认不写时间（要写就显式传 ``generated_at``），于是"同一份数据 →
逐字节相同的报告"成立，报告也能当作可复现的产物来校验。

--------------------------------------------------------------------------
与流程级信息的关系
--------------------------------------------------------------------------
上游的 HTML 报告把整条流程的结论汇总在一起（处理前后的读数、重复率、
插入片段分布……）。本模块只渲染**它能拿到的那部分**——质量统计。
其余数据由调用方通过 ``extra_cards``（额外的摘要条目）与 ``extra_sections``
（额外的整节 HTML）补进来，这样报告层不必知道流程长什么样。

--------------------------------------------------------------------------
绘图原语
--------------------------------------------------------------------------
样式常量与画图函数（折线 / 柱状 / 图例 / 卡片 / 整节）与 read_stats 的语义无关，
工作流报告也要用同一套，因此已上移到 :mod:`..common.svg_report`。本文件只留下
read_stats 专属的两样东西：曲线配色与 5-mer 表。
"""

from __future__ import annotations

from html import escape
from typing import Final, Sequence

from ...common.svg_report import STYLE, bar_chart, card, line_chart, section
from .algorithm import ReadStatsSummary, kmer_name

#: 曲线的配色。取色原则是"四色可辨、灰度打印也分得开"。
_CURVE_COLORS: Final[dict[str, str]] = {
    "A": "#2e7d32",
    "T": "#c62828",
    "C": "#1565c0",
    "G": "#ef6c00",
    "N": "#616161",
    "GC": "#6a1b9a",
    "mean": "#212121",
}


def render_html(
    summary: ReadStatsSummary,
    *,
    title: str = "reads 质量报告",
    generated_at: str | None = None,
    extra_cards: Sequence[tuple[str, str]] = (),
    extra_sections: Sequence[tuple[str, str]] = (),
    kmer_limit: int = 20,
) -> str:
    """把质量统计渲染成一份自包含的 HTML 报告。

    参数：
        summary: :func:`~.runner.stat_fastq` 给出的统计。
        title: 报告标题，也作为 ``<title>``。
        generated_at: 生成时间。默认 ``None``——**不写时间**，这样同一份数据
            每次渲染的 HTML 逐字节相同，报告本身也可当作可复现的产物。
            需要时间戳时由调用方传入字符串（本模块不读时钟）。
        extra_cards: 额外的摘要条目 ``(标签, 值)``，供流程层补进读数变化、
            重复率之类的信息。
        extra_sections: 额外的整节内容 ``(标题, HTML 片段)``。片段由调用方
            负责转义——本模块只把它原样拼进页面。
        kmer_limit: 5-mer 表列出前多少条；0 表示不出这一节。

    返回：
        完整的 HTML 文本（内嵌样式与 SVG，不引用任何外部资源、不执行脚本）。
    """
    cards = [
        card("read 条数", f"{summary.total_reads:,}"),
        card("碱基总数", f"{summary.total_bases:,}"),
        card("平均读长", str(summary.mean_length)),
        card("cycle 数", str(summary.cycles)),
        card("Q20 及以上", f"{summary.q20_rate:.2%}"),
        card("Q30 及以上", f"{summary.q30_rate:.2%}"),
        card("Q40 及以上", f"{summary.q40_rate:.2%}"),
        card("GC 含量", f"{summary.gc_content:.2%}"),
    ]
    cards.extend(card(label, value) for label, value in extra_cards)

    sections = [
        section(
            "按测序位置的质量",
            line_chart(
                {name: summary.quality_curves.get(name, ()) for name in ("mean", "A", "T", "C", "G", "N")},
                y_label="平均质量（Phred）",
                colors=_CURVE_COLORS,
                x_labels=("1", str(max(summary.cycles, 1))),
            )
            + '<p class="note">看曲线的<b>形状</b>：平着下降是正常的测序衰减；某一位突然掉下去，'
            "往往是那个循环的化学问题。</p>",
        ),
        section(
            "按测序位置的碱基含量",
            line_chart(
                {name: summary.content_curves.get(name, ()) for name in ("A", "T", "C", "G", "N")},
                y_label="占比",
                colors=_CURVE_COLORS,
                y_max=1.0,
                x_labels=("1", str(max(summary.cycles, 1))),
            )
            + '<p class="note">四条线如果严重不平行，可能是污染或建库偏好；'
            "某个位置 N 突然变多，通常是质量塌方的先兆。</p>",
        ),
        section(
            "GC 含量（按位置）",
            line_chart(
                {"GC": summary.content_curves.get("GC", ())},
                y_label="GC 占比",
                colors=_CURVE_COLORS,
                y_max=1.0,
                x_labels=("1", str(max(summary.cycles, 1))),
            ),
        ),
        section(
            "读长分布",
            bar_chart(summary.length_counts, y_label="条数", x_label="读长"),
        ),
        section(
            "质量值分布",
            bar_chart(
                summary.quality_histogram, y_label="碱基个数", x_label="Phred"
            ),
        ),
    ]

    if kmer_limit > 0:
        sections.append(
            section(f"5-mer 频次（前 {kmer_limit} 条）", _kmer_table(summary, kmer_limit))
        )

    sections.extend(section(name, html) for name, html in extra_sections)

    stamp = (
        f'<p class="meta">{escape(f"生成时间：{generated_at}")}</p>'
        if generated_at
        else '<p class="meta">本报告由 Project IDEA 生物方法模块生成；'
        "内容不含时间戳，同一份数据每次渲染的结果完全相同。</p>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{escape(title)}</h1>\n{stamp}\n"
        f'<div class="cards">{"".join(cards)}</div>\n'
        f'{"".join(sections)}\n'
        "</body>\n</html>\n"
    )


def _kmer_table(summary: ReadStatsSummary, limit: int) -> str:
    """计数最高的若干个 5-mer。"""
    counts = summary.kmer_counts
    if not counts:
        return '<p class="empty">没有 k-mer 数据。</p>'
    ranked = sorted(
        range(len(counts)), key=lambda index: (-counts[index], index)
    )
    rows = []
    for index in ranked:
        if counts[index] <= 0 or len(rows) >= limit:
            break
        share = counts[index] / sum(counts) if sum(counts) else 0.0
        rows.append(
            f'<tr><td><code>{escape(kmer_name(index))}</code></td>'
            f"<td>{counts[index]:,}</td><td>{share:.4%}</td></tr>"
        )
    if not rows:
        return '<p class="empty">没有出现任何 5-mer（数据里可能全是 N 或长度不足 5）。</p>'
    return (
        '<table><thead><tr><th>5-mer</th><th>出现次数</th><th>占比</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
