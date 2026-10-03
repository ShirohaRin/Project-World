"""通用数值矩阵 PCA 的核心实现。

实现约定：
- 输入矩阵的每一行是一个样本，每一列是一个特征；
- 默认只做中心化，不默认按特征标准差缩放；
- 使用 SVD 而不是显式构造协方差矩阵，减少数值稳定性问题；
- 缺失值和常数特征不会被静默修复，调用方必须先处理它们。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


Transform = Literal["none", "log1p", "center", "zscore"]


@dataclass(frozen=True, slots=True)
class PCAResult:
    """PCA 的机器可读结果。

    scores 的形状为 (样本数, 主成分数)，loadings 的形状为
    (特征数, 主成分数)。二者都按主成分列排列。
    """

    scores: np.ndarray
    loadings: np.ndarray
    eigenvalues: np.ndarray
    explained_variance_ratio: np.ndarray
    cumulative_explained_variance_ratio: np.ndarray
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    n_samples: int
    n_features: int
    transform: Transform


def fit_pca(matrix: np.ndarray,*,n_components: int | None = None,transform: Transform = "center",) -> PCAResult:
    """拟合 PCA 并返回样本得分、特征载荷和解释方差。

    PCA 的基本对象是一个矩阵 ``X``。本函数先按 ``transform`` 约定得到
    工作矩阵 ``Z``，再对 ``Z`` 做奇异值分解 ``Z = U S V^T``：

    - ``scores = U S``：每个样本在主成分坐标系中的坐标；
    - ``loadings = V``：每个特征在主成分方向上的权重；
    - ``eigenvalues = S^2 / (n - 1)``：每个主成分解释的样本方差。

    参数：
        matrix: 二维数值矩阵，行表示样本，列表示特征。当前接口只接受
            已加载到内存中的矩阵，不负责 CSV/TSV 解析。
        n_components: 保留的主成分数量；默认保留全部可用主成分。
        transform: ``none``、``log1p``、``center`` 或 ``zscore``。

    返回：
        :class:`PCAResult`，所有数组均为新的浮点数组。

    异常：
        ``ValueError``：输入不是合法的有限二维矩阵、参数不合法、
        log1p 输入含负数，或 z-score 遇到常数特征。
    """
    data = np.asarray(matrix, dtype=np.float64)
    if data.ndim != 2:    #验证数据维度是否为2，即矩阵格式
        raise ValueError("PCA 输入必须是二维矩阵。")
    if data.shape[0] < 2 or data.shape[1] < 1:  #验证行列数
        raise ValueError("PCA 至少需要 2 个样本和 1 个特征。")
    if not np.isfinite(data).all():
        raise ValueError("PCA 输入不能包含 NaN 或无穷值；请先处理缺失值。")
    if transform not in {"none", "log1p", "center", "zscore"}:
        raise ValueError(f"不支持的变换方式：{transform}")

    if transform == "log1p":
        if np.any(data < 0):
            raise ValueError("log1p 变换要求所有输入值都不小于 0。")
        data = np.log1p(data)

    feature_mean = data.mean(axis=0)
    centered = data - feature_mean
    feature_scale = np.ones(data.shape[1], dtype=np.float64)

    if transform == "zscore":
        # 口径约定：这里使用总体标准差 ddof=0，与 scikit-learn
        # StandardScaler 的做法一致，也与下面用 n-1 计算特征值的口径不同。
        # 后果是逐列标准化后，每个特征的样本方差（n-1 口径）等于 n/(n-1)
        # 而不是精确的 1，因此 z-score 后的特征值之和略大于特征个数。
        # R 的 prcomp(scale = TRUE) 使用 n-1 口径，跨工具比对时需注意该差异；
        # 样本量较大时两者差异可忽略，解释方差比例不受影响，因为分子分母同口径。
        feature_scale = centered.std(axis=0, ddof=0)
        if np.any(feature_scale == 0):
            raise ValueError("z-score 不能处理常数特征；请先删除常数特征。")
        centered = centered / feature_scale
    elif transform == "none":
        # none 保留原始矩阵：PCA 不进行中心化。
        centered = data
        feature_mean = np.zeros(data.shape[1], dtype=np.float64)

    # X = U S V^T。V 的列是特征空间中的主方向，U S 是样本得分。
    u, singular_values, vt = np.linalg.svd(centered, full_matrices=False)
    available_components = singular_values.shape[0]
    if n_components is None:
        component_count = available_components
    elif not isinstance(n_components, (int, np.integer)):
        raise ValueError("n_components 必须是整数或 None。")
    elif not 1 <= n_components <= available_components:
        raise ValueError(
            f"n_components 必须在 1 到 {available_components} 之间。"
        )
    else:
        component_count = int(n_components)

    total_variance = np.sum(singular_values**2) / (data.shape[0] - 1)
    singular_values = singular_values[:component_count]
    scores = u[:, :component_count] * singular_values
    loadings = vt[:component_count].T

    # 每个奇异值平方除以 n-1，就是对应主成分的样本方差。
    eigenvalues = singular_values**2 / (data.shape[0] - 1)
    if total_variance == 0:
        explained_ratio = np.zeros(component_count, dtype=np.float64)
    else:
        explained_ratio = eigenvalues / total_variance
    cumulative_ratio = np.cumsum(explained_ratio)

    return PCAResult(
        scores=np.array(scores, copy=True),
        loadings=np.array(loadings, copy=True),
        eigenvalues=np.array(eigenvalues, copy=True),
        explained_variance_ratio=np.array(explained_ratio, copy=True),
        cumulative_explained_variance_ratio=np.array(cumulative_ratio, copy=True),
        feature_mean=np.array(feature_mean, copy=True),
        feature_scale=np.array(feature_scale, copy=True),
        n_samples=data.shape[0],
        n_features=data.shape[1],
        transform=transform,
    )
