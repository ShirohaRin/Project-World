"""PCoA 距离矩阵读取的测试。"""

from pathlib import Path

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCoA import load_distance_matrix


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_distance_matrix_reads_square_matrix(tmp_path: Path) -> None:
    path = _write(tmp_path / "distance.csv", "sample_id,A,B\nA,0,1\nB,1,0\n")

    matrix = load_distance_matrix(path)

    assert matrix.sample_ids == ("A", "B")
    assert np.allclose(matrix.values, [[0.0, 1.0], [1.0, 0.0]])
    assert matrix.values.dtype == np.float64


def test_load_distance_matrix_rejects_non_square(tmp_path: Path) -> None:
    path = _write(tmp_path / "distance.csv", "sample_id,A,B,C\nA,0,1,2\nB,1,0,3\n")

    with pytest.raises(ValueError, match="方阵"):
        load_distance_matrix(path)


def test_load_distance_matrix_rejects_mismatched_labels(tmp_path: Path) -> None:
    """行列标签不一致意味着样本错位，会安静地给出错误坐标。"""
    path = _write(tmp_path / "distance.csv", "sample_id,A,B\nA,0,1\nC,1,0\n")

    with pytest.raises(ValueError, match="标签必须完全一致"):
        load_distance_matrix(path)


def test_load_distance_matrix_rejects_non_numeric(tmp_path: Path) -> None:
    path = _write(tmp_path / "distance.csv", "sample_id,A,B\nA,0,x\nB,1,0\n")

    with pytest.raises(ValueError, match="不是合法数值"):
        load_distance_matrix(path)
