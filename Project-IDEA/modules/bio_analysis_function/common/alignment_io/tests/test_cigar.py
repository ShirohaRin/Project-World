"""CIGAR 的确定性用例：每个操作消费 query 还是消费 reference。

这张"消费表"是 CIGAR 解析的全部要害——写错一个操作，下游所有坐标都会错位，
而且不会报错。所以这里把八种操作逐一钉住，而不是只测两个常用写法。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.alignment_io import (
    AlignmentFormatError,
    Cigar,
    CigarOp,
    parse_cigar,
)


@pytest.mark.parametrize(
    ("text", "query_length", "reference_length"),
    [
        ("100M", 100, 100),
        ("50M10S", 60, 50),
        ("10H50M", 50, 50),
        ("50M5I", 55, 50),
        ("50M5D", 50, 55),
        ("50M10N", 50, 60),
        ("50M5P", 50, 50),
        ("50M5X", 55, 55),
        ("50M5=", 55, 55),
        ("5H20S30M40I50D60N", 90, 140),
    ],
)
def test_cigar_consumption_table(text: str, query_length: int, reference_length: int) -> None:
    cigar = parse_cigar(text)
    assert cigar.query_length == query_length
    assert cigar.reference_length == reference_length


def test_star_and_empty_mean_no_cigar() -> None:
    for text in ("*", "", "   "):
        cigar = parse_cigar(text)
        assert cigar.is_empty is True
        assert cigar.ops == ()
        assert str(cigar) == "*"


def test_clip_flags_distinguish_soft_and_hard() -> None:
    soft = parse_cigar("10S90M")
    hard = parse_cigar("10H90M")
    assert soft.has_soft_clip is True and soft.has_hard_clip is False
    assert hard.has_hard_clip is True and hard.has_soft_clip is False


def test_indel_and_skipped_flags() -> None:
    assert parse_cigar("50M5I").has_indel is True
    assert parse_cigar("50M5D").has_indel is True
    assert parse_cigar("50M").has_indel is False
    assert parse_cigar("50M10N").has_skipped is True
    assert parse_cigar("50M10D").has_skipped is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100M", True),
        ("50M5I", True),
        ("50M5D", True),
        ("10S90M", False),
        ("10H90M", False),
        ("50M10N", False),
        ("50M5X", False),
        ("50M5=", False),
    ],
)
def test_is_simple_covers_m_i_d_only(text: str, expected: bool) -> None:
    assert parse_cigar(text).is_simple is expected


def test_cigar_text_round_trip() -> None:
    for text in ("100M", "10S90M", "5H20S30M40I50D60N", "50M5P"):
        assert str(parse_cigar(text)) == text


def test_cigar_operations_are_in_order() -> None:
    cigar = parse_cigar("10S90M5I")
    assert cigar.ops == (CigarOp(10, "S"), CigarOp(90, "M"), CigarOp(5, "I"))


@pytest.mark.parametrize("text", ["0M", "50M0I", "10Z", "M", "50", "50M10", "50M5IM"])
def test_broken_cigar_is_rejected(text: str) -> None:
    with pytest.raises(AlignmentFormatError):
        parse_cigar(text)


def test_cigar_op_rejects_bad_length_and_operation() -> None:
    with pytest.raises(AlignmentFormatError):
        CigarOp(0, "M")
    with pytest.raises(AlignmentFormatError):
        CigarOp(10, "Z")


def test_empty_cigar_has_zero_lengths() -> None:
    assert Cigar().query_length == 0
    assert Cigar().reference_length == 0
    assert Cigar().is_simple is True
