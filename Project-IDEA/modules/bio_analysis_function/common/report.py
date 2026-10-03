"""可视化数据接口的公共实现。

只负责把"一组二维坐标 + 标签 + 可选分组"整理成可直接序列化的纯数据，
**不引入绘图库、不做渲染，也不改动任何统计结果**。图形渲染由上层负责。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def _resolve_labels(
    n_samples: int,
    labels: Sequence[str] | None,
    what: str,
) -> list[str]:
    if labels is None:
        return [f"sample_{index + 1}" for index in range(n_samples)]
    if len(labels) != n_samples:
        raise ValueError(f"{what}数量必须与样本数一致。")
    return list(labels)


def build_scatter_plot(
    coordinates: np.ndarray,
    *,
    explained_variance_ratio: np.ndarray,
    axis_prefix: str,
    components: tuple[int, int] = (0, 1),
    sample_ids: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
) -> dict:
    """构造二维散点图数据。

    参数：
        coordinates: 形状为 ``(样本数, 坐标列数)`` 的坐标矩阵。
        explained_variance_ratio: 与坐标列对应的解释方差比例。
        axis_prefix: 轴名前缀，例如 ``"PC"``（PCA）或 ``"PCoA"``。
        components: 用作横纵轴的两列索引（0 基），默认取前两列。
        sample_ids: 样本标签；默认生成 ``sample_1`` 到 ``sample_n``。
        groups: 分组标签，只用于绘图着色。

    返回：
        含轴索引、轴标签、两轴解释方差比例和坐标点的字典。
    """
    data = np.asarray(coordinates, dtype=np.float64)
    if data.ndim != 2:
        raise ValueError("坐标矩阵必须是二维。")
    n_samples, n_columns = data.shape

    for index in components:
        if not isinstance(index, (int, np.integer)) or not 0 <= index < n_columns:
            raise ValueError(
                f"坐标列索引必须在 0 到 {n_columns - 1} 之间，当前为 {index}。"
            )

    ratios = np.asarray(explained_variance_ratio, dtype=np.float64)
    if ratios.shape[0] < n_columns:
        raise ValueError("解释方差比例的数量不能少于坐标列数。")

    labels = _resolve_labels(n_samples, sample_ids, "样本标签")
    if groups is not None and len(groups) != n_samples:
        raise ValueError("分组标签数量必须与样本数一致。")

    x_index, y_index = int(components[0]), int(components[1])
    points = [
        {
            "sample_id": labels[row],
            "x": float(data[row, x_index]),
            "y": float(data[row, y_index]),
            "group": None if groups is None else groups[row],
        }
        for row in range(n_samples)
    ]

    return {
        "components": [x_index + 1, y_index + 1],
        "axis_labels": [f"{axis_prefix}{index + 1}" for index in components],
        "explained_variance_ratio": [float(ratios[x_index]), float(ratios[y_index])],
        "points": points,
    }
