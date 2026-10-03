"""生物方法模块的公共数据输入层。

这一层只负责"把磁盘上的分隔文本读成带标签的表格"，**不包含任何算法语义**：
不规定行是样本还是特征、不规定是否必须是数值、不规定矩阵形状。

各子模块在这一层之上再做自己的形状与语义校验，例如：

- PCA 要求数值特征矩阵（行=样本、列=特征）
- PCoA 要求距离矩阵（方阵、对称、对角为 0、非负）

之所以把这些原语抽出来，是因为"文件编码、分隔符、空行、标签唯一性、报错定位"
这些事情与算法无关——在 417 个工具里各写一遍只会产生 417 份略有差异的实现。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


_CSV_SUFFIXES = {".csv"}
_TSV_SUFFIXES = {".tsv", ".tab", ".txt"}


@dataclass(frozen=True, slots=True)
class LabeledTable:
    """带行列标签的原始字符串表格。

    这是所有算法输入层的共同起点：先统一读进来，再由各子模块把它解释成
    自己需要的形状。``cells`` 保持字符串原样，不做任何类型推断。
    """

    row_labels: tuple[str, ...]
    column_labels: tuple[str, ...]
    cells: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class MetadataTable:
    """样本元数据表：首列为样本 ID，其余列原样保存为字符串。

    元数据只用于结果关联与绘图分组着色，不参与任何算法的拟合过程。
    """

    sample_ids: tuple[str, ...]
    columns: tuple[str, ...]
    rows: dict[str, dict[str, str]]


def resolve_delimiter(path: Path, delimiter: str | None) -> str:
    """确定分隔符：显式传入优先，否则按扩展名推断。"""
    if delimiter is not None:
        if len(delimiter) != 1:
            raise ValueError("delimiter 必须是单个字符。")
        return delimiter
    suffix = path.suffix.lower()
    if suffix in _CSV_SUFFIXES:
        return ","
    if suffix in _TSV_SUFFIXES:
        return "\t"
    raise ValueError(f"无法从扩展名 {suffix!r} 推断分隔符，请显式传入 delimiter。")


def read_rows(path: Path, delimiter: str) -> list[list[str]]:
    """读取非空行；使用 utf-8-sig 兼容带 BOM 的导出文件。"""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            rows = [row for row in reader if any(cell.strip() for cell in row)]
    except FileNotFoundError as error:
        raise ValueError(f"文件不存在：{path}") from error
    if not rows:
        raise ValueError(f"文件为空：{path}")
    return rows


def require_unique(labels: Sequence[str], what: str) -> None:
    """标签必须唯一，否则后续按标签对齐样本会出错。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for label in labels:
        if label in seen and label not in duplicates:
            duplicates.append(label)
        seen.add(label)
    if duplicates:
        raise ValueError(f"{what} 重复：{'、'.join(duplicates)}。")


def load_labeled_table(
    path: str | Path,
    *,
    delimiter: str | None = None,
    has_header: bool = True,
    has_index: bool = True,
    row_label_prefix: str = "row",
    column_label_prefix: str = "column",
) -> LabeledTable:
    """把分隔文本读成带标签的字符串表格。

    参数：
        path: CSV/TSV 文件路径。
        delimiter: 单字符分隔符；默认按扩展名推断。
        has_header: 首行是否为列名。
        has_index: 首列是否为行标签；为 ``False`` 时自动生成
            ``{row_label_prefix}_1`` 到 ``{row_label_prefix}_n``。
        row_label_prefix / column_label_prefix: 自动生成标签时使用的前缀。
            由子模块传入各自的语义名称（如 ``sample`` / ``feature``）。

    返回：
        :class:`LabeledTable`，``cells`` 为纯字符串，不做类型转换。
    """
    file_path = Path(path)
    rows = read_rows(file_path, resolve_delimiter(file_path, delimiter))

    header = rows[0] if has_header else None
    body = rows[1:] if has_header else rows

    n_rows = len(body)
    if n_rows == 0:
        raise ValueError("文件中没有数据行。")

    offset = 1 if has_index else 0
    n_columns = len(body[0]) - offset
    if n_columns < 1:
        raise ValueError("文件至少需要一个数据列。")

    cells: list[tuple[str, ...]] = []
    row_labels: list[str] = []
    for row_index, row in enumerate(body):
        if len(row) != n_columns + offset:
            raise ValueError(
                f"第 {row_index + 1} 个数据行列数不一致："
                f"期望 {n_columns + offset}，实际 {len(row)}。"
            )
        if has_index:
            label = row[0].strip()
            if not label:
                raise ValueError(f"第 {row_index + 1} 个数据行缺少行标签。")
        else:
            label = f"{row_label_prefix}_{row_index + 1}"
        row_labels.append(label)
        cells.append(tuple(cell.strip() for cell in row[offset:]))

    if header is not None:
        header_cells = [cell.strip() for cell in header]
        if len(header_cells) == n_columns + offset:
            # 表头含角标列（pandas / R 导出的常见形式），首格对应行标签列。
            column_labels = header_cells[offset:]
        elif len(header_cells) == n_columns:
            # 表头只写列名，没有角标列。
            column_labels = header_cells
        else:
            raise ValueError(
                f"表头列数（{len(header_cells)}）与数据列数（{n_columns}）不一致。"
            )
        if any(label == "" for label in column_labels):
            raise ValueError("表头存在空列名。")
    else:
        column_labels = [
            f"{column_label_prefix}_{index + 1}" for index in range(n_columns)
        ]

    require_unique(row_labels, "行标签")
    require_unique(column_labels, "列名")

    return LabeledTable(
        row_labels=tuple(row_labels),
        column_labels=tuple(column_labels),
        cells=tuple(cells),
    )


