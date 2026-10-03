"""单条 read 判定的测试。

重点守三类东西：

1. **判定顺序**。上游的几处检查是 ``else if``，只报第一条命中的原因；
   一条 read 同时"低质量 + N 过多"时到底报哪个，是有讲究的。
2. **边界比较的方向**。低质量比例与长度比较都用严格不等号，
   "恰好等于阈值"应当通过——差一位就是成千上万条 read 的去留。
3. **整数除法口径**。平均质量那一处上游是整数除法，小数被丢掉，
   这里用能被精确区分的样例把它固定住。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.submodules.read_filtering.algorithm import (
    FAILURE_LABELS,
    FAIL_COMPLEXITY,
    FAIL_LENGTH,
    FAIL_N_BASE,
    FAIL_QUALITY,
    FAIL_TOO_LONG,
    PASS_FILTER,
    ReadFilterConfig,
    count_quality_metrics,
    filter_verdict,
    passes_filter,
    passes_low_complexity,
    verdict_label,
)

# 三个常用的质量字符：Phred+33 编码。
_Q10 = chr(10 + 33)  # '+'
_Q14 = chr(14 + 33)  # '/'
_Q15 = chr(15 + 33)  # '0'，恰好等于 fastp 的默认达标线
_Q40 = chr(40 + 33)  # 'I'


def quality_string(pattern: str, length: int) -> bytes:
    """把单字符的质量码重复成长度为 length 的质量串。"""
    return (pattern * length).encode("ascii")


# --------------------------------------------------------------------------
# 质量过滤
# --------------------------------------------------------------------------


def test_high_quality_read_passes_with_defaults() -> None:
    sequence = b"ACGT" * 38  # 152 bp
    assert filter_verdict(sequence, quality_string(_Q40, len(sequence))) == PASS_FILTER


def test_low_quality_bases_over_limit_fails() -> None:
    """低质量碱基比例超过上限即失败；恰好等于上限则通过（严格不等号）。"""
    length = 100
    sequence = b"A" * length

    # 41% > 40%（默认上限）→ 失败
    quality = quality_string(_Q40, 59) + quality_string(_Q10, 41)
    assert filter_verdict(sequence, quality) == FAIL_QUALITY

    # 恰好 40% → 通过
    quality = quality_string(_Q40, 60) + quality_string(_Q10, 40)
    assert filter_verdict(sequence, quality) == PASS_FILTER


def test_qualified_quality_boundary_is_strictly_less() -> None:
    """质量字符恰好等于阈值算达标，只有严格小于才算低质量。"""
    length = 100
    sequence = b"A" * length
    config = ReadFilterConfig(unqualified_percent_limit=10)

    # 10 个恰好 Q15 的碱基不算低质量 → 0% → 通过
    quality = quality_string(_Q40, 90) + quality_string(_Q15, 10)
    assert filter_verdict(sequence, quality, config) == PASS_FILTER

    # 11 个 Q14（差一个 Phred）才是低质量 → 11% > 10% → 失败
    quality = quality_string(_Q40, 89) + quality_string(_Q14, 11)
    assert filter_verdict(sequence, quality, config) == FAIL_QUALITY


def test_average_quality_uses_integer_division() -> None:
    """平均质量用整数除法：14.9 会被截成 14，因此没过 15 的线。

    这条不是"实现细节"，而是会让边界 read 的去留产生系统性差异的口径，
    因此用能被精确区分的样例固定住。
    """
    length = 10
    sequence = b"A" * length
    config = ReadFilterConfig(
        average_qual=15,
        unqualified_percent_limit=100,  # 关掉比例这条，专测平均质量
        qualified_quality_phred=0,  # 不做低质量统计的干扰
        required_length=0,  # 关掉长度这条，避免 10bp 被"过短"截胡
    )

    # 总和 149 → 149 // 10 = 14 < 15 → 失败
    quality = quality_string(chr(14 + 33), 9) + quality_string(chr(23 + 33), 1)
    assert filter_verdict(sequence, quality, config) == FAIL_QUALITY

    # 总和 150 → 150 // 10 = 15，不小于 15 → 通过
    quality = quality_string(_Q15, length)
    assert filter_verdict(sequence, quality, config) == PASS_FILTER


def test_average_quality_disabled_when_zero() -> None:
    """average_qual 为 0 表示不设要求，低质量 read 仍要走比例那条判据。"""
    sequence = b"A" * 100
    quality = quality_string(chr(2 + 33), 100)  # 全部 Q2
    assert filter_verdict(sequence, quality, ReadFilterConfig(average_qual=0)) == FAIL_QUALITY


def test_n_base_limit() -> None:
    length = 151
    sequence = b"A" * (length - 6) + b"N" * 6
    assert filter_verdict(sequence, quality_string(_Q40, length)) == FAIL_N_BASE

    sequence = b"A" * (length - 5) + b"N" * 5  # 恰好 5 个不算超
    assert filter_verdict(sequence, quality_string(_Q40, length)) == PASS_FILTER


def test_quality_reason_precedes_n_base_reason() -> None:
    """同时低质量且 N 过多时，报的是质量（上游的 else if 顺序）。"""
    length = 100
    sequence = b"N" * 50 + b"A" * 50
    quality = quality_string(_Q10, 100)  # 100% 低质量

    assert filter_verdict(sequence, quality) == FAIL_QUALITY


def test_n_base_reason_reported_when_quality_is_fine() -> None:
    length = 100
    sequence = b"N" * 50 + b"A" * 50
    assert filter_verdict(sequence, quality_string(_Q40, length)) == FAIL_N_BASE


# --------------------------------------------------------------------------
# 长度过滤
# --------------------------------------------------------------------------


def test_empty_read_always_fails_even_with_all_filters_disabled() -> None:
    """零长度 read 是格式层面的失败，不受任何开关影响。"""
    config = ReadFilterConfig(
        enabled_quality=False, enabled_length=False, enabled_complexity=False
    )
    assert filter_verdict(b"", b"", config) == FAIL_LENGTH


def test_required_length_boundary() -> None:
    config = ReadFilterConfig(required_length=15)

    assert filter_verdict(b"A" * 14, quality_string(_Q40, 14), config) == FAIL_LENGTH
    assert filter_verdict(b"A" * 15, quality_string(_Q40, 15), config) == PASS_FILTER


def test_max_length_boundary() -> None:
    config = ReadFilterConfig(max_length=100)

    assert filter_verdict(b"A" * 101, quality_string(_Q40, 101), config) == FAIL_TOO_LONG
    assert filter_verdict(b"A" * 100, quality_string(_Q40, 100), config) == PASS_FILTER


def test_max_length_zero_means_no_limit() -> None:
    config = ReadFilterConfig(max_length=0)
    assert filter_verdict(b"A" * 5000, quality_string(_Q40, 5000), config) == PASS_FILTER


def test_short_check_precedes_long_check() -> None:
    """两个长度条件同时矛盾时（要求 ≥200 且 ≤100），报"过短"。"""
    config = ReadFilterConfig(required_length=200, max_length=100)
    assert filter_verdict(b"A" * 150, quality_string(_Q40, 150), config) == FAIL_LENGTH


def test_length_check_precedes_complexity() -> None:
    """长度不够时不会走到复杂度判定。"""
    config = ReadFilterConfig(enabled_complexity=True, required_length=15)
    assert filter_verdict(b"A" * 10, quality_string(_Q40, 10), config) == FAIL_LENGTH


# --------------------------------------------------------------------------
# 低复杂度过滤
# --------------------------------------------------------------------------


def test_low_complexity_filter_is_off_by_default() -> None:
    """默认关闭：全同序列（复杂度 0）在默认参数下应当通过。"""
    assert filter_verdict(b"A" * 100, quality_string(_Q40, 100)) == PASS_FILTER


def test_low_complexity_filter_when_enabled() -> None:
    config = ReadFilterConfig(enabled_complexity=True, complexity_threshold=0.3)

    # 全同 → 复杂度 0 → 失败
    assert filter_verdict(b"A" * 100, quality_string(_Q40, 100), config) == FAIL_COMPLEXITY

    # 高低交替 → 复杂度 1.0 → 通过
    assert filter_verdict(b"AT" * 50, quality_string(_Q40, 100), config) == PASS_FILTER


def test_complexity_threshold_boundary() -> None:
    """恰好等于阈值算通过（`>=`）。"""
    # 11 个碱基、10 对相邻、3 对不同 → 3/10 = 0.3 ≥ 0.3 → 通过
    assert passes_low_complexity(b"AAABBBCCCCD", 0.3) is True
    # 11 个碱基、10 对相邻、2 对不同 → 2/10 = 0.2 < 0.3 → 不通过
    assert passes_low_complexity(b"AAAABBBBCCC", 0.3) is False


def test_complexity_of_length_one_is_false() -> None:
    """长度为 1 时分母为 0，上游明确返回 false。"""
    assert passes_low_complexity(b"A", 0.3) is False
    assert passes_low_complexity(b"", 0.3) is False


# --------------------------------------------------------------------------
# 统计口径与参数校验
# --------------------------------------------------------------------------


def test_count_quality_metrics_matches_naive_implementation() -> None:
    """滚动统计与"每步重新数一遍"的朴素实现必须一致。

    随机数据上各跑 500 轮，覆盖含 N、含极低质量、长度悬殊等情况。
    """
    rng = random.Random(20260917)
    for _ in range(500):
        length = rng.randint(1, 200)
        sequence = bytes(rng.choice(b"ACGTN") for _ in range(length))
        quality = bytes(rng.randint(0, 93) + 33 for _ in range(length))
        threshold = rng.randint(0, 93) + 33

        metrics = count_quality_metrics(sequence, quality, qualified_code=threshold)

        assert metrics.low_quality_bases == sum(
            1 for code in quality if code < threshold
        )
        assert metrics.n_bases == sum(1 for base in sequence if base == ord("N"))
        assert metrics.total_quality == sum(code - 33 for code in quality)


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="不一致"):
        filter_verdict(b"ACGT", quality_string(_Q40, 3))


def test_invalid_config_values_raise() -> None:
    with pytest.raises(ValueError, match="qualified_quality_phred"):
        ReadFilterConfig(qualified_quality_phred=94)
    with pytest.raises(ValueError, match="unqualified_percent_limit"):
        ReadFilterConfig(unqualified_percent_limit=101)
    with pytest.raises(ValueError, match="n_base_limit"):
        ReadFilterConfig(n_base_limit=-1)
    with pytest.raises(ValueError, match="complexity_threshold"):
        ReadFilterConfig(complexity_threshold=1.5)


def test_passes_filter_matches_verdict() -> None:
    sequence = b"A" * 100
    assert passes_filter(sequence, quality_string(_Q40, 100)) is True
    assert passes_filter(sequence, quality_string(_Q10, 100)) is False


def test_verdict_labels_match_fastp_names() -> None:
    """标签与上游 ``FAILED_TYPES`` 一致，便于和 fastp 报告对照。"""
    assert verdict_label(PASS_FILTER) == "passed"
    assert verdict_label(FAIL_QUALITY) == "failed_quality_filter"
    assert verdict_label(FAIL_N_BASE) == "failed_too_many_n_bases"
    assert verdict_label(FAIL_LENGTH) == "failed_too_short"
    assert verdict_label(FAIL_TOO_LONG) == "failed_too_long"
    assert verdict_label(FAIL_COMPLEXITY) == "failed_low_complexity"
    assert set(FAILURE_LABELS.values()) == {
        "passed",
        "failed_quality_filter",
        "failed_too_many_n_bases",
        "failed_too_short",
        "failed_too_long",
        "failed_low_complexity",
    }
