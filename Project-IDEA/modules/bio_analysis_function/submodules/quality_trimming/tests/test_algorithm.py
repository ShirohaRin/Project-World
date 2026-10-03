"""quality_trimming 核心算法的确定性测试。

测试策略分三层：

1. **上游对齐**：直接使用 fastp ``src/filter.cpp`` 的 ``Filter::test()`` 自带向量，
   逐位比对序列与质量结果。这是"实现正确"的最强证据。
2. **语义用例**：分别覆盖 cut_front / cut_right / cut_tail、N 剥离、丢弃条件，
   每个用例都手工推导出期望值并在注释里写明推导过程。
3. **实现交叉验证**：滚动求和是本算法最容易写出差一错误的地方，
   因此另写一份"每步重新求和"的朴素实现，用随机数据对比两者。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.submodules.quality_trimming.algorithm import (
    QualityCutConfig,
    _scan_backward,
    _scan_forward,
    trim_and_cut,
)

_HIGH = b"I"  # ASCII 73 -> Q40
_LOW = b"#"   # ASCII 35 -> Q2
_FAIL = b"!"  # ASCII 33 -> Q0


def _naive_scan_forward(
    quality: bytes,
    start: int,
    last_start: int,
    window: int,
    threshold: int,
    *,
    want_low: bool,
) -> tuple[bool, int]:
    """朴素版正向扫描：每个窗口重新求和，不做滚动更新。"""
    for s in range(start, last_start + 1):
        total = sum(quality[s : s + window])
        hit = total < threshold if want_low else total >= threshold
        if hit:
            return True, s
    return False, last_start + 1


def _naive_scan_backward(
    quality: bytes,
    first_end: int,
    last_end: int,
    window: int,
    threshold: int,
) -> tuple[bool, int]:
    """朴素版反向扫描：窗口用终点表示。"""
    for t in range(first_end, last_end - 1, -1):
        total = sum(quality[t - window + 1 : t + 1])
        if total >= threshold:
            return True, t
    return False, last_end - 1


# --------------------------------------------------------------------------
# 第一层：与上游 fastp 的官方测试向量逐位对齐
# --------------------------------------------------------------------------


def test_matches_fastp_official_test_vector() -> None:
    """复现 fastp `src/filter.cpp` 中 `Filter::test()` 的向量。

    上游原文：

        Read r("@name",
            "TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTT",
            "+",
            "/////CCCCCCCCCCCC////CCCCCCCCCCCCCC////E");
        opt.qualityCut.enabledFront = true;
        opt.qualityCut.enabledTail = true;
        opt.qualityCut.windowSizeFront = 4;  opt.qualityCut.qualityFront = 20;
        opt.qualityCut.windowSizeTail = 4;   opt.qualityCut.qualityTail = 20;
        Read* ret = filter.trimAndCut(&r, 0, 1, frontTrimmed);

    上游断言的结果是 28 个 C 的序列，以及 "CCCCCCCCCCC////CCCCCCCCCCCCC" 的质量。

    手工推导（窗口=4、阈值 Q20，故阈值和 = 4*(33+20) = 212）：

    - cut_front：从位置 0 起滑，窗口 [3,6] 的和为 47+47+67+67 = 228 >= 212，
      首个达标窗口起点 s=3；切点取 s+window-1 = 6，故 front 变为 6。
    - cut_tail：从右端 t=38 起滑，窗口 [33,36] 的和为 67+67+47+47 = 228 >= 212，
      首个达标窗口终点 t=36；回退到窗口起点 36-3 = 33，故保留到位置 33。
    - 最终长度 = 33 - 6 + 1 = 28。
    """
    sequence = b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTT"
    quality = b"/////CCCCCCCCCCCC////CCCCCCCCCCCCCC////E"
    config = QualityCutConfig(
        enabled_front=True,
        enabled_tail=True,
        window_size_front=4,
        quality_front=20,
        window_size_tail=4,
        quality_tail=20,
    )

    result = trim_and_cut(sequence, quality, front=0, tail=1, config=config)

    assert result is not None
    assert result.sequence == b"C" * 28
    assert result.quality == b"C" * 11 + b"/" * 4 + b"C" * 13
    assert result.front_trimmed == 6


# --------------------------------------------------------------------------
# 第二层：各模式的语义用例
# --------------------------------------------------------------------------


def test_returns_original_when_no_processing_requested() -> None:
    """既没有固定修剪也没有质量剪切时，read 原样返回。"""
    sequence = b"ACGTACGT"
    quality = b"IIIIIIII"

    result = trim_and_cut(sequence, quality)

    assert result is not None
    assert result.sequence == sequence
    assert result.quality == quality
    assert result.front_trimmed == 0


def test_fixed_trimming_without_quality_cut() -> None:
    """只做固定位置修剪：从头部切 2 个、尾部切 3 个。"""
    sequence = b"ACGTACGTAC"
    quality = b"IIIIIIIIII"

    result = trim_and_cut(sequence, quality, front=2, tail=3)

    assert result is not None
    assert result.sequence == b"GTACG"
    assert result.quality == b"IIIII"
    assert result.front_trimmed == 2


def test_tail_cut_removes_low_quality_tail() -> None:
    """cut_tail：去掉 3' 端低质量区。

    构造 20bp：前 12 个 Q40、后 8 个 Q2。窗口 4、阈值 Q20（阈值和 212）。

    从右端终点 t=19 起扫：
        [16,19] 到 [12,15] 的和均为 35*4 = 140 < 212，均不达标；
        t=14 时窗口 [11,14] 的和为 73+35+35+35 = 178 < 212，仍不达标；
        t=13 时窗口 [10,13] 的和为 73+73+35+35 = 216 >= 212，命中。
    命中窗口终点为 13，回退到窗口起点 13-3 = 10，保留 0..10 共 11 个碱基。

    注意这里比"真实分界"（前 12 个好碱基）少保留了 1 个：
    这是算法的固有保守性——只要窗口整体达标就保留到窗口起点，
    不保证每个保留的碱基单独达标。
    """
    sequence = b"C" * 20
    quality = _HIGH * 12 + _LOW * 8
    config = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20)

    result = trim_and_cut(sequence, quality, config=config)

    assert result is not None
    assert result.sequence == b"C" * 11
    assert result.quality == _HIGH * 11


def test_front_cut_removes_low_quality_head() -> None:
    """cut_front：去掉 5' 端低质量区。与上一个用例镜像对称。

    20bp：前 8 个 Q2、后 12 个 Q40。从 s=0 起扫：
        [0,3] 到 [4,7] 的和均为 35*4 = 140 < 212；
        s=5 时窗口 [5,8] 的和为 35+35+35+73 = 178 < 212；
        s=6 时窗口 [6,9] 的和为 35+35+73+73 = 216 >= 212，命中。
    切点取 6+4-1 = 9，从头切掉 9 个，保留 11 个。
    """
    sequence = b"C" * 20
    quality = _LOW * 8 + _HIGH * 12
    config = QualityCutConfig(enabled_front=True, window_size_front=4, quality_front=20)

    result = trim_and_cut(sequence, quality, config=config)

    assert result is not None
    assert result.sequence == b"C" * 11
    assert result.quality == _HIGH * 11
    assert result.front_trimmed == 9


def test_right_cut_is_more_aggressive_than_tail_cut() -> None:
    """同一个输入下 cut_right 比 cut_tail 激进得多。

    构造一个"中间塌陷、后面又好回来"的质量曲线（20bp）：
        0-9  Q40
        10-13 Q2   <- 塌陷段
        14-19 Q40
    这正是区分两种模式的场景。

    cut_tail 从右端扫，第一个窗口 [16,19] 的和就是 73*4 = 292 >= 212，
    立即命中且终点等于最后一个位置，于是**完全不切**，保留 20bp。

    cut_right 从左端扫，直到 s=9 时窗口 [9,12] 的和为 73+35+35+35 = 178 < 212 才命中；
    再保留该窗口内仍达标的单碱基前缀：q[9]=73 >= 53 保留，q[10]=35 < 53 停止，
    切点为 10，故只保留 10bp。
    """
    sequence = b"C" * 20
    quality = _HIGH * 10 + _LOW * 4 + _HIGH * 6

    tail_result = trim_and_cut(
        sequence,
        quality,
        config=QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20),
    )
    right_result = trim_and_cut(
        sequence,
        quality,
        config=QualityCutConfig(
            enabled_right=True, window_size_right=4, quality_right=20
        ),
    )

    assert tail_result is not None and len(tail_result.sequence) == 20
    assert right_result is not None and len(right_result.sequence) == 10
    assert right_result.sequence == b"C" * 10


def test_right_cut_takes_precedence_over_tail_cut() -> None:
    """两种模式同时开启时只执行 cut_right，这是上游的明确行为。"""
    sequence = b"C" * 20
    quality = _HIGH * 10 + _LOW * 4 + _HIGH * 6
    both_config = QualityCutConfig(
        enabled_right=True,
        enabled_tail=True,
        window_size_right=4,
        quality_right=20,
        window_size_tail=4,
        quality_tail=20,
    )

    both = trim_and_cut(sequence, quality, config=both_config)
    right_only = trim_and_cut(
        sequence,
        quality,
        config=QualityCutConfig(
            enabled_right=True, window_size_right=4, quality_right=20
        ),
    )

    assert both is not None and right_only is not None
    assert both.sequence == right_only.sequence
    assert len(both.sequence) == 10


def test_strips_leading_n_after_front_cut() -> None:
    """cut_front 命中后要顺带剥掉前导 N。

    这里构造"高质量 N"：质量全为 Q40，但前 6 个碱基是 N。
    从 s=0 起第一个窗口 [0,3] 的和即 73*4 = 292 >= 212，命中于 s=0；
    由于 s 不大于 0，不执行切窗，front 仍为 0；
    随后剥离前导 N 把 front 推到 6，最终保留 14 个 C。
    """
    sequence = b"N" * 6 + b"C" * 14
    quality = _HIGH * 20
    config = QualityCutConfig(enabled_front=True, window_size_front=4, quality_front=20)

    result = trim_and_cut(sequence, quality, config=config)

    assert result is not None
    assert result.sequence == b"C" * 14
    assert result.front_trimmed == 6


def test_strips_trailing_n_after_tail_cut() -> None:
    """cut_tail 命中后要顺带剥掉尾部 N。"""
    sequence = b"C" * 14 + b"N" * 6
    quality = _HIGH * 20
    config = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20)

    result = trim_and_cut(sequence, quality, config=config)

    assert result is not None
    assert result.sequence == b"C" * 14


def test_drops_read_when_fixed_trimming_exceeds_length() -> None:
    """固定修剪量超过读长时丢弃。10 - 6 - 6 = -2 < 0。"""
    assert trim_and_cut(b"C" * 10, _HIGH * 10, front=6, tail=6) is None


def test_drops_read_when_window_does_not_fit() -> None:
    """可用长度放不下一个窗口时丢弃。10 - 0 - 0 - 10 = 0 <= 0。"""
    config = QualityCutConfig(enabled_tail=True, window_size_tail=10)

    assert trim_and_cut(b"C" * 10, _HIGH * 10, config=config) is None


def test_drops_read_when_right_cut_yields_empty() -> None:
    """cut_right 在整条 read 都不达标时会把长度切成 0，此时丢弃。

    质量全为 Q2：第一个窗口的和 35*4 = 140 < 212 立即命中；
    窗口内第一个碱基也不达标（35 < 53），切点停在起点，剩余长度为 0。
    """
    config = QualityCutConfig(enabled_right=True, window_size_right=4, quality_right=20)

    assert trim_and_cut(b"C" * 20, _LOW * 20, config=config) is None


def test_drops_read_when_front_reaches_last_base() -> None:
    """cut_front 全部窗口都不达标时，front 会走到倒数第二个碱基之后，此时丢弃。

    质量全为 Q2：所有窗口和均为 140 < 212，扫描未命中，
    返回的退出位置为 (length - tail - window) = 16，再加 window-1 得 19，
    恰好等于 length-1，触发丢弃条件。
    """
    config = QualityCutConfig(enabled_front=True, window_size_front=4, quality_front=20)

    assert trim_and_cut(b"C" * 20, _LOW * 20, config=config) is None


def test_all_low_quality_keeps_single_base_for_tail_cut() -> None:
    """cut_tail 全部窗口都不达标时，上游行为是只保留 1 个碱基而不是丢弃。

    质量全为 Q2：反向扫描未命中，退出终点为 front+window-1 = 3，
    回退窗口起点得 3-3 = 0，剩余长度 = 0 - 0 + 1 = 1。
    这个"只留 1 个"的结果看起来奇怪，但它是上游的真实行为，故固定下来。
    """
    config = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20)

    result = trim_and_cut(b"C" * 20, _LOW * 20, config=config)

    assert result is not None
    assert len(result.sequence) == 1


def test_front_cut_then_tail_cut_uses_updated_front() -> None:
    """cut_front 切掉的部分会收窄 cut_tail 的可用范围（两步串行，非并行）。"""
    sequence = b"C" * 30
    # 前 10 个低质量、中间 10 个高质量、末尾 10 个低质量
    quality = _LOW * 10 + _HIGH * 10 + _LOW * 10
    config = QualityCutConfig(
        enabled_front=True,
        enabled_tail=True,
        window_size_front=4,
        quality_front=20,
        window_size_tail=4,
        quality_tail=20,
    )

    result = trim_and_cut(sequence, quality, config=config)

    assert result is not None
    # 两端各切掉一部分，剩余必然短于中间那段高质量区（10bp）
    assert 0 < len(result.sequence) <= 10
    # 切完的序列里不应再出现低质量碱基对应的位置
    assert result.front_trimmed > 0


# --------------------------------------------------------------------------
# 第三层：参数与输入校验
# --------------------------------------------------------------------------


def test_rejects_mismatched_sequence_and_quality_length() -> None:
    with pytest.raises(ValueError, match="长度不一致"):
        trim_and_cut(b"ACGT", b"III")


def test_rejects_non_bytes_input() -> None:
    with pytest.raises(TypeError, match="bytes"):
        trim_and_cut("ACGT", b"IIII")  # type: ignore[arg-type]


def test_rejects_negative_fixed_trimming() -> None:
    with pytest.raises(ValueError, match="不能为负数"):
        trim_and_cut(b"ACGT", b"IIII", front=-1)


def test_rejects_invalid_window_size() -> None:
    with pytest.raises(ValueError, match="必须不小于 1"):
        QualityCutConfig(window_size_front=0)


def test_rejects_out_of_range_quality_threshold() -> None:
    with pytest.raises(ValueError, match="0 到 93"):
        QualityCutConfig(quality_front=94)


# --------------------------------------------------------------------------
# 第三层：滚动求和 vs 朴素求和的随机交叉验证
# --------------------------------------------------------------------------


def test_rolling_scan_forward_matches_naive_implementation() -> None:
    """随机数据下，滚动求和的正向扫描必须与逐步求和的朴素实现完全一致。"""
    rng = random.Random(20260917)
    for _ in range(3000):
        length = rng.randint(5, 200)
        quality = bytes(rng.randrange(33, 74) for _ in range(length))
        window = rng.randint(1, min(12, length))
        threshold = window * (33 + rng.randint(0, 40))
        start = rng.randrange(0, length - window + 1)
        last_start = rng.randrange(start - 1, length - window + 1)

        for want_low in (False, True):
            assert _scan_forward(
                quality, start, last_start, window, threshold, want_low=want_low
            ) == _naive_scan_forward(
                quality, start, last_start, window, threshold, want_low=want_low
            )


def test_rolling_scan_backward_matches_naive_implementation() -> None:
    """反向扫描同样必须与朴素实现一致。"""
    rng = random.Random(20260918)
    for _ in range(3000):
        length = rng.randint(5, 200)
        quality = bytes(rng.randrange(33, 74) for _ in range(length))
        window = rng.randint(1, min(12, length))
        threshold = window * (33 + rng.randint(0, 40))
        first_end = rng.randrange(window - 1, length)
        # last_end 的下界取 window-1：循环体内会访问 quality[t-window+1]，
        # 若 last_end 更小则索引为负，Python 会静默从尾部取值而不是报错。
        # 真实调用路径下 last_end = front + window 且 front >= 0，天然满足该约束，
        # 因此这里按合法范围生成，不把越界行为当作契约。
        last_end = rng.randrange(window - 1, first_end + 2)

        assert _scan_backward(
            quality, first_end, last_end, window, threshold
        ) == _naive_scan_backward(quality, first_end, last_end, window, threshold)
