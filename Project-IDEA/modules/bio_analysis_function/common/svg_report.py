"""svg_report.py：自包含 SVG 报告的**绘图原语**。

read_stats 的报告层最早自带一整套"数据集 → SVG 片段"的画法（样式、画布尺寸、
坐标轴与网格、折线、柱状、图例、卡片、整节）。工作流的汇总报告要用同一套画法，
按 `开发规则.md` 3.2 的"公共能力至少出现两个真实使用方之后再提取"，在第二个
使用方（`modules/workflow/report.py`）落地时把**与算法语义无关**的那部分上移到这里，
两条报告从此共用同一份样式与绘图口径，版式不会再各自漂移。

本层只负责**画**：给数据、给轴标题，返回 SVG 片段或页面的骨架片段。"这些数据
从哪来、怎么解释"仍留在各自的报告层——所以连配色都由调用方传进来（read_stats
的质量曲线配色与工作流的过滤前后对比配色本来就是两回事）。所有产出都是静态
字符串：不执行任何脚本、不引用任何外部资源。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from html import escape
from typing import Final

__all__ = [
    "CHART_HEIGHT",
    "CHART_WIDTH",
    "PLOT_BOTTOM",
    "PLOT_LEFT",
    "PLOT_RIGHT",
    "PLOT_TOP",
    "STYLE",
    "axis_lines",
    "bar_chart",
    "card",
    "legend",
    "line_chart",
    "plot_area",
    "section",
]

#: SVG 画布尺寸。比例接近 A4 正文宽度，缩放到页面里不会变形。
CHART_WIDTH: Final = 900
CHART_HEIGHT: Final = 260
PLOT_LEFT: Final = 56
PLOT_RIGHT: Final = 12
#: 顶部留出一整行给图例与轴标题——**图例必须画在绘图区之外**。
#: 画在里面会和曲线的高质量平台叠在一起（实测质量图有上百个点落进图例框）。
PLOT_TOP: Final = 28
PLOT_BOTTOM: Final = 30

STYLE: Final = """
:root { color-scheme: light; }
body { margin: 0; padding: 32px 40px 56px; background: #fafafa; color: #212121;
       font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; padding-bottom: 6px;
     border-bottom: 1px solid #e0e0e0; }
.meta { color: #757575; margin: 0 0 24px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
.card { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
        padding: 10px 16px; min-width: 132px; }
.card .label { color: #757575; font-size: 12px; }
.card .value { font-size: 19px; font-weight: 600; }
.chart { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
         padding: 8px; display: block; max-width: 100%; height: auto; }
table { border-collapse: collapse; background: #fff; font-size: 13px; }
th, td { border: 1px solid #e0e0e0; padding: 5px 12px; text-align: left; }
th { background: #f5f5f5; }
code { font-family: Consolas, "Courier New", monospace; }
.note { color: #757575; font-size: 12px; margin: 6px 0 0; }
.empty { color: #757575; background: #fff; border: 1px dashed #e0e0e0;
         border-radius: 6px; padding: 16px; }
"""


# ---------------------------------------------------------------------------
# 绘图：把数据画成 SVG 元素（不依赖任何脚本）
# ---------------------------------------------------------------------------


def plot_area() -> tuple[float, float, float, float]:
    """返回可绘图区域的 (左边, 上边, 宽, 高)。"""
    width = CHART_WIDTH - PLOT_LEFT - PLOT_RIGHT
    height = CHART_HEIGHT - PLOT_TOP - PLOT_BOTTOM
    return PLOT_LEFT, PLOT_TOP, width, height


def axis_lines(y_max: float, y_label: str, x_labels: Sequence[str]) -> str:
    """坐标轴、网格线与刻度文字。"""
    left, top, width, height = plot_area()
    parts = [
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + height}" '
        f'stroke="#bdbdbd"/>',
        f'<line x1="{left}" y1="{top + height}" x2="{left + width}" '
        f'y2="{top + height}" stroke="#bdbdbd"/>',
        # 轴标题放在绘图区**上方**的左侧：放在绘图区里会和顶部的刻度数字叠住。
        f'<text x="{left}" y="{top - 6}" font-size="11" fill="#757575">'
        f"{escape(y_label)}</text>",
    ]
    # 横向网格：4 等分，标出对应的数值。
    for step in range(5):
        value = y_max * step / 4
        y = top + height - height * step / 4
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + width}" y2="{y:.1f}" '
            f'stroke="#eeeeee"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" font-size="11" fill="#757575" '
            f'text-anchor="end">{value:.4g}</text>'
        )
    # 纵向刻度只标首尾，避免密到看不清。
    if x_labels:
        for ratio, label in ((0.0, x_labels[0]), (1.0, x_labels[-1])):
            x = left + width * ratio
            parts.append(
                f'<text x="{x:.1f}" y="{top + height + 16}" font-size="11" '
                f'fill="#757575" text-anchor="{"start" if ratio == 0 else "end"}">'
                f"{escape(label)}</text>"
            )
    return "".join(parts)


def line_chart(
    series: dict[str, Sequence[float]],
    *,
    y_label: str,
    colors: Mapping[str, str],
    y_max: float | None = None,
    x_labels: Sequence[str] = (),
) -> str:
    """把若干条等长序列画成折线图。

    ``colors`` 由调用方给出：配色属于各自的报告语义，本层只按名字取色，
    取不到时用黑色兜底。
    """
    length = max((len(values) for values in series.values()), default=0)
    if length == 0:
        return f'<p class="empty">{escape("没有数据，无法作图。")}</p>'

    if y_max is None or y_max <= 0:
        y_max = max((max(values) for values in series.values() if values), default=1.0)
        # 留一点顶部余量，免得峰顶贴着边框。
        y_max = y_max * 1.05 if y_max > 0 else 1.0

    left, top, width, height = plot_area()
    parts = [axis_lines(y_max, y_label, x_labels)]
    for name, values in series.items():
        if not values:
            continue
        color = colors.get(name, "#000000")
        points = []
        for index, value in enumerate(values):
            x = left + (width * index / max(length - 1, 1))
            y = top + height - height * min(max(value, 0.0) / y_max, 1.0)
            points.append(f"{x:.1f},{y:.1f}")
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.4" '
            f'points="{" ".join(points)}"/>'
        )
    # 图例画在 SVG 最上面一行的右侧（绘图区之上），轴标题在同一行的左侧。
    parts.append(legend(series.keys(), left + width, 6, colors))
    return (
        f'<svg class="chart" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'role="img" aria-label="{escape(y_label)}">{"".join(parts)}</svg>'
    )


def bar_chart(
    counts: dict[int, int],
    *,
    y_label: str,
    x_label: str,
    limit: int = 120,
) -> str:
    """把"值 → 个数"画成柱状图。柱子太多时只画出现次数最多的前 ``limit`` 个值。

    读长分布与质量分布都属于"取值可能很散"的数据，全画出来会糊成一片，
    所以按取值排序取前若干个，并在图下注明是否截断。
    """
    if not counts:
        return f'<p class="empty">{escape("没有数据，无法作图。")}</p>'

    items = sorted(counts.items())
    truncated = False
    if len(items) > limit:
        items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        items.sort()
        truncated = True

    y_max = max(count for _, count in items) or 1
    left, top, width, height = plot_area()
    bar_width = max(width / len(items) * 0.72, 0.6)
    parts = [axis_lines(float(y_max), y_label, [str(items[0][0]), str(items[-1][0])])]
    for index, (value, count) in enumerate(items):
        x = left + width * (index + 0.5) / len(items)
        bar_height = height * count / y_max
        # ``<title>`` 必须是图形元素的子元素才有效，所以写在 ``<rect>`` 里面。
        parts.append(
            f'<rect x="{x - bar_width / 2:.1f}" y="{top + height - bar_height:.1f}" '
            f'width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#42a5f5">'
            f"<title>{escape(f'{x_label} {value}：{count}')}</title></rect>"
        )
    svg = (
        f'<svg class="chart" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" '
        f'role="img" aria-label="{escape(y_label)}">{"".join(parts)}</svg>'
    )
    if truncated:
        svg += (
            f'<p class="note">{escape(f"取值共 {len(counts)} 种，图中只画了出现最多的 {limit} 种。")}</p>'
        )
    return svg


def legend(
    names: Iterable[str], right: float, top: float, colors: Mapping[str, str]
) -> str:
    """右上角的图例。"""
    parts = []
    x = right
    for name in names:
        color = colors.get(name, "#000000")
        parts.append(
            f'<rect x="{x - 46:.1f}" y="{top + 2}" width="9" height="9" fill="{color}"/>'
            f'<text x="{x - 33:.1f}" y="{top + 10}" font-size="11" fill="#424242">'
            f"{escape(name)}</text>"
        )
        x -= 52
    return "".join(parts)


# ---------------------------------------------------------------------------
# 报告骨架片段
# ---------------------------------------------------------------------------


def card(label: str, value: str) -> str:
    return (
        f'<div class="card"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div></div>'
    )


def section(name: str, body: str) -> str:
    return f"<section>\n<h2>{escape(name)}</h2>\n{body}\n</section>\n"
