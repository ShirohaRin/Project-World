"""PCoA 核心算法的确定性测试。"""

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCoA import fit_pcoa


def _square() -> np.ndarray:
    """单位正方形四个顶点的距离矩阵：二维配置，两个相等的特征值。"""
    side = 1.0
    diagonal = np.sqrt(2.0)
    return np.array(
        [
            [0.0, side, diagonal, side],
            [side, 0.0, side, diagonal],
            [diagonal, side, 0.0, side],
            [side, diagonal, side, 0.0],
        ]
    )


def _reconstruct(coordinates: np.ndarray) -> np.ndarray:
    """由主坐标反算样本间的欧氏距离。"""
    diff = coordinates[:, None, :] - coordinates[None, :, :]
    return np.sqrt((diff**2).sum(axis=-1))


def test_recovers_one_dimensional_configuration() -> None:
    """三个共线点（位置 0、1、3）的距离应当被精确还原。"""
    distance = np.array([[0.0, 1.0, 3.0], [1.0, 0.0, 2.0], [3.0, 2.0, 0.0]])

    result = fit_pcoa(distance)

    assert result.is_euclidean
    assert result.coordinates.shape == (3, 1)
    # 三个点的一维方差 = (16/9 + 1/9 + 25/9) / 1 = 14/3，即双中心化矩阵的唯一特征值
    assert result.eigenvalues == pytest.approx([14.0 / 3.0])
    assert result.explained_variance_ratio == pytest.approx([1.0])
    assert np.allclose(_reconstruct(result.coordinates), distance)


def test_geometric_configurations_give_expected_component_counts() -> None:
    tetrahedron = np.ones((4, 4)) - np.eye(4)

    tetra = fit_pcoa(tetrahedron)
    # 四点两两等距只能落在三维（正四面体），三个特征值相等
    assert tetra.coordinates.shape == (4, 3)
    assert tetra.eigenvalues == pytest.approx([0.5, 0.5, 0.5])
    assert tetra.explained_variance_ratio == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    square = fit_pcoa(_square())
    assert square.coordinates.shape == (4, 2)
    assert square.eigenvalues == pytest.approx([1.0, 1.0])
    assert np.allclose(_reconstruct(square.coordinates), _square())


def test_non_euclidean_distance_reports_negative_eigenvalues() -> None:
    """违反三角不等式的距离不是欧氏距离，双中心化后必然出现负特征值。"""
    distance = np.array([[0.0, 1.0, 5.0], [1.0, 0.0, 1.0], [5.0, 1.0, 0.0]])

    result = fit_pcoa(distance)

    assert not result.is_euclidean
    assert result.eigenvalues == pytest.approx([12.5])
    assert result.negative_eigenvalues == pytest.approx([-3.5])
    # 解释方差的分母是 Σ|λ| = 12.5 + 3.5 = 16
    assert result.explained_variance_ratio == pytest.approx([0.78125])
    # 负特征值开不出实坐标，只能取正特征值那一维
    assert result.coordinates.shape == (3, 1)


def test_n_components_limits_coordinates() -> None:
    full = fit_pcoa(_square())
    partial = fit_pcoa(_square(), n_components=1)

    assert partial.coordinates.shape == (4, 1)
    assert partial.eigenvalues == pytest.approx(full.eigenvalues[:1])
    assert partial.explained_variance_ratio == pytest.approx(
        full.explained_variance_ratio[:1]
    )

    with pytest.raises(ValueError, match="n_components"):
        fit_pcoa(_square(), n_components=0)
    with pytest.raises(ValueError, match="n_components"):
        fit_pcoa(_square(), n_components=3)


def test_rejects_invalid_distance_matrices() -> None:
    with pytest.raises(ValueError, match="二维矩阵"):
        fit_pcoa(np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="方阵"):
        fit_pcoa(np.array([[0.0, 1.0], [1.0, 0.0], [2.0, 3.0]]))
    with pytest.raises(ValueError, match="至少需要 2 个样本"):
        fit_pcoa(np.array([[0.0]]))
    with pytest.raises(ValueError, match="NaN"):
        fit_pcoa(np.array([[0.0, np.nan], [np.nan, 0.0]]))
    with pytest.raises(ValueError, match="负值"):
        fit_pcoa(np.array([[0.0, -1.0], [-1.0, 0.0]]))
    with pytest.raises(ValueError, match="必须对称"):
        fit_pcoa(np.array([[0.0, 1.0], [2.0, 0.0]]))
    with pytest.raises(ValueError, match="对角线"):
        fit_pcoa(np.array([[1.0, 1.0], [1.0, 1.0]]))


def test_rejects_distance_matrix_without_positive_eigenvalues() -> None:
    with pytest.raises(ValueError, match="没有正特征值"):
        fit_pcoa(np.zeros((3, 3)))
