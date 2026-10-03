"""PCoA 可视化数据接口的测试。"""

import numpy as np
import pytest

from modules.bio_analysis_function.submodules.PCoA import (
    build_coordinate_plot,
    fit_pcoa,
)


def _result():
    side = 1.0
    diagonal = np.sqrt(2.0)
    distance = np.array(
        [
            [0.0, side, diagonal, side],
            [side, 0.0, side, diagonal],
            [diagonal, side, 0.0, side],
            [side, diagonal, side, 0.0],
        ]
    )
    return fit_pcoa(distance)


def test_build_coordinate_plot_uses_pcoa_axis_labels() -> None:
    result = _result()

    plot = build_coordinate_plot(
        result,
        sample_ids=("S1", "S2", "S3", "S4"),
        groups=("a", "a", "b", "b"),
    )

    assert plot["axis_labels"] == ["PCoA1", "PCoA2"]
    assert plot["components"] == [1, 2]
    assert plot["explained_variance_ratio"] == pytest.approx([0.5, 0.5])
    assert [point["sample_id"] for point in plot["points"]] == ["S1", "S2", "S3", "S4"]
    assert plot["points"][0]["x"] == pytest.approx(result.coordinates[0, 0])
    assert plot["points"][3]["group"] == "b"


def test_build_coordinate_plot_defaults_and_argument_validation() -> None:
    result = _result()

    plot = build_coordinate_plot(result)
    assert plot["points"][0]["sample_id"] == "sample_1"
    assert plot["points"][0]["group"] is None

    with pytest.raises(ValueError, match="坐标列索引"):
        build_coordinate_plot(result, components=(0, 5))
    with pytest.raises(ValueError, match="分组标签数量"):
        build_coordinate_plot(result, groups=("a",))
    with pytest.raises(ValueError, match="样本标签数量"):
        build_coordinate_plot(result, sample_ids=("S1",))
