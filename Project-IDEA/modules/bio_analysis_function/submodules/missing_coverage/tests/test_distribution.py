"""分布拟合与阈值的定点用例：矩估计能反解手算值、概率归一、分位点定义、退化分支。"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.submodules.missing_coverage import (
    CoverageDistribution,
    CoverageProfile,
    fit_coverage_distribution,
    low_coverage_cutoff,
)


def _profile(*depths: int) -> CoverageProfile:
    return CoverageProfile(seq_id="chr", depths=depths)


# ---------------------------------------------------------------------------
# 矩估计
# ---------------------------------------------------------------------------


def test_overdispersed_sample_gives_exact_negative_binomial_parameters() -> None:
    # (0, 0, 5, 5, 5, 5)：μ = 10/3、σ² = 50/9 → r = 5、p = 5/(5+10/3) = 0.6
    distribution = fit_coverage_distribution(_profile(0, 0, 5, 5, 5, 5))
    assert distribution is not None
    assert distribution.mean == pytest.approx(10 / 3)
    assert distribution.variance == pytest.approx(50 / 9)
    assert distribution.dispersion == pytest.approx(5.0)
    assert distribution.probability == pytest.approx(0.6)
    assert not distribution.is_poisson


def test_underdispersed_sample_falls_back_to_poisson() -> None:
    # (0, 1, 1, 2, 2, 2)：μ = 4/3、σ² = 5/9 < μ —— 没有过度离散，负二项不适用
    distribution = fit_coverage_distribution(_profile(0, 1, 1, 2, 2, 2))
    assert distribution is not None
    assert distribution.is_poisson
    assert distribution.dispersion is None
    with pytest.raises(ValueError, match="泊松分支"):
        _ = distribution.probability


def test_flat_coverage_is_poisson_with_zero_variance() -> None:
    distribution = fit_coverage_distribution(_profile(7, 7, 7, 7))
    assert distribution is not None
    assert distribution.is_poisson
    assert distribution.mean == pytest.approx(7.0)
    assert distribution.variance == pytest.approx(0.0)


def test_no_coverage_anywhere_is_not_fitted() -> None:
    assert fit_coverage_distribution(_profile(0, 0, 0)) is None
    assert fit_coverage_distribution(CoverageProfile(seq_id="chr", depths=())) is None


def test_negative_moments_are_rejected() -> None:
    with pytest.raises(ValueError, match="必须非负"):
        CoverageDistribution.from_moments(-1.0, 1.0)


# ---------------------------------------------------------------------------
# 概率
# ---------------------------------------------------------------------------


def test_negative_binomial_probabilities_sum_to_one() -> None:
    distribution = CoverageDistribution.from_moments(10 / 3, 50 / 9)
    total = sum(distribution.pmf(depth) for depth in range(200))
    assert total == pytest.approx(1.0)


def test_poisson_probabilities_sum_to_one() -> None:
    distribution = CoverageDistribution.from_moments(4.0, 4.0)
    assert distribution.is_poisson
    total = sum(distribution.pmf(depth) for depth in range(200))
    assert total == pytest.approx(1.0)


def test_negative_depths_have_zero_probability() -> None:
    distribution = CoverageDistribution.from_moments(10 / 3, 50 / 9)
    assert distribution.pmf(-1) == 0.0
    assert distribution.cdf(-1) == 0.0


def test_degenerate_zero_mean_puts_all_mass_at_zero() -> None:
    distribution = CoverageDistribution.from_moments(0.0, 0.0)
    assert distribution.pmf(0) == 1.0
    assert distribution.pmf(1) == 0.0
    assert distribution.cdf(0) == pytest.approx(1.0)


def test_cdf_is_monotone_and_bounded() -> None:
    distribution = CoverageDistribution.from_moments(10 / 3, 50 / 9)
    values = [distribution.cdf(depth) for depth in range(30)]
    assert values == sorted(values)
    assert values[-1] <= 1.0


# ---------------------------------------------------------------------------
# 分位点与阈值
# ---------------------------------------------------------------------------


def test_quantile_is_the_smallest_depth_reaching_the_probability() -> None:
    distribution = CoverageDistribution.from_moments(4.0, 4.0)
    for probability in (0.001, 0.05, 0.5, 0.9):
        depth = distribution.quantile(probability)
        assert distribution.cdf(depth) >= probability
        assert distribution.cdf(depth - 1) < probability


def test_quantile_rejects_probabilities_outside_the_open_unit_interval() -> None:
    distribution = CoverageDistribution.from_moments(4.0, 4.0)
    with pytest.raises(ValueError, match="落在 \\(0, 1\\)"):
        distribution.quantile(0.0)
    with pytest.raises(ValueError, match="落在 \\(0, 1\\)"):
        distribution.quantile(1.0)


def test_cutoff_uses_the_multiple_testing_scale_of_the_sequence_length() -> None:
    distribution = CoverageDistribution.from_moments(100.0, 100.0)
    short = low_coverage_cutoff(distribution, 10_000)
    long = low_coverage_cutoff(distribution, 10_000_000)
    # 参考越长，要求越严 → 阈值越低
    assert long <= short
    # 阈值确实落在低尾：按 0.05/sqrt(L) 算出的分位点
    expected = distribution.quantile(0.05 / (10_000_000 ** 0.5))
    assert long == max(0, min(expected, 100))


def test_cutoff_never_exceeds_the_mean_coverage() -> None:
    distribution = CoverageDistribution.from_moments(1.0, 1.0)
    # 尾概率取得很大时，分位点会超过均值；护栏把它压回平均深度
    assert low_coverage_cutoff(distribution, 1, tail_probability=0.9) == 1


def test_cutoff_argument_validation() -> None:
    distribution = CoverageDistribution.from_moments(4.0, 4.0)
    with pytest.raises(ValueError, match="长度必须为正"):
        low_coverage_cutoff(distribution, 0)
    with pytest.raises(ValueError, match="落在 \\(0, 1\\)"):
        low_coverage_cutoff(distribution, 100, tail_probability=1.5)
