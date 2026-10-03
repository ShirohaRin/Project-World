"""公共数据输入层的测试：只测与算法无关的读取行为。"""

from pathlib import Path

import numpy as np
import pytest

from modules.bio_analysis_function.common.io import (
    align_metadata,
    load_labeled_table,
    load_metadata,
    to_float_matrix,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_labeled_table_reads_csv_with_header_and_index(tmp_path: Path) -> None:
    path = _write(tmp_path / "table.csv", "geneA,geneB\nS1,1,2\nS2,3,4\n")

    table = load_labeled_table(path)

    assert table.row_labels == ("S1", "S2")
    assert table.column_labels == ("geneA", "geneB")
    assert table.cells == (("1", "2"), ("3", "4"))


def test_load_labeled_table_infers_delimiter_and_uses_given_prefixes(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "table.tsv", "1\t2\n3\t4\n")

    table = load_labeled_table(
        path,
        has_header=False,
        has_index=False,
        row_label_prefix="sample",
        column_label_prefix="feature",
    )

    assert table.row_labels == ("sample_1", "sample_2")
    assert table.column_labels == ("feature_1", "feature_2")


def test_load_labeled_table_requires_explicit_delimiter_for_unknown_suffix(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "table.dat", "a,b\nS1,1,2\n")

    with pytest.raises(ValueError, match="分隔符"):
        load_labeled_table(path)

    table = load_labeled_table(path, delimiter=",")
    assert table.column_labels == ("a", "b")


def test_load_labeled_table_accepts_header_with_or_without_corner_cell(
    tmp_path: Path,
) -> None:
    without_corner = _write(tmp_path / "plain.csv", "geneA,geneB\nS1,1,2\n")
    with_corner = _write(tmp_path / "corner.csv", "sample_id,geneA,geneB\nS1,1,2\n")

    assert load_labeled_table(without_corner).column_labels == ("geneA", "geneB")
    assert load_labeled_table(with_corner).column_labels == ("geneA", "geneB")


def test_load_labeled_table_rejects_ragged_rows_and_duplicate_labels(
    tmp_path: Path,
) -> None:
    ragged = _write(tmp_path / "ragged.csv", "a,b\nS1,1,2\nS2,3\n")
    with pytest.raises(ValueError, match="列数不一致"):
        load_labeled_table(ragged)

    duplicate_row = _write(tmp_path / "duprow.csv", "a,b\nS1,1,2\nS1,3,4\n")
    with pytest.raises(ValueError, match="行标签 重复"):
        load_labeled_table(duplicate_row)

    duplicate_column = _write(tmp_path / "dupcol.csv", "a,a\nS1,1,2\n")
    with pytest.raises(ValueError, match="列名 重复"):
        load_labeled_table(duplicate_column)


def test_to_float_matrix_parses_values(tmp_path: Path) -> None:
    path = _write(tmp_path / "num.csv", "a,b\nS1,1,2\nS2,3.5,4\n")

    values = to_float_matrix(load_labeled_table(path))

    assert values.dtype == np.float64
    assert np.allclose(values, [[1.0, 2.0], [3.5, 4.0]])


def test_to_float_matrix_rejects_empty_and_non_numeric_cells(tmp_path: Path) -> None:
    empty_cell = _write(tmp_path / "empty.csv", "a,b\nS1,1,\n")
    with pytest.raises(ValueError, match="空值"):
        to_float_matrix(load_labeled_table(empty_cell))

    non_numeric = _write(tmp_path / "bad.csv", "a,b\nS1,1,x\n")
    with pytest.raises(ValueError, match="不是合法数值"):
        to_float_matrix(load_labeled_table(non_numeric))


def test_load_and_align_metadata(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "meta.csv",
        "sample_id,group,batch\nS1,control,A\nS2,treat,B\n",
    )

    metadata = load_metadata(path)
    aligned = align_metadata(("S2", "S1"), metadata)

    assert metadata.columns == ("group", "batch")
    assert aligned[0] == {"group": "treat", "batch": "B"}
    assert aligned[1] == {"group": "control", "batch": "A"}


def test_align_metadata_rejects_missing_samples(tmp_path: Path) -> None:
    path = _write(tmp_path / "meta.csv", "sample_id,group\nS1,control\n")
    metadata = load_metadata(path)

    with pytest.raises(ValueError, match="缺失"):
        align_metadata(("S1", "S9"), metadata)
