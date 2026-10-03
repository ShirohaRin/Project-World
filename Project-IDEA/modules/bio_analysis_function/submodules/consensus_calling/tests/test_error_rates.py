"""错误率重校准的定点用例：伪计数、归一化、gap 类别、排除规则、均匀回退。

参考与 read 都手写，好让每一行的期望概率能手算：

- 某个 (参考碱基, 质量) 行的分母 = 该行**原始计数之和 + 5**（伪计数 1 × 五个 read 类别）；
- 分子 = 该类别的原始计数 + 1。

例如 4 个匹配的 A 观测（质量 40）时，行内总计 4+5=9 → ``P(A|A,40) = 5/9``、其余四类各 ``1/9``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.alignment_io import (
    SamHeader,
    SamRecord,
    parse_cigar,
    write_sam,
)
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.consensus_calling import build_error_rates

#: 32 bp 参考：0-7 A、8-15 C、16-23 G、24-31 T——方便"取某个碱基的窗口"。
_REFERENCE = "AAAAAAAA" + "CCCCCCCC" + "GGGGGGGG" + "TTTTTTTT"
_SEQ_ID = "chr"
_Q40 = "I"  # 73 - 33 = 40
_Q10 = "+"  # 43 - 33 = 10


def _reference(sequence: str = _REFERENCE) -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=_SEQ_ID, sequence=sequence)])


def _record(
    name: str,
    position: int,
    cigar: str,
    sequence: str,
    *,
    qualities: str = "*",
    flag: int = 0,
) -> SamRecord:
    return SamRecord(
        query_name=name,
        flag=flag,
        reference_name=_SEQ_ID,
        position=position,
        mapping_quality=60,
        cigar=parse_cigar(cigar),
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities=qualities,
    )


# ---------------------------------------------------------------------------
# 计数、伪计数与归一化
# ---------------------------------------------------------------------------


def test_all_matching_observations_raise_the_diagonal() -> None:
    rates = build_error_rates(
        _reference(),
        [_record("r", position=1, cigar="4M", sequence="AAAA", qualities=_Q40 * 4)],
    )

    assert rates.summary() == {
        "observations": 4,
        "rows": 1,
        "skipped_reference_ambiguous": 0,
        "skipped_read_ambiguous": 0,
        "skipped_long_deletions": 0,
        "skipped_trimmed": 0,
    }
    # 4 个匹配的 A：P(A|A,40) = (4+1)/(4+5) = 5/9，其余四类各 1/9
    assert rates.probability("A", "A", 40) == pytest.approx(5 / 9)
    assert rates.probability("A", "C", 40) == pytest.approx(1 / 9)
    assert rates.match_probability("A", 40) == pytest.approx(5 / 9)
    assert sum(rates.row("A", 40).values()) == pytest.approx(1.0)


def test_a_single_mismatch_pulls_the_row_towards_that_base() -> None:
    """同一个质量行：4 个匹配 vs 1 个错配——错配把那一行往错配碱基上拉。"""
    matched = build_error_rates(
        _reference(),
        [_record("r", position=1, cigar="4M", sequence="AAAA", qualities=_Q40 * 4)],
    )
    mismatched = build_error_rates(
        _reference(),
        [_record("r", position=1, cigar="1M", sequence="C", qualities=_Q40)],
    )

    assert matched.match_probability("A", 40) > mismatched.match_probability("A", 40)
    # 只有一条错配时：(0+1)/(1+5) = 1/6
    assert mismatched.match_probability("A", 40) == pytest.approx(1 / 6)
    assert mismatched.probability("A", "C", 40) == pytest.approx(1 / 3)


def test_qualities_are_binned_separately() -> None:
    records = [
        _record("high", position=1, cigar="4M", sequence="AAAA", qualities=_Q40 * 4),
        _record("low", position=1, cigar="4M", sequence="CCCC", qualities=_Q10 * 4),
    ]
    rates = build_error_rates(_reference(), records)

    assert rates.rows() == (("A", 10), ("A", 40))
    # 高质量那一列以 A 为主，低质量那一列以 C 为主——同一参考碱基、不同质量互不影响
    assert rates.probability("A", "A", 40) == pytest.approx(5 / 9)
    assert rates.probability("A", "C", 40) == pytest.approx(1 / 9)
    assert rates.probability("A", "C", 10) == pytest.approx(5 / 9)
    assert rates.probability("A", "A", 10) == pytest.approx(1 / 9)


def test_error_rate_is_monotone_in_quality() -> None:
    """质量越低、经验错误率越高——这是这张表存在的意义。"""
    records = [
        # 高质量：10 条里 9 条对上
        *[
            _record(f"h{index}", position=1, cigar="1M", sequence="A" if index < 9 else "C", qualities=_Q40)
            for index in range(10)
        ],
        # 低质量：10 条里 5 条对上
        *[
            _record(f"l{index}", position=1, cigar="1M", sequence="A" if index < 5 else "C", qualities=_Q10)
            for index in range(10)
        ],
    ]
    rates = build_error_rates(_reference(), records)

    assert rates.match_probability("A", 40) == pytest.approx(10 / 15)  # (9+1)/(10+5)
    assert rates.match_probability("A", 10) == pytest.approx(6 / 15)  # (5+1)/(10+5)
    assert rates.probability("A", "C", 40) == pytest.approx(2 / 15)
    assert rates.probability("A", "C", 10) == pytest.approx(6 / 15)
    assert rates.match_probability("A", 40) > rates.match_probability("A", 10)


# ---------------------------------------------------------------------------
# 缺失（gap 类别）与排除规则
# ---------------------------------------------------------------------------


def test_single_base_deletion_enters_the_gap_class_with_the_next_base_quality() -> None:
    # 参考 0,1 是 A；2 被缺失；3,4 是 A → 4 个碱基观测 + 1 个 gap
    record = _record("del", position=1, cigar="2M1D2M", sequence="AAAA", qualities="IIII")
    rates = build_error_rates(_reference(), [record])

    assert rates.summary()["observations"] == 5
    assert rates.summary()["skipped_long_deletions"] == 0
    # 质量取缺失后那个碱基的质量（下标 2 → 40）：(1+1)/(4+1+5) = 1/5
    assert rates.probability("A", "-", 40) == pytest.approx(2 / 10)
    assert rates.probability("A", "A", 40) == pytest.approx(5 / 10)


def test_long_deletions_are_counted_but_never_enter_the_table() -> None:
    """长度 > 1 的缺失属于 junction 那一路，不能算成"单碱基错误率"。"""
    record = _record("long-del", position=1, cigar="2M2D2M", sequence="AAAA", qualities=_Q40 * 4)
    rates = build_error_rates(_reference(), [record])

    assert rates.summary()["skipped_long_deletions"] == 2
    assert rates.summary()["observations"] == 4
    assert rates.probability("A", "-", 40) == pytest.approx(1 / 9)  # 没有 gap 计数，只剩伪计数


def test_ambiguous_reference_and_read_bases_are_skipped_and_counted() -> None:
    reference = _reference("AAANAAAA")
    records = [
        _record("over-n", position=4, cigar="1M", sequence="A", qualities=_Q40),  # 参考碱基是 N
        _record("read-n", position=1, cigar="1M", sequence="N", qualities=_Q40),  # read 碱基是 N
    ]
    rates = build_error_rates(reference, records)

    assert rates.summary() == {
        "observations": 0,
        "rows": 0,
        "skipped_reference_ambiguous": 1,
        "skipped_read_ambiguous": 1,
        "skipped_long_deletions": 0,
        "skipped_trimmed": 0,
    }
    assert rates.rows() == ()


# ---------------------------------------------------------------------------
# 回退与输入校验
# ---------------------------------------------------------------------------


def test_unobserved_row_falls_back_to_a_uniform_distribution() -> None:
    rates = build_error_rates(
        _reference(),
        [_record("r", position=1, cigar="4M", sequence="AAAA", qualities=_Q40 * 4)],
    )

    # (C, 40) 这一行一条观测都没有 → 均匀 1/5，而不是"错误率 0"
    assert rates.probability("C", "A", 40) == pytest.approx(1 / 5)
    assert rates.probability("C", "-", 40) == pytest.approx(1 / 5)
    assert rates.row("C", 40) == dict.fromkeys("ACGT-", pytest.approx(1 / 5))


def test_missing_qualities_use_the_documented_default() -> None:
    record = _record("noqual", position=1, cigar="2M", sequence="AA")
    rates = build_error_rates(_reference(), [record], default_quality=30)

    assert rates.rows() == (("A", 30),)
    assert rates.summary()["observations"] == 2


def test_invalid_queries_are_rejected() -> None:
    rates = build_error_rates(_reference(), [])
    with pytest.raises(ValueError, match="参考碱基"):
        rates.probability("N", "A", 40)
    with pytest.raises(ValueError, match="read 类别"):
        rates.probability("A", "X", 40)
    with pytest.raises(ValueError, match="参考碱基"):
        rates.row("N", 40)


def test_rates_can_be_built_from_a_sam_file(tmp_path: Path) -> None:
    record = _record("file", position=1, cigar="4M", sequence="AAAA", qualities=_Q40 * 4)
    path = tmp_path / "mapped.sam"
    write_sam(path, SamHeader.from_sequences([(_SEQ_ID, len(_REFERENCE))]), [record])

    from_records = build_error_rates(_reference(), [record])
    from_file = build_error_rates(_reference(), path)
    assert from_file.table == from_records.table
    assert from_file.summary() == from_records.summary()
