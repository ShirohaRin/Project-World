"""PCA 核心算法的确定性测试。"""

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCA import fit_pca


def test_pca_returns_expected_shapes_and_variance_ratio() -> None:
    matrix = np.array(
        [
            [1.0, 2.0],
            [2.0, 4.0],
            [3.0, 6.0],
            [4.0, 8.0],
        ]
    )

    result = fit_pca(matrix)

    assert result.scores.shape == (4, 2)
    assert result.loadings.shape == (2, 2)
    assert result.eigenvalues.shape == (2,)
    assert result.feature_mean.tolist() == [2.5, 5.0]
    assert np.isclose(result.explained_variance_ratio[0], 1.0)
    assert np.allclose(result.explained_variance_ratio[1], 0.0)
    assert np.allclose(result.cumulative_explained_variance_ratio, [1.0, 1.0])


def test_pca_matches_svd_reconstruction_for_selected_components() -> None:
    matrix = np.array(
        [
            [1.0, 0.0, 2.0],
            [2.0, 1.0, 0.0],
            [0.0, 3.0, 1.0],
            [4.0, 2.0, 3.0],
        ]
    )

    result = fit_pca(matrix, n_components=2)
    centered = matrix - matrix.mean(axis=0)
    reconstructed = result.scores @ result.loadings.T

    expected = np.linalg.svd(centered, full_matrices=False)
    expected_reconstructed = expected[0][:, :2] @ np.diag(expected[1][:2]) @ expected[2][:2]

    assert np.allclose(reconstructed, expected_reconstructed)
    assert result.scores.shape == (4, 2)


def test_zscore_and_log1p_are_explicit_transforms() -> None:
    matrix = np.array([[0.0, 1.0], [1.0, 3.0], [3.0, 7.0]])

    zscore_result = fit_pca(matrix, transform="zscore")
    log_result = fit_pca(matrix, transform="log1p")

    assert np.allclose(zscore_result.feature_mean, matrix.mean(axis=0))
    assert np.all(zscore_result.feature_scale > 0)
    assert not np.allclose(zscore_result.scores, log_result.scores)


def test_pca_rejects_invalid_shape_component_count_and_transform() -> None:
    matrix = np.array([[1.0, 2.0], [2.0, 3.0]])

    with pytest.raises(ValueError, match="二维矩阵"):
        fit_pca(np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="至少需要"):
        fit_pca(np.array([[1.0, 2.0]]))
    with pytest.raises(ValueError, match="n_components"):
        fit_pca(matrix, n_components=0)
    with pytest.raises(ValueError, match="不支持"):
        fit_pca(matrix, transform="invalid")  # type: ignore[arg-type]


def test_pca_rejects_missing_values_and_invalid_log1p_input() -> None:
    with pytest.raises(ValueError, match="NaN"):
        fit_pca(np.array([[1.0, np.nan], [2.0, 3.0]]))

    with pytest.raises(ValueError, match="不小于 0"):
        fit_pca(np.array([[-1.0, 2.0], [1.0, 3.0]]), transform="log1p")


def test_zscore_rejects_constant_features() -> None:
    with pytest.raises(ValueError, match="常数特征"):
        fit_pca(np.array([[1.0, 2.0], [1.0, 3.0], [1.0, 4.0]]), transform="zscore")


def test_zscore_pins_the_population_standard_deviation_convention() -> None:
    """固定 z-score 的 ddof=0 口径，避免口径被无意改变。"""
    matrix = np.array([[1.0, 2.0], [2.0, 4.0], [4.0, 9.0], [8.0, 5.0]])

    result = fit_pca(matrix, transform="zscore")
    scaled = (matrix - matrix.mean(axis=0)) / result.feature_scale

    # ddof=0 口径下逐列标准差为 1，与 scikit-learn StandardScaler 一致。
    assert np.allclose(scaled.std(axis=0, ddof=0), 1.0)
    # n-1 口径下方差为 n/(n-1)，说明与 R 的 prcomp(scale = TRUE) 口径不同。
    assert np.allclose(
        scaled.var(axis=0, ddof=1),
        matrix.shape[0] / (matrix.shape[0] - 1),
    )
