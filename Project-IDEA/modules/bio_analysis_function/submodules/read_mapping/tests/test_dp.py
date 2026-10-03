"""带内比对的确定性用例。

这一层的每个数字都能手算，所以期望值全部写死：CIGAR 的形态、错配/插入/缺失的条数、
参考覆盖长度。并列时的取舍（同聚物里空位放哪边）也有测试钉住——**不能是"随便"**。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.alignment_io import Cigar
from modules.bio_analysis_function.submodules.read_mapping import (
    BandedAlignment,
    align_banded,
)


def _random_window(length: int = 30, seed: int = 20260923) -> str:
    """固定种子的随机参考窗口——用于"只能有一种解释"的 indel 用例。"""
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT") for _ in range(length))


def _distinct_base(*bases: str) -> str:
    """取一个与给定碱基都不同的碱基，避免插入/删除落在同聚物里产生歧义。"""
    for candidate in "ACGT":
        if candidate not in bases:
            return candidate
    raise AssertionError("没有可用的碱基。")


def _align(
    query: str,
    window: str,
    *,
    window_start: int = 0,
    center: int = 0,
    max_indel: int = 3,
    max_clip: int = 0,
    min_aligned_length: int = 0,
) -> BandedAlignment | None:
    return align_banded(
        query,
        window,
        window_start=window_start,
        center=center,
        max_indel=max_indel,
        max_clip=max_clip,
        min_aligned_length=min_aligned_length,
    )


def test_identical_sequences_align_as_one_block() -> None:
    result = _align("ACGTACGTAC", "ACGTACGTAC")
    assert result is not None
    assert str(result.cigar) == "10M"
    assert result.mismatches == 0
    assert result.insertions == 0
    assert result.deletions == 0
    assert result.reference_start == 0
    assert result.reference_length == 10
    assert result.query_length == 10
    assert result.edit_distance == 0


def test_single_substitution_in_the_middle_is_counted_not_gapped() -> None:
    result = _align("AAACGCGG", "AAACCCGG")
    assert result is not None
    assert str(result.cigar) == "8M"
    assert result.mismatches == 1
    assert result.insertions == 0 and result.deletions == 0
    assert result.edit_distance == 1


def test_trailing_base_is_kept_as_mismatch_when_clipping_is_off() -> None:
    """不剪裁时，结尾一个错配留着（``9M`` 一分不多不少，比记成插入更划算）。"""
    result = _align("AAACCCGGT", "AAACCCGGG")
    assert result is not None
    assert str(result.cigar) == "9M"
    assert result.mismatches == 1
    assert result.insertions == 0
    assert result.edit_distance == 1


def test_trailing_base_is_clipped_when_clipping_is_on() -> None:
    """打开剪裁后，末尾那个错配得不偿失（``-1 < 0``），于是被剪掉。"""
    result = _align("AAACCCGGT", "AAACCCGGG", max_clip=1)
    assert result is not None
    assert str(result.cigar) == "8M1S"
    assert result.soft_clipped == 1
    assert result.mismatches == 0
    assert result.aligned_length == 8


def test_reference_has_extra_bases_shows_as_deletion() -> None:
    """read 比参考少两个碱基：CIGAR 里出现 D，且参考覆盖长度大于读长。

    只断言**不变量**，不断言具体写法：两个缺失放在哪里可能有多种等价写法
    （代价相同就是并列），具体取哪一种属于"取舍"，由下面的 tie-break 用例单独钉。
    """
    window = _random_window()
    query = window[:10] + window[12:]
    result = _align(query, window)
    assert result is not None
    assert result.deletions == 2
    assert result.mismatches == 0
    assert result.insertions == 0
    assert result.edit_distance == 2
    assert result.reference_length == 30
    assert result.query_length == 28
    assert str(result.cigar).count("D") == 2


def test_read_has_extra_base_shows_as_insertion() -> None:
    """read 比参考多一个碱基；插入的碱基刻意与左右邻居都不同，保证解释唯一。"""
    window = _random_window()
    inserted = _distinct_base(window[9], window[10])
    query = window[:10] + inserted + window[10:]
    result = _align(query, window)
    assert result is not None
    assert str(result.cigar) == "10M1I20M"
    assert result.insertions == 1
    assert result.mismatches == 0
    assert result.deletions == 0
    assert result.query_length == 31
    assert result.reference_length == 30


def test_reference_start_is_free_so_alignment_can_begin_later() -> None:
    """参考端自由：期望对角线落在窗口中间时，对齐从那里开始，不吃前面的碱基。"""
    result = _align("AAACCC", "TTTTAAACCC", window_start=1000, center=4)
    assert result is not None
    assert str(result.cigar) == "6M"
    assert result.mismatches == 0
    assert result.reference_start == 1004  # 窗口内偏移 4 被加回绝对坐标
    assert result.reference_length == 6


def test_reference_end_is_free_so_alignment_can_stop_early() -> None:
    result = _align("AAACCC", "AAACCCTTTT")
    assert result is not None
    assert str(result.cigar) == "6M"
    assert result.reference_start == 0
    assert result.reference_length == 6


def test_indel_beyond_the_band_is_unreachable() -> None:
    """6 个插入碱基、带宽只有 1 时，带内没有任何可行路径。"""
    assert _align("AAACCCGGGGGG", "AAACCC", max_indel=1) is None


def test_center_off_the_window_start_is_respected() -> None:
    """期望对角线在窗口中间时，坐标要按窗口偏移算对。"""
    window = "TTTTAAACCC"
    result = _align("AAACCC", window, window_start=500, center=4)
    assert result is not None
    assert result.reference_start == 504


def test_alignment_is_deterministic() -> None:
    first = _align("AAACGGG", "AAACCCGGG")
    second = _align("AAACGGG", "AAACCCGGG")
    assert first == second


def test_homopolymer_gap_ambiguity_is_resolved_deterministically() -> None:
    """同聚物里"少一个碱基"有多种等价写法（空位放左边/右边、或起点挪一位配一个错配）。

    这里不假装只有一种正确写法，只钉两件真正要紧的事：

    1. **代价是对的**：无论取哪种写法，编辑距离都必须是 1（真实差异就是一个碱基）；
    2. **取舍是确定的**：同一份输入每次跑出来完全一样，覆盖区间也落在应有的位置上。
    """
    tail = _random_window(10, seed=7)
    window = "GGG" + "AAAA" + tail
    query = "GGG" + "AAA" + tail
    first = _align(query, window)
    second = _align(query, window)
    assert first is not None
    assert first == second
    assert first.edit_distance == 1
    assert first.query_length == len(query)
    assert first.reference_start in (0, 1)
    assert first.reference_start + first.reference_length in (len(window) - 1, len(window))
    assert first.mismatches + first.insertions + first.deletions == 1


def test_result_validates_cigar_consistency() -> None:
    with pytest.raises(ValueError):
        BandedAlignment(
            reference_start=0,
            reference_length=10,
            query_length=5,
            mismatches=0,
            insertions=0,
            deletions=0,
            cigar=Cigar(),  # 空 CIGAR 消费 0，与声明的长度不符
        )


# ---------------------------------------------------------------------------
# 末端处理（软剪裁）
# ---------------------------------------------------------------------------


def test_foreign_prefix_is_soft_clipped_when_enabled() -> None:
    """一端整段对不上（接头读通）时：剪成 S，参考起点跟着推到真正对得上的位置。

    造法：前缀 ``GGGGG`` 与参考对应位置 ``TTTTT`` 逐个不同，于是这 5 个碱基
    无论记成错配还是插入代价都是 5；打开末端处理后它们应当被剪掉。
    """
    window = "TTTTTACGTACGTAC"  # 15
    query = "GGGGGACGTACGTAC"  # 15
    result = _align(query, window, window_start=1000, max_clip=5)
    assert result is not None
    assert str(result.cigar) == "5S10M"
    assert result.soft_clipped == 5
    assert result.aligned_length == 10
    assert result.mismatches == 0
    assert result.reference_start == 1005  # 5 个被剪掉的碱基仍然占过参考位置
    assert result.reference_length == 10


def test_soft_clipping_is_off_by_default() -> None:
    """``max_clip=0``（默认）时行为与分片 C 完全一致：不剪，末端只能被吸收。"""
    result = _align("GGGGGACGTACGTAC", "TTTTTACGTACGTAC", window_start=1000)
    assert result is not None
    assert result.soft_clipped == 0
    assert "S" not in str(result.cigar)


def test_soft_clip_respects_the_limit() -> None:
    """每端最多剪 ``max_clip`` 个：上限设小了就只剪那么多个。"""
    window = "TTTTTACGTACGTAC"
    query = "GGGGGACGTACGTAC"
    limited = _align(query, window, window_start=0, max_clip=3)
    assert limited is not None
    assert limited.soft_clipped == 3
    full = _align(query, window, window_start=0, max_clip=5)
    assert full is not None
    assert full.soft_clipped == 5


def test_interior_mismatch_is_not_clipped() -> None:
    """内部的错配**不能**被剪掉——真实变异位点大多在 read 中间。"""
    window = "ACGTACGTACGTACGT"
    query = "ACGTACGAACGTACGT"  # 第 8 位由 T 变 A
    result = _align(query, window, window_start=0, max_clip=10)
    assert result is not None
    assert result.soft_clipped == 0
    assert result.mismatches == 1
    assert str(result.cigar) == "16M"


def test_min_aligned_length_limits_trimming() -> None:
    """剪完必须留下足够长的对齐块：留不够就不剪那么多。"""
    window = "TTTTTACGTACGTAC"
    query = "GGGGGACGTACGTAC"
    result = _align(query, window, window_start=0, max_clip=5, min_aligned_length=12)
    assert result is not None
    assert result.soft_clipped == 3  # 15 - 12 = 3，再多就低于底线
    assert result.aligned_length == 12


@pytest.mark.parametrize("query,window", [("", "ACGT"), ("ACGT", "")])
def test_empty_input_returns_none(query: str, window: str) -> None:
    assert _align(query, window) is None


def test_negative_band_is_rejected() -> None:
    with pytest.raises(ValueError):
        _align("ACGT", "ACGT", max_indel=-1)
