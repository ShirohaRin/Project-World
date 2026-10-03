"""PCoA 结果的可视化数据接口。

散点数据的通用整理逻辑在公共层，本模块只负责绑定 PCoA 的字段与轴名。
"""

from __future__ import annotations

from typing import Sequence

from ...common.report import build_scatter_plot
from .algorithm import PCoAResult


def build_coordinate_plot(
    result: PCoAResult,
    *,
    components: tuple[int, int] = (0, 1),
    sample_ids: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
) -> dict:
    """构造主坐标散点数据。

    参数：
        result: :func:`fit_pcoa` 的返回值。
        components: 用作横纵轴的两个主坐标的 0 基索引，默认 ``(0, 1)``。
        sample_ids: 样本标签；默认生成 ``sample_1`` 到 ``sample_n``。
        groups: 分组标签，只用于绘图着色，长度必须与样本数一致。

    返回：
        字典，含轴索引、轴标签、两轴解释方差比例和每个样本的坐标点。
    """
    return build_scatter_plot(
        result.coordinates,
        explained_variance_ratio=result.explained_variance_ratio,
        axis_prefix="PCoA",
        components=components,
        sample_ids=sample_ids,
        groups=groups,
    )
