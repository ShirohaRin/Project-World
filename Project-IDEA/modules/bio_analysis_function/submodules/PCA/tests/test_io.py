"""PCA 特征矩阵读取的测试：只测 PCA 特有的语义约定。"""

from pathlib import Path

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCA import load_feature_matrix


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_feature_matrix_reads_numeric_matrix(tmp_path: Path) -> None:
    path = _write(tmp_path / "matrix.csv", "geneA,geneB\nS1,1,2\nS2,3,4\n")

    table = load_feature_matrix(path)

    assert table.sample_ids == ("S1", "S2")
    assert table.feature_names == ("geneA", "geneB")
    assert np.allclose(table.values, [[1.0, 2.0], [3.0, 4.0]])
    assert table.values.dtype == np.float64


def test_load_feature_matrix_generates_sample_and_feature_labels(tmp_path: Path) -> None:
    path = _write(tmp_path / "matrix.tsv", "1\t2\n3\t4\n")

    table = load_feature_matrix(path, has_header=False, has_index=False)

    assert table.sample_ids == ("sample_1", "sample_2")
    assert table.feature_names == ("feature_1", "feature_2")


def test_load_feature_matrix_rejects_missing_values(tmp_path: Path) -> None:
    path = _write(tmp_path / "empty.csv", "a,b\nS1,1,\n")

    with pytest.raises(ValueError, match="空值"):
        load_feature_matrix(path)
