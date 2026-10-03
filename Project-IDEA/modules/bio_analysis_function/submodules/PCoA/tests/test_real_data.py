"""在真实公开数据集上验证 PCoA。

复用模块共享的 WDBC 数据集（569 个样本 × 30 个数值特征，标签为恶性 M / 良性 B）。
数据集说明见 ``modules/bio_analysis_function/tests/data``。

这里跨子模块引用 PCA 是刻意的：欧氏距离下的 PCoA 与对中心化数据做 PCA
在数学上应当给出同一组坐标，用两条独立实现互相验证是最强的正确性检查。
"""

from pathlib import Path

import numpy as np
import pytest

from modules.bio_analysis_function.common.io import align_metadata, load_metadata
from modules.bio_analysis_function.submodules.PCA import fit_pca, load_feature_matrix
from modules.bio_analysis_function.submodules.PCoA import (
    fit_pcoa,
    pairwise_bray_curtis,
    pairwise_euclidean,
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"


@pytest.fixture(scope="module")
def wdbc():
    table = load_feature_matrix(DATA_DIR / "wdbc_features.csv")
    metadata = load_metadata(DATA_DIR / "wdbc_labels.csv")
    rows = align_metadata(table.sample_ids, metadata)
    return table, np.array([row["diagnosis"] for row in rows])


def _standardized(table):
    """按 ddof=0 逐列标准化，与 PCA 的 transform="zscore" 口径一致。"""
    return (table.values - table.values.mean(axis=0)) / table.values.std(
        axis=0, ddof=0
    )


def test_euclidean_pcoa_reproduces_pca_coordinates(wdbc) -> None:
    """欧氏距离下 PCoA 与 PCA 必须给出同一组坐标（允许每轴符号翻转）。"""
    table, _ = wdbc
    standardized = _standardized(table)

    pca = fit_pca(standardized, transform="center")
    pcoa = fit_pcoa(pairwise_euclidean(standardized))

    assert pcoa.is_euclidean
    # 直接比较坐标会被符号翻转干扰，改比较 Gram 矩阵（旋转与符号不变）。
    gram_pca = pca.scores @ pca.scores.T
    gram_pcoa = pcoa.coordinates @ pcoa.coordinates.T
    assert np.allclose(gram_pca, gram_pcoa, rtol=1e-8, atol=1e-8)


def test_euclidean_pcoa_matches_pca_variance_structure(wdbc) -> None:
    table, _ = wdbc
    standardized = _standardized(table)

    pca = fit_pca(standardized, transform="center")
    pcoa = fit_pcoa(pairwise_euclidean(standardized))

    assert pcoa.explained_variance_ratio[:3] == pytest.approx(
        pca.explained_variance_ratio[:3], abs=1e-8
    )
    assert pcoa.explained_variance_ratio[0] == pytest.approx(0.4427, abs=0.005)
    assert pcoa.cumulative_explained_variance_ratio[-1] == pytest.approx(1.0, abs=1e-9)


def test_euclidean_pcoa_detects_rank_deficiency_of_raw_features(wdbc) -> None:
    """原始尺度下特征高度共线，主坐标数会明显少于特征数。"""
    table, _ = wdbc

    pcoa = fit_pcoa(pairwise_euclidean(table.values))

    assert pcoa.is_euclidean
    assert pcoa.coordinates.shape[1] < table.values.shape[1]
    assert pcoa.explained_variance_ratio[0] > 0.95


def test_bray_curtis_on_real_data_is_non_euclidean(wdbc) -> None:
    """Bray–Curtis 一般不是欧氏距离，双中心化后会出现负特征值。"""
    table, _ = wdbc

    distance = pairwise_bray_curtis(table.values)
    assert np.allclose(distance, distance.T)
    assert np.allclose(np.diag(distance), 0.0)
    assert float(distance.min()) >= 0.0
    assert float(distance.max()) <= 1.0

    result = fit_pcoa(distance)
    assert not result.is_euclidean
    assert result.negative_eigenvalues.size > 0
    assert float(result.negative_eigenvalues.sum()) < 0.0
    # 负特征值不参与坐标，因此解释方差比例之和小于 1
    assert float(result.explained_variance_ratio.sum()) < 1.0
    assert result.explained_variance_ratio[0] == pytest.approx(0.7956, abs=0.01)


def test_euclidean_pcoa_separates_diagnosis_groups(wdbc) -> None:
    """标准化后的欧氏 PCoA 应像 PCA 一样把恶性与良性样本分开。

    只检查分离幅度，不检查方向：主坐标的符号可以整体翻转。
    """
    table, groups = wdbc
    standardized = _standardized(table)

    result = fit_pcoa(pairwise_euclidean(standardized))
    first = result.coordinates[:, 0]

    separation = abs(first[groups == "M"].mean() - first[groups == "B"].mean())
    assert separation > 4.0
