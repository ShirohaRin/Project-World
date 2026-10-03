"""PCA 结果的可视化数据接口。

散点数据的通用整理逻辑在公共层，本模块只负责绑定 PCA 的字段与轴名。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from ...common.report import build_scatter_plot
from .algorithm import PCAResult


def _resolve_feature_names(
    result: PCAResult,
    feature_names: Sequence[str] | None,
) -> list[str]:
    if feature_names is None:
        return [f"feature_{index + 1}" for index in range(result.n_features)]
    if len(feature_names) != result.n_features:
        raise ValueError("特征名数量必须与特征数一致。")
    return list(feature_names)


def build_score_plot(
    result: PCAResult,
    *,
    components: tuple[int, int] = (0, 1),
    sample_ids: Sequence[str] | None = None,
    groups: Sequence[str] | None = None,
) -> dict:
    """构造 PC 得分散点数据。

    参数：
        result: :func:`fit_pca` 的返回值。
        components: 用作横纵轴的两个主成分的 0 基索引，默认 ``(0, 1)``
            即 PC1 与 PC2。
        sample_ids: 样本标签；默认生成 ``sample_1`` 到 ``sample_n``。
        groups: 分组标签，只用于绘图着色，长度必须与样本数一致。

    返回：
        字典，含轴索引、轴标签、两轴解释方差比例和每个样本的坐标点。
    """
    return build_scatter_plot(
        result.scores,
        explained_variance_ratio=result.explained_variance_ratio,
        axis_prefix="PC",
        components=components,
        sample_ids=sample_ids,
        groups=groups,
    )


def rank_loadings(
    result: PCAResult,
    *,
    component: int = 0,
    feature_names: Sequence[str] | None = None,
    top_n: int | None = None,
) -> list[dict]:
    """按载荷绝对值降序排列特征。

    载荷符号本身在主方向整体翻转时会一起变号，因此排序按绝对值进行；
    这与 :func:`fit_pca` 中"符号可整体翻转"的约定一致。
    """
    n_components = result.loadings.shape[1]
    if not isinstance(component, (int, np.integer)) or not 0 <= component < n_components:
        raise ValueError(
            f"主成分索引必须在 0 到 {n_components - 1} 之间，当前为 {component}。"
        )
    if top_n is not None and (not isinstance(top_n, int) or top_n < 1):
        raise ValueError("top_n 必须为正整数或 None。")

    names = _resolve_feature_names(result, feature_names)
    column = result.loadings[:, component]
    order = np.argsort(-np.abs(column), kind="stable")
    if top_n is not None:
        order = order[:top_n]

    return [
        {
            "rank": rank + 1,
            "feature": names[index],
            "loading": float(column[index]),
            "abs_loading": float(abs(column[index])),
        }
        for rank, index in enumerate(order)
    ]