def to_float_matrix(table: LabeledTable) -> np.ndarray:
    """把字符串表格转成 float64 数值矩阵。

    空单元格和非数值都会报错，并指出具体行列位置与标签。
    不隐式插补缺失值：缺失值会改变协方差等结构，必须由调用方显式处理。
    """
    values = np.empty((len(table.row_labels), len(table.column_labels)), dtype=np.float64)
    for row_index, row in enumerate(table.cells):
        for column_index, cell in enumerate(row):
            if cell == "":
                raise ValueError(
                    f"第 {row_index + 1} 行（{table.row_labels[row_index]}）、"
                    f"第 {column_index + 1} 列（{table.column_labels[column_index]}）"
                    "为空值；本模块不隐式插补缺失值，请先处理。"
                )
            try:
                values[row_index, column_index] = float(cell)
            except ValueError as error:
                raise ValueError(
                    f"第 {row_index + 1} 行（{table.row_labels[row_index]}）、"
                    f"第 {column_index + 1} 列（{table.column_labels[column_index]}）"
                    f"不是合法数值：{cell!r}。"
                ) from error
    return values


def load_metadata(
    path: str | Path,
    *,
    delimiter: str | None = None,
) -> MetadataTable:
    """读取样本元数据表。

    首行为列名，首列为样本 ID，其余列原样保存为字符串。
    不做类型推断，避免把分组编号、批次号等标识误转成数值。
    """
    file_path = Path(path)
    rows = read_rows(file_path, resolve_delimiter(file_path, delimiter))
    if len(rows) < 2:
        raise ValueError("元数据文件需要表头和至少一个数据行。")

    header = [cell.strip() for cell in rows[0]]
    if len(header) < 2:
        raise ValueError("元数据文件至少需要样本 ID 列和一个字段列。")
    columns = tuple(header[1:])
    require_unique(columns, "元数据列名")

    sample_ids: list[str] = []
    records: dict[str, dict[str, str]] = {}
    for row_index, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise ValueError(f"元数据第 {row_index} 行列数不一致。")
        sample_id = row[0].strip()
        if not sample_id:
            raise ValueError(f"元数据第 {row_index} 行缺少样本 ID。")
        if sample_id in records:
            raise ValueError(f"元数据存在重复样本 ID：{sample_id}。")
        sample_ids.append(sample_id)
        records[sample_id] = {
            name: row[index + 1].strip() for index, name in enumerate(columns)
        }

    return MetadataTable(
        sample_ids=tuple(sample_ids),
        columns=columns,
        rows=records,
    )


def align_metadata(
    sample_ids: Sequence[str],
    metadata: MetadataTable,
) -> list[dict[str, str]]:
    """按数据表的行顺序取出元数据行。

    数据表中的样本如果在元数据里缺失就直接报错，而不是静默跳过。
    静默丢样本会改变样本集合，从而改变统计含义。
    """
    missing = [sample_id for sample_id in sample_ids if sample_id not in metadata.rows]
    if missing:
        raise ValueError(f"以下样本在元数据中缺失：{'、'.join(missing)}。")
    return [dict(metadata.rows[sample_id]) for sample_id in sample_ids]
