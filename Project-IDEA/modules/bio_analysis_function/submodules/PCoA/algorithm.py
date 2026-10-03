"""PCoA（主坐标分析）的核心实现，又称经典多维标度（Classical MDS）。

与 PCA 的区别
--------------
PCA 输入的是**特征矩阵** $X$（行=样本、列=特征），对中心化后的矩阵做 SVD。
PCoA 输入的是**距离矩阵** $D$（$n \\times n$），先把距离平方，再做 Gower 双中心化：

$$
B = -\\tfrac{1}{2}\\, J D^{(2)} J,
\\qquad J = I - \\tfrac{1}{n}\\mathbf{1}\\mathbf{1}^{\\mathsf{T}}
$$

然后对 $B$ 做特征分解。$B$ 就是中心化坐标的内积矩阵（Gram 矩阵），
因此当 $D$ 是欧氏距离时，PCoA 与"对中心化数据做 PCA"给出同一组坐标。

非欧距离的后果
--------------
若 $D$ 不是欧氏距离，$B$ 会出现**负特征值**。此时：

- 主坐标只能取正特征值（负特征值开不出实坐标）；
- 负特征值本身是有用的诊断证据，说明这批距离无法用欧氏空间精确表示。

因此 PCoA 的解释方差比例以 $\\sum |\\lambda|$ 为分母（而不是 $\\sum \\lambda$），
这样在存在负特征值时分母仍然有意义。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# 判定对称性与零对角线的绝对容差。
_SYMMETRY_ATOL = 1e-8
# 判定特征值符号的容差，按最大特征值缩放，避免把数值噪声当成真实的反号。
_EIGEN_TOL_RATIO = 1e-10


@dataclass(frozen=True, slots=True)
class PCoAResult:
    """PCoA 的机器可读结果。

    ``coordinates`` 的形状为 ``(样本数, 主坐标数)``，``eigenvalues`` 为对应的
    正特征值。``negative_eigenvalues`` 记录双中心化矩阵的负特征值（按数值升序，
    最负的在前），欧氏距离下为空数组。
    """

    coordinates: np.ndarray
    eigenvalues: np.ndarray
    explained_variance_ratio: np.ndarray
    cumulative_explained_variance_ratio: np.ndarray
    negative_eigenvalues: np.ndarray
    n_samples: int
    is_euclidean: bool


def fit_pcoa(
    distance: np.ndarray,
    *,
    n_components: int | None = None,
) -> PCoAResult:
    """对距离矩阵做 PCoA，返回主坐标、特征值和解释方差。

    参数：
        distance: ``(n, n)`` 的对称距离矩阵，对角线必须为 0、元素非负。
        n_components: 保留的主坐标数量；默认保留全部正特征值对应的坐标。

    返回：
        :class:`PCoAResult`，所有数组均为新的浮点数组。

    异常：
        ``ValueError``：输入不是方阵、不对称、对角线非 0、含负值或非有限值，
        参数不合法，或距离矩阵没有正特征值。
    """
    data = np.asarray(distance, dtype=np.float64)
    if data.ndim != 2:
        raise ValueError("PCoA 输入必须是二维矩阵。")
    if data.shape[0] != data.shape[1]:
        raise ValueError(f"距离矩阵必须是方阵，当前形状为 {data.shape}。")

    n_samples = data.shape[0]
    if n_samples < 2:
        raise ValueError("PCoA 至少需要 2 个样本。")
    if not np.isfinite(data).all():
        raise ValueError("距离矩阵不能包含 NaN 或无穷值；请先处理缺失值。")
    if np.any(data < 0):
        raise ValueError("距离矩阵不能包含负值。")
    if not np.allclose(data, data.T, atol=_SYMMETRY_ATOL):
        raise ValueError("距离矩阵必须对称。")
    if not np.allclose(np.diag(data), 0.0, atol=_SYMMETRY_ATOL):
        raise ValueError("距离矩阵的对角线必须全为 0。")

    # Gower 双中心化：B = -1/2 * J D^2 J。
    # 这里使用逐元素等价式，避免显式构造 n×n 的中心化矩阵 J。
    # row_mean[i] 是第 i 行平方距离的均值；对称距离矩阵下，列均值相同，
    # 因而 row_mean.T 同时承担列均值项。
    squared = data**2
    row_mean = squared.mean(axis=1, keepdims=True)
    grand_mean = squared.mean()
    centered = -0.5 * (squared - row_mean - row_mean.T + grand_mean)

    # eigh 面向对称矩阵，返回升序特征值，比 eig 稳定。
    eigenvalues, eigenvectors = np.linalg.eigh(centered)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]

    scale = max(float(abs(eigenvalues[0])), 1.0)
    tolerance = _EIGEN_TOL_RATIO * scale

    positive_mask = eigenvalues > tolerance
    available_components = int(positive_mask.sum())
    if available_components == 0:
        raise ValueError(
            "距离矩阵的双中心化结果没有正特征值，无法给出主坐标；"
            "请检查是否所有样本间距离都为 0。"
        )

    if n_components is None:
        component_count = available_components
    elif not isinstance(n_components, (int, np.integer)):
        raise ValueError("n_components 必须是整数或 None。")
    elif not 1 <= n_components <= available_components:
        raise ValueError(f"n_components 必须在 1 到 {available_components} 之间。")
    else:
        component_count = int(n_components)

    used_values = eigenvalues[:component_count]
    # 主坐标 = 特征向量 × sqrt(特征值)，广播按列缩放。
    coordinates = eigenvectors[:, :component_count] * np.sqrt(used_values)

    # 分母用全部特征值的绝对值之和：非欧距离下存在负特征值时，
    # 直接用 Σλ 会让分母偏小甚至接近 0。
    total = float(np.abs(eigenvalues).sum())
    if total == 0:
        explained_ratio = np.zeros(component_count, dtype=np.float64)
    else:
        explained_ratio = used_values / total
    cumulative_ratio = np.cumsum(explained_ratio)

    # 负特征值按数值升序排列，最负的在前，便于诊断。
    negative = np.sort(eigenvalues[eigenvalues < -tolerance])

    return PCoAResult(
        coordinates=np.array(coordinates, copy=True),
        eigenvalues=np.array(used_values, copy=True),
        explained_variance_ratio=np.array(explained_ratio, copy=True),
        cumulative_explained_variance_ratio=np.array(cumulative_ratio, copy=True),
        negative_eigenvalues=np.array(negative, copy=True),
        n_samples=n_samples,
        is_euclidean=bool(negative.size == 0),
    )
