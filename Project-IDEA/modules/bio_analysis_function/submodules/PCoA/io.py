"""PCoA 的数据输入：距离矩阵。

这里只保留 PCoA 这一族算法的语义约定——**方阵、行列标签一致、数值**。
对称性、零对角线和非负性由 :func:`fit_pcoa` 在计算前校验，
因为那是"能不能算"的问题，而不是"文件格式对不对"的问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common.io import load_labeled_table, to_float_matrix


@dataclass(frozen=True, slots=True)
class DistanceMatrix:
    """一张样本间距离矩阵及其样本标签。

    ``values`` 的形状是 ``(样本数, 样本数)``，行与列对应同一批样本，
    顺序由 ``sample_ids`` 给出。
    """

    values: np.ndarray
    sample_ids: tuple[str, ...]


def load_distance_matrix(
    path: str | Path,
    *,
    delimiter: str | None = None,
) -> DistanceMatrix:
    """读取距离矩阵文件。

    距离矩阵必须**同时带表头和行标签**：缺少任一边都无法判断行列是否对应
    同一批样本，而错位在这里不会报错、只会安静地给出错误结果。

    参数：
        path: CSV/TSV 文件路径。
        delimiter: 单字符分隔符；默认按扩展名推断。

    返回：
        :class:`DistanceMatrix`，``values`` 为 float64 的方阵。

    异常：
        ``ValueError``：文件不存在或为空、不是方阵、行列标签不一致、
        标签重复、存在空单元格，或单元格不是合法数值。
    """
    table = load_labeled_table(
        path,
        delimiter=delimiter,
        has_header=True,
        has_index=True,
        row_label_prefix="sample",
        column_label_prefix="sample",
    )
    values = to_float_matrix(table)

    if values.shape[0] != values.shape[1]:
        raise ValueError(
            f"距离矩阵必须是方阵，当前为 {values.shape[0]} 行 {values.shape[1]} 列。"
        )
    if table.row_labels != table.column_labels:
        raise ValueError(
            "距离矩阵的行标签与列标签必须完全一致；"
            "请检查表头列顺序是否与数据行顺序对应。"
        )

    return DistanceMatrix(values=values, sample_ids=table.row_labels)
