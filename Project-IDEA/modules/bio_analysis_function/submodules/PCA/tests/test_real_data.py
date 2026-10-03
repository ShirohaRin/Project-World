"""在真实公开数据集上验证 PCA 行为。

数据来源
--------
UCI Machine Learning Repository，
Breast Cancer Wisconsin (Diagnostic)，569 个样本 × 30 个数值特征，
标签为恶性（M，212 例）/ 良性（B，357 例）。

原始文件：
https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/wdbc.data

本目录 ``data/`` 下的 ``wdbc_features.csv`` 与 ``wdbc_labels.csv`` 是它的拆分结果：
原始文件无表头、32 列，第 0 列为样本 ID、第 1 列为诊断结果、第 2-31 列为 30 个特征。
拆分后数据固定不变，因此测试不访问网络。

这些测试同时起到两个作用：验证 PCA 在真实数据上的数值行为，
以及固定几项容易被无意改动的口径约定（尤其是 z-score 的 ddof）。
"""

from pathlib import Path

import numpy as np
import pytest

from modules.bio_analysis_function.common.io import align_metadata, load_metadata
from modules.bio_analysis_function.submodules.PCA import (
    fit_pca,
    load_feature_matrix,
    rank_loadings,
)

# 真实数据集由整个模块共享，放在模块根目录的 tests/data 下。
DATA_DIR = Path(__file__).parents[3] / "tests" / "data"

# 已发表分析中常见的 PC1/PC2 载荷头部特征，用于检查载荷是否落在合理的方向上。
KNOWN_DISCRIMINATIVE_FEATURES = {
    "concave_points_mean",
    "concavity_mean",
    "concave_points_worst",
    "compactness_mean",
    "perimeter_worst",
}


@pytest.fixture(scope="module")
def wdbc():
    table = load_feature_matrix(DATA_DIR / "wdbc_features.csv")
    metadata = load_metadata(DATA_DIR / "wdbc_labels.csv")
    rows = align_metadata(table.sample_ids, metadata)
    return table, metadata, np.array([row["diagnosis"] for row in rows])


def test_real_dataset_loads_with_expected_shape_and_groups(wdbc) -> None:
    table, metadata, groups = wdbc

    assert table.values.shape == (569, 30)
    assert len(table.feature_names) == 30
    assert metadata.columns == ("diagnosis",)
    assert sorted(np.unique(groups)) == ["B", "M"]
    assert int((groups == "M").sum()) == 212
    assert int((groups == "B").sum()) == 357
    assert np.isfinite(table.values).all()


def test_unscaled_pca_is_dominated_by_large_variance_features(wdbc) -> None:
    """不做标准化时，方差极大的特征会吞掉几乎全部解释方差。"""
    table, _, _ = wdbc

    variances = table.values.var(axis=0)
    # 原始尺度下方差跨度极大，这正是需要 z-score 的原因。
    assert variances.max() / variances.min() > 1e6

    centered = fit_pca(table.values, transform="center")
    assert centered.explained_variance_ratio[0] > 0.95

    top = rank_loadings(centered, feature_names=table.feature_names, top_n=2)
    assert {item["feature"] for item in top} <= {"area_worst", "area_mean"}


def test_zscore_pca_shows_expected_variance_structure(wdbc) -> None:
    """标准化后 PC1/PC2 的方差占比应与常规分析结果一致。"""
    table, _, _ = wdbc

    scaled = fit_pca(table.values, transform="zscore")

    assert scaled.explained_variance_ratio[0] == pytest.approx(0.4427, abs=0.005)
    assert scaled.explained_variance_ratio[1] == pytest.approx(0.1897, abs=0.005)
    assert scaled.cumulative_explained_variance_ratio[1] == pytest.approx(
        0.6324, abs=0.005
    )
    # 解释方差比例之和必须为 1。
    assert scaled.cumulative_explained_variance_ratio[-1] == pytest.approx(1.0, abs=1e-9)
    # 特征值必须单调不增。
    assert np.all(np.diff(scaled.eigenvalues) <= 1e-12)


def test_top_loadings_fall_on_concavity_related_features(wdbc) -> None:
    table, _, _ = wdbc

    scaled = fit_pca(table.values, transform="zscore")
    # rank_loadings 按绝对值排序，因此不受主方向整体符号翻转的影响。
    top = [item["feature"] for item in rank_loadings(
        scaled, feature_names=table.feature_names, top_n=5
    )]

    assert top[0].startswith(("concave_points", "concavity"))
    assert len(set(top) & KNOWN_DISCRIMINATIVE_FEATURES) >= 3


def test_groups_separate_on_first_component(wdbc) -> None:
    """恶性与良性样本应在 PC1 上明显分开。

    只检查分离的幅度，不检查方向：主方向的整体符号可以翻转，
    因此“M 在正侧还是负侧”不是稳定性质。
    """
    table, _, groups = wdbc

    scaled = fit_pca(table.values, transform="zscore")
    first = scaled.scores[:, 0]

    separation = abs(first[groups == "M"].mean() - first[groups == "B"].mean())
    assert separation > 4.0

    # 分开方向对调后，用中位数做单变量切分也能达到较高准确率。
    cut = np.median(first)
    prediction = np.where(first > cut, "M", "B")
    accuracy = (prediction == groups).mean()
    assert max(accuracy, 1 - accuracy) > 0.80


def test_eigenvalue_sum_follows_documented_ddof_convention(wdbc) -> None:
    """固定 z-score 用 ddof=0、特征值用 n-1 这一约定在真实数据上的后果。

    z-score 后每个特征的样本方差（n-1 口径）等于 n/(n-1)，
    因此特征值之和等于 p * n/(n-1)，而不是精确的 p。
    """
    table, _, _ = wdbc

    scaled = fit_pca(table.values, transform="zscore")
    n_samples, n_features = table.values.shape
    expected = n_features * n_samples / (n_samples - 1)

    assert scaled.eigenvalues.sum() == pytest.approx(expected, abs=1e-4)
    # 差异很小，但不能当成精确相等。
    assert scaled.eigenvalues.sum() != pytest.approx(float(n_features), abs=1e-6)
