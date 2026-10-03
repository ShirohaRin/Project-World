"""PCA 的数据输入：数值特征矩阵。

这里只保留 PCA 这一族算法的语义约定——**数值特征矩阵、行=样本、列=特征**。
文件读取原语（编码、分隔符、空行、标签唯一性、报错定位）由模块公共层提供，
见 :mod:`modules.bio_analysis_function.common.io`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common.io import load_labeled_table, to_float_matrix


@dataclass(frozen=True, slots=True)
class MatrixTable:
    """一张数值特征矩阵及其行列标签。

    ``values`` 的形状是 ``(样本数, 特征数)``，第 i 行对应 ``sample_ids[i]``，
    第 j 列对应 ``feature_names[j]``。
    """

    values: np.ndarray
    sample_ids: tuple[str, ...]
    feature_names: tuple[str, ...]


def load_feature_matrix(
    path: str | Path,
    *,
    delimiter: str | None = None,
    has_header: bool = True,
    has_index: bool = True,
) -> MatrixTable:
    """读取数值特征矩阵文件。

    参数：
        path: CSV/TSV 文件路径。
        delimiter: 单字符分隔符；默认按扩展名推断（``.csv`` 用逗号，
            ``.tsv``/``.tab``/``.txt`` 用制表符）。
        has_header: 首行是否为特征名。
        has_index: 首列是否为样本 ID；为 ``False`` 时自动生成
            ``sample_1`` 到 ``sample_n``。

    返回：
        :class:`MatrixTable`，``values`` 为 float64 的二维数组。

    异常：
        ``ValueError``：文件不存在或为空、行列数不一致、标签重复、
        存在空单元格，或单元格不是合法数值。
    """
    table = load_labeled_table(
        path,
        delimiter=delimiter,
        has_header=has_header,
        has_index=has_index,
        row_label_prefix="sample",
        column_label_prefix="feature",
    )
    return MatrixTable(
        values=to_float_matrix(table),
        sample_ids=table.row_labels,
        feature_names=table.column_labels,
    )
