"""距离度量的测试。"""

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCoA import (
    pairwise_bray_curtis,
    pairwise_euclidean,
)


def test_euclidean_matches_hand_computed_distances() -> None:
    matrix = np.array([[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]])

    distance = pairwise_euclidean(matrix)

    assert np.allclose(distance, [[0.0, 3.0, 4.0], [3.0, 0.0, 5.0], [4.0, 5.0, 0.0]])
    assert np.allclose(np.diag(distance), 0.0)
    assert np.allclose(distance, distance.T)


def test_euclidean_keeps_zero_diagonal_without_negative_square_roots() -> None:
    """Gram 展开会引入浮点误差，对角线必须被显式压成 0 而不是极小正数。"""
    rng = np.random.default_rng(20240909)
    matrix = rng.normal(size=(12, 5))

    distance = pairwise_euclidean(matrix)

    assert np.array_equal(np.diag(distance), np.zeros(12))
    assert np.isfinite(distance).all()


def test_bray_curtis_matches_hand_computed_distances() -> None:
    matrix = np.array(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 1.0],
            [2.0, 0.0],
        ]
    )

    distance = pairwise_bray_curtis(matrix)

    # (1,0) 与 (0,1)：2 / 2 = 1
    assert distance[0, 1] == pytest.approx(1.0)
    # (1,0) 与 (1,1)：1 / 3
    assert distance[0, 2] == pytest.approx(1.0 / 3.0)
    # (1,1) 与 (2,0)：2 / 4 = 0.5
    assert distance[2, 3] == pytest.approx(0.5)
    assert np.allclose(np.diag(distance), 0.0)
    assert np.allclose(distance, distance.T)


def test_bray_curtis_with_all_zero_sample() -> None:
    """全零样本与全零样本之间的距离定义为 0，与有值样本之间为 1。"""
    matrix = np.array([[0.0, 0.0], [1.0, 2.0]])

    distance = pairwise_bray_curtis(matrix)

    assert distance[0, 1] == pytest.approx(1.0)
    assert np.allclose(pairwise_bray_curtis(np.zeros((2, 3))), 0.0)


def test_distance_functions_reject_invalid_input() -> None:
    with pytest.raises(ValueError, match="二维矩阵"):
        pairwise_euclidean(np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="至少需要 2 个样本"):
        pairwise_euclidean(np.array([[1.0, 2.0]]))
    with pytest.raises(ValueError, match="NaN"):
        pairwise_euclidean(np.array([[1.0, np.nan], [2.0, 3.0]]))
    with pytest.raises(ValueError, match="非负"):
        pairwise_bray_curtis(np.array([[1.0, -2.0], [2.0, 3.0]]))
