"""PCA 可视化数据接口的测试。"""

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCA import (
    build_score_plot,
    fit_pca,
    rank_loadings,
)


def _result():
    matrix = np.array(
        [
            [1.0, 2.0, 0.5],
            [2.0, 4.0, 1.0],
            [3.0, 6.0, 1.5],
            [5.0, 1.0, 3.0],
        ]
    )
    return fit_pca(matrix)


def test_build_score_plot_returns_coordinates_variance_and_groups() -> None:
    result = _result()

    plot = build_score_plot(
        result,
        sample_ids=("S1", "S2", "S3", "S4"),
        groups=("a", "a", "b", "b"),
    )

    assert plot["components"] == [1, 2]
    assert plot["explained_variance_ratio"][0] == pytest.approx(
        result.explained_variance_ratio[0]
    )
    assert [point["sample_id"] for point in plot["points"]] == ["S1", "S2", "S3", "S4"]
    assert plot["points"][0]["x"] == pytest.approx(result.scores[0, 0])
    assert plot["points"][0]["y"] == pytest.approx(result.scores[0, 1])
    assert plot["points"][3]["group"] == "b"


def test_build_score_plot_uses_default_labels_and_rejects_bad_arguments() -> None:
    result = _result()

    plot = build_score_plot(result)
    assert plot["points"][0]["sample_id"] == "sample_1"
    assert plot["points"][0]["group"] is None

    with pytest.raises(ValueError, match="坐标列索引"):
        build_score_plot(result, components=(0, 9))
    with pytest.raises(ValueError, match="分组标签数量"):
        build_score_plot(result, groups=("a",))
    with pytest.raises(ValueError, match="样本标签数量"):
        build_score_plot(result, sample_ids=("S1",))


def test_rank_loadings_orders_by_absolute_loading_and_applies_top_n() -> None:
    result = _result()

    ranked = rank_loadings(result, feature_names=("f1", "f2", "f3"))

    assert [item["rank"] for item in ranked] == [1, 2, 3]
    assert all(
        ranked[index]["abs_loading"] >= ranked[index + 1]["abs_loading"]
        for index in range(len(ranked) - 1)
    )
    assert {item["feature"] for item in ranked} == {"f1", "f2", "f3"}

    top_one = rank_loadings(result, component=1, top_n=1)
    assert len(top_one) == 1
    assert top_one[0]["feature"] == "feature_1"


def test_rank_loadings_rejects_invalid_component_and_top_n() -> None:
    result = _result()

    with pytest.raises(ValueError, match="主成分索引"):
        rank_loadings(result, component=5)
    with pytest.raises(ValueError, match="top_n"):
        rank_loadings(result, top_n=0)
