"""poly_trimming 核心算法的确定性测试。

测试策略：

1. **上游对齐**：直接使用 fastp ``src/polyx.cpp`` 的 ``PolyX::test()`` 自带向量。
2. **语义用例**：polyG 与 polyX 各覆盖"应裁剪""不应裁剪"两侧，
   每个用例都手工推导出期望值并在注释里写明推导过程。
3. **边界**：空序列、全 poly、含 N、阈值不足。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.submodules.poly_trimming.algorithm import (
    PolyTrimConfig,
    trim_poly_g,
    trim_poly_tails,
    trim_poly_x,
)


# --------------------------------------------------------------------------
# 上游对齐
# --------------------------------------------------------------------------


def test_poly_x_matches_fastp_official_test_vector() -> None:
    """复现 fastp `src/polyx.cpp` 中 `PolyX::test()` 的向量。

    上游原文：

        Read r("@name",
            "ATTTTAAAAAAAAAATAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAT",
            "+",
            "///EEEEEEEEEEEEEEEEEEEEEEEEEE////EEEEEEEEEEEEE////E////E");
        PolyX::trimPolyX(&r, &fr, 10);

    上游断言：序列变为 ``ATTTT``，修剪 51 个碱基。

    手工推导（阈值 10，序列长 56）：从右往左扫，前 25 个是 A、接着 1 个 T、
    1 个 C、13 个 A、1 个 T、10 个 A，最后 4 个 T。
    扫描到第 54 个碱基时（此时 A 计 48 个、T 计 5 个），四种碱基的
    "非它数量" 全部超过允许的错配数 5，扫描停止。
    计数最多的是 A，从扫描范围左端向右找到第一个 A 的位置是索引 5，
    于是保留 ``[0, 5)`` 即 ``ATTTT``，切掉 56 - 5 = 51 个。
    """
    sequence = b"ATTTTAAAAAAAAAATAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAT"

    result = trim_poly_x(sequence, min_length=10)

    assert result.sequence == b"ATTTT"
    assert result.trimmed_bases == 51
    assert result.poly_base == b"A"
    assert result.changed


# --------------------------------------------------------------------------
# polyG
# --------------------------------------------------------------------------


def test_poly_g_trims_g_tail() -> None:
    """polyG：20 个 C 之后接 15 个 G，应当截断到位置 20。

    从右往左扫过 15 个 G（`first_g_position` 最终为 20），
    继续扫到第 18 个碱基时错配达到 3，超过当时允许的 2（18/8），
    且已扫长度 17 ≥ 阈值 10，扫描停止；扫过长度 17 ≥ 10，执行裁剪。
    """
    sequence = b"C" * 20 + b"G" * 15

    result = trim_poly_g(sequence, min_length=10)

    assert result.sequence == b"C" * 20
    assert result.trimmed_bases == 15
    assert result.poly_base == b"G"


def test_poly_g_ignores_tail_shorter_than_threshold() -> None:
    """polyG：只有 5 个 G 时不应裁剪。

    扫描到第 10 个碱基时错配为 5，超过允许值 1（10/8）且已扫长度 9 < 阈值 10，
    但此时循环已因错配上限（5）停止，扫过长度 9 < 10，因此不裁剪。
    """
    sequence = b"C" * 30 + b"G" * 5

    result = trim_poly_g(sequence, min_length=10)

    assert result.sequence == sequence
    assert result.trimmed_bases == 0
    assert result.poly_base is None
    assert not result.changed


def test_poly_g_trims_entire_read_when_all_bases_are_g() -> None:
    """整条 read 都是 G：截断到位置 0（因为"最左边的 G"就在开头）。"""
    sequence = b"G" * 20

    result = trim_poly_g(sequence, min_length=10)

    assert result.sequence == b""
    assert result.trimmed_bases == 20


# --------------------------------------------------------------------------
# polyX
# --------------------------------------------------------------------------


def test_poly_x_trims_poly_a_tail() -> None:
    """polyX：10 个 C 之后接 20 个 A，应当截断到位置 10。"""
    sequence = b"C" * 10 + b"A" * 20

    result = trim_poly_x(sequence, min_length=10)

    assert result.sequence == b"C" * 10
    assert result.trimmed_bases == 20
    assert result.poly_base == b"A"


def test_poly_x_counts_n_toward_all_bases() -> None:
    """polyX：N 同时计入四种碱基，因此不会中断扫描，也不会被选为 poly 碱基。

    序列为 10 个 C + 3 个 N + 20 个 T。从右往左扫过 20 个 T、3 个 N
    之后 T 仍以 23 票领先（C 为 7 票，由 3 个 N 各投一票再加 4 个 C 得到），
    最终判定为 polyT，从扫描范围右端向右找到第一个 T 的位置（索引 13）并裁剪。
    """
    sequence = b"C" * 10 + b"N" * 3 + b"T" * 20

    result = trim_poly_x(sequence, min_length=10)

    assert result.sequence == b"C" * 10 + b"N" * 3
    assert result.trimmed_bases == 20
    assert result.poly_base == b"T"


def test_poly_x_ignores_alternating_sequence() -> None:
    """交替序列不构成 poly 尾巴：扫到第 9 个碱基即停止，不足阈值。"""
    sequence = b"ACGTTGCA" * 3

    result = trim_poly_x(sequence, min_length=10)

    assert result.sequence == sequence
    assert result.trimmed_bases == 0


def test_poly_x_trims_entire_read_when_all_bases_are_same() -> None:
    """整条 read 同种碱基时全部切掉。

    这是本实现与上游的**唯一有意差异**：上游在扫描覆盖整条 read 时会访问
    ``c_str()`` 之前的内存（``pos = -1`` 时的 ``data[-1]``），属未定义行为；
    本实现明确取裁剪位置为 0。
    """
    sequence = b"A" * 25

    result = trim_poly_x(sequence, min_length=10)

    assert result.sequence == b""
    assert result.trimmed_bases == 25
    assert result.poly_base == b"A"


# --------------------------------------------------------------------------
# 组合与边界
# --------------------------------------------------------------------------


def test_poly_tails_matches_manual_two_step() -> None:
    """串联执行必须等于"先 polyG 再 polyX"两步手工执行。

    序列为 12 个 A + 12 个 C + 15 个 G（刻意不含歧义碱基）：
    polyG 先切掉 15 个 G、截到位置 24；polyX 在剩下的 12A+12C 上
    再判出 polyC 并切掉 12 个 C，最终只剩 12 个 A。
    这条用例同时说明两点：polyG 与 polyX 会接连生效；
    ``trimmed_bases`` 是两步之和，而 ``poly_base`` 记录的是最后生效那一步的碱基。
    """
    sequence = b"A" * 12 + b"C" * 12 + b"G" * 15
    config = PolyTrimConfig(enabled_poly_g=True, enabled_poly_x=True)

    combined = trim_poly_tails(sequence, config=config)

    step_g = trim_poly_g(sequence, min_length=10)
    step_x = trim_poly_x(step_g.sequence, min_length=10)

    assert step_g.sequence == b"A" * 12 + b"C" * 12
    assert step_g.trimmed_bases == 15
    assert step_x.sequence == b"A" * 12
    assert step_x.trimmed_bases == 12

    assert combined.sequence == step_x.sequence
    assert combined.trimmed_bases == 27
    assert combined.poly_base == b"C"


def test_poly_tails_returns_original_when_nothing_enabled() -> None:
    sequence = b"C" * 10 + b"A" * 20

    result = trim_poly_tails(sequence)

    assert result.sequence == sequence
    assert result.trimmed_bases == 0
    assert result.poly_base is None


def test_poly_tails_applies_only_requested_step() -> None:
    """只开 polyX 时，polyG 不该生效。"""
    sequence = b"C" * 10 + b"G" * 15

    result = trim_poly_tails(sequence, config=PolyTrimConfig(enabled_poly_x=True))

    # 15 个 G 也会被 polyX 判为 polyG 尾巴并切掉，结果与 polyG 相同，
    # 但 poly_base 由 polyX 判定得出。
    assert result.sequence == b"C" * 10
    assert result.poly_base == b"G"


def test_empty_sequence_is_returned_unchanged() -> None:
    for trimmer in (trim_poly_g, trim_poly_x):
        result = trimmer(b"", min_length=10)

        assert result.sequence == b""
        assert result.trimmed_bases == 0
        assert result.poly_base is None


def test_rejects_non_bytes_input() -> None:
    with pytest.raises(TypeError, match="bytes"):
        trim_poly_g("ACGT")  # type: ignore[arg-type]


def test_rejects_invalid_min_length() -> None:
    with pytest.raises(ValueError, match="必须不小于 1"):
        trim_poly_x(b"ACGT", min_length=0)

    with pytest.raises(ValueError, match="必须不小于 1"):
        PolyTrimConfig(min_length_poly_g=0)
