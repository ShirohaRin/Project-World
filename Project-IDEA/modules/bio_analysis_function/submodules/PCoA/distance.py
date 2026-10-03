"""距离度量：把特征矩阵转成 PCoA 需要的距离矩阵。

本模块只提供两种最基础、最常用的度量：

- **欧氏距离**：连续型特征的默认选择，结果一定是欧氏距离，
  因此 PCoA 不会出现负特征值；
- **Bray–Curtis 距离**：丰度/计数型数据的常用选择（微生物组等），
  **一般不是欧氏距离**，因此 PCoA 可能出现负特征值。

两者都只依赖 NumPy，不重复实现已有能力。
"""

from __future__ import annotations

import numpy as np


def _as_matrix(matrix: np.ndarray, name: str) -> np.ndarray:
    data = np.asarray(matrix, dtype=np.float64)
    if data.ndim != 2:
        raise ValueError(f"{name} 输入必须是二维矩阵。")
    if data.shape[0] < 2:
        raise ValueError(f"{name} 至少需要 2 个样本。")
    if not np.isfinite(data).all():
        raise ValueError(f"{name} 输入不能包含 NaN 或无穷值；请先处理缺失值。")
    return data


def pairwise_euclidean(matrix: np.ndarray) -> np.ndarray:
    """计算样本间的欧氏距离矩阵。

    用 Gram 矩阵展开：

    $$
    d_{ij}^2 = \\lVert x_i \\rVert^2 + \\lVert x_j \\rVert^2 - 2\\,x_i \\cdot x_j
    $$

    这样只占 $O(n^2)$ 内存，不必构造 $(n, n, p)$ 的中间张量。
    """
    data = _as_matrix(matrix, "欧氏距离")
    gram = data @ data.T
    squared_norm = np.diag(gram)
    squared = squared_norm[:, None] + squared_norm[None, :] - 2.0 * gram
    # 浮点误差可能让本该为 0 的对角线变成极小负数。
    np.maximum(squared, 0.0, out=squared)
    distance = np.sqrt(squared)
    np.fill_diagonal(distance, 0.0)
    return distance


def pairwise_bray_curtis(matrix: np.ndarray) -> np.ndarray:
    """计算样本间的 Bray–Curtis 距离矩阵。

    $$
    d_{ij} = \\frac{\\sum_k |x_{ik} - x_{jk}|}{\\sum_k (x_{ik} + x_{jk})}
    $$

    要求输入非负（丰度/计数的前提）。两个全零样本之间的距离定义为 0。

    实现上逐特征累加，只占 $O(n^2)$ 内存；若一次性构造 $(n, n, p)$
    的中间张量，特征数上万时会直接吃光内存。
    """
    data = _as_matrix(matrix, "Bray–Curtis 距离")
    if np.any(data < 0):
        raise ValueError("Bray–Curtis 距离要求输入非负（丰度或计数）。")

    n_samples = data.shape[0]
    numerator = np.zeros((n_samples, n_samples), dtype=np.float64)
    for column_index in range(data.shape[1]):
        column = data[:, column_index]
        numerator += np.abs(column[:, None] - column[None, :])

    row_sums = data.sum(axis=1)
    denominator = row_sums[:, None] + row_sums[None, :]

    distance = np.zeros((n_samples, n_samples), dtype=np.float64)
    # 分母为 0 表示两个样本都是全零向量，距离按 0 处理。
    np.divide(numerator, denominator, out=distance, where=denominator > 0)
    np.fill_diagonal(distance, 0.0)
    return distance
