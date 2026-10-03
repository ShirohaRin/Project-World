"""公共层序列工具的测试。"""

from __future__ import annotations

from modules.bio_analysis_function.common.sequences import (
    complement,
    count_mismatches,
    count_mismatches_bounded,
    reverse_complement,
)


def test_complement() -> None:
    """单个碱基的互补，参数与返回值都是 ASCII 字节值（对应 C++ 的 complement_of）。"""
    assert complement(ord("A")) == ord("T")
    assert complement(ord("C")) == ord("G")
    assert complement(ord("G")) == ord("C")
    assert complement(ord("T")) == ord("A")
    # 与 reverse_complement 的口径一致：小写与 N 也认，未知字符原样返回
    assert complement(ord("a")) == ord("t")
    assert complement(ord("N")) == ord("N")
    assert complement(ord("X")) == ord("X")


def test_reverse_complement() -> None:
    assert reverse_complement(b"ACGT") == b"ACGT"
    assert reverse_complement(b"AAAA") == b"TTTT"
    assert reverse_complement(b"AACCGGTT") == b"AACCGGTT"
    # 反向互补两次回到原串
    sequence = b"ACGTTGCAATCGGATC"
    assert reverse_complement(reverse_complement(sequence)) == sequence


def test_reverse_complement_keeps_unknown_characters() -> None:
    """上游对未知字符不报错、原样保留；这里保持同样的宽松口径。"""
    assert reverse_complement(b"ACGTN") == b"NACGT"
    assert reverse_complement(b"ACGTX") == b"XACGT"


def test_count_mismatches() -> None:
    assert count_mismatches(b"AAAA", b"AAAA", 4) == 0
    assert count_mismatches(b"AAAA", b"AAAT", 4) == 1
    assert count_mismatches(b"AAAA", b"TTTT", 4) == 4
    # 只看前 length 个碱基
    assert count_mismatches(b"AAAACCCC", b"AAAAGGGG", 4) == 0


def test_count_mismatches_bounded_stops_early_but_signals_over_limit() -> None:
    """超过上限时返回一个 > limit 的数（调用方用 <= limit 判断），不保证是精确值。"""
    assert count_mismatches_bounded(b"AAAA", b"AAAA", 4, 2) == 0
    assert count_mismatches_bounded(b"AAAA", b"AAAT", 4, 2) == 1
    over = count_mismatches_bounded(b"AAAA", b"TTTT", 4, 2)
    assert over > 2


def test_count_mismatches_bounded_with_negative_limit() -> None:
    """上限为负数时，任何一条错配都算超限（调用方据此判否）。"""
    assert count_mismatches_bounded(b"AAAA", b"AAAA", 4, -1) == 0
    assert count_mismatches_bounded(b"AAAA", b"AAAT", 4, -1) > -1
