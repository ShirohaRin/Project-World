"""多态档的定点用例：混合模型的判据、少数派能被报出来、两档不重复报。

为了数字能手算，**建表与判定分成两份数据**（和 `test_consensus.py` 同一套路）：

- *训练*：一条 300 bp 的完全匹配 read（覆盖 0-based 0–299）。参考 400 bp、``ACGT`` 周期，
  所以每种碱基在训练里出现 75 次，于是 ``P(X|X,40) = 76/80 = 0.95``、``P(其它|X,40) = 1/80``，
  多重比较折减 ``log₁₀(400) ≈ 2.602``；
- *判定*：只覆盖某个位置的一批 1 bp read，位置选在**训练覆盖之外**（0-based 320 / 356），
  这样计数里不会混进训练那一条观测。

手算的关键几例：

| 判定数据的构成 | 纯参考解释 | 混合解释 | 结论 |
| --- | --- | --- | --- |
| 8 条少数派 + 12 条参考 | 比值 ≈ 10^−7.5 | ``f̂=0.4``，比值 ≈ 10^9.3 → 打分 ≈ 6.7 | 报多态 |
| 1 条少数派 + 19 条参考 | — | 比值 ≈ 1.8 → 打分 ≈ −2.3 | 不报 |
| 18 条少数派 + 2 条参考 | 比值 ≈ 10^30 → 打分 ≈ 27 | — | 归共识档 |
| 32 条少数派 + 8 条参考 | ``频率=0.8`` → 打分 ≈ 42 | — | 归共识档 |
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.consensus_calling import (
    PolymorphismSettings,
    build_error_rates,
    call_consensus,
    call_polymorphisms,
    call_variants,
    iter_pileup,
    score_polymorphisms,
)

_REFERENCE = "ACGT" * 100  # 400 bp：位置 i 的碱基是 "ACGT"[i % 4]
_SEQ_ID = "chr"
_Q40 = "I"
_TRAINING_LENGTH = 300
#: 判定位置（1-based）：0-based 320 与 356 的参考碱基都是 'A'，且都在训练覆盖之外。
_POSITION = 321
_SECOND_POSITION = 357


def _reference() -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=_SEQ_ID, sequence=_REFERENCE)])


def _record(name: str, position: int, cigar: str, sequence: str, *, qualities: str = "*") -> SamRecord:
    return SamRecord(
        query_name=name,
        flag=0,
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


def _training() -> list[SamRecord]:
    """一条覆盖 0-based 0–299 的完全匹配 read：每种碱基 75 个匹配观测。"""
    return [
        _record(
            "train",
            1,
            f"{_TRAINING_LENGTH}M",
            _REFERENCE[:_TRAINING_LENGTH],
            qualities=_Q40 * _TRAINING_LENGTH,
        )
    ]


def _mixed(bases: str, *, position: int = _POSITION) -> list[SamRecord]:
    """一批只覆盖某个位置的单碱基 read。"""
    return [
        _record(f"r{index}", position, "1M", base, qualities=_Q40)
        for index, base in enumerate(bases)
    ]


def _rates():
    """只用训练数据建表（判定数据不参与校准）。"""
    return build_error_rates(_reference(), _training(), default_quality=0)


def _column(position: int, records: list[SamRecord]):
    return next(
        column
        for column in iter_pileup(_reference(), records, default_quality=0)
        if column.position == position
    )


def test_training_table_is_what_the_hand_calculations_assume() -> None:
    rates = _rates()

    assert rates.probability("A", "A", 40) == pytest.approx(76 / 80)
    assert rates.probability("A", "T", 40) == pytest.approx(1 / 80)


# ---------------------------------------------------------------------------
# 混合模型：能报出少数派
# ---------------------------------------------------------------------------


def test_minority_allele_is_called_when_the_mixture_explains_the_data_better() -> None:
    records = _training() + _mixed("T" * 8 + "A" * 12)

    calls = list(call_polymorphisms(_reference(), records, rates=_rates()))

    assert len(calls) == 1
    call = calls[0]
    assert (call.prediction, call.seq_id, call.position) == ("polymorphism", _SEQ_ID, 320)
    assert call.reference_base == "A"
    assert call.call_base == "T"          # 报的是**少数派**碱基
    assert call.variant_reads == 8
    assert call.total_reads == 20
    assert call.frequency == pytest.approx(0.4, abs=0.02)
    assert 6.0 < call.score < 7.5         # 手算 ≈ 6.7（10^9.3 倍的解释力，再减 log₁₀400）
    assert call.likelihood_ratio > call.score


def test_a_single_minority_read_is_not_called() -> None:
    records = _training() + _mixed("T" * 1 + "A" * 19)

    assert list(call_polymorphisms(_reference(), records, rates=_rates())) == []


def test_a_few_minority_reads_out_of_many_are_still_just_noise() -> None:
    """3 条少数派 / 60 条：混合比例只有 5%，打分也过不了。"""
    records = _training() + _mixed("T" * 3 + "A" * 57)

    assert list(call_polymorphisms(_reference(), records, rates=_rates())) == []


def test_positions_with_only_unusable_observations_are_skipped() -> None:
    # 全 N：既不是 ACGT，也就谈不上"哪个碱基变了"
    records = _training() + _mixed("N" * 20)

    assert list(call_polymorphisms(_reference(), records, rates=_rates())) == []


def test_each_candidate_is_scored() -> None:
    """同一位置有两个少数派时，两个候选各得一个打分（都报出来，按打分降序）。"""
    records = _training() + _mixed("T" * 8 + "G" * 8 + "A" * 4)

    scores = score_polymorphisms(_column(320, records), _rates())

    assert {score.call_base for score in scores} == {"T", "G"}
    assert all(score.variant_reads == 8 for score in scores)
    assert scores[0].likelihood_ratio >= scores[1].likelihood_ratio


# ---------------------------------------------------------------------------
# 与共识档的分工：不重复报、不丢信息
# ---------------------------------------------------------------------------


def test_consensus_tier_ignores_a_minority_allele() -> None:
    """少数派在共识档里整个消失——这正是需要多态档的理由。"""
    records = _training() + _mixed("T" * 8 + "A" * 12)
    rates = _rates()

    assert list(call_consensus(_reference(), records, rates=rates)) == []
    assert len(list(call_polymorphisms(_reference(), records, rates=rates))) == 1


def test_fixed_allele_belongs_to_the_consensus_tier_only() -> None:
    """18/20 支持 T：共识档报出，多态档不重复报。"""
    records = _training() + _mixed("T" * 18 + "A" * 2)
    rates = _rates()

    consensus = list(call_consensus(_reference(), records, rates=rates))
    polymorphism = list(call_polymorphisms(_reference(), records, rates=rates))

    assert len(consensus) == 1
    assert (consensus[0].call_base, consensus[0].prediction) == ("T", "consensus")
    assert consensus[0].frequency == pytest.approx(0.9)
    assert consensus[0].consensus_score > 20
    assert polymorphism == []


def test_the_frequency_boundary_is_left_to_the_consensus_tier() -> None:
    """``f̂`` 恰好 0.8 的位置归共识档——多态档一条都不报（这条曾差点变成两边都报）。"""
    records = _training() + _mixed("T" * 32 + "A" * 8)
    rates = _rates()

    consensus = list(call_consensus(_reference(), records, rates=rates))
    polymorphism = list(call_polymorphisms(_reference(), records, rates=rates))

    assert len(consensus) == 1
    assert consensus[0].frequency == pytest.approx(0.8)
    assert polymorphism == []


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------


def test_thresholds_are_respected() -> None:
    records = _training() + _mixed("T" * 8 + "A" * 12)
    rates = _rates()

    # 默认阈值下报得出（8/20 的混合比例是 0.4）
    assert len(list(call_polymorphisms(_reference(), records, rates=rates))) == 1
    # 把下限提到 0.5：0.4 就不过了
    assert (
        list(
            call_polymorphisms(
                _reference(),
                records,
                rates=rates,
                settings=PolymorphismSettings(min_frequency=0.5),
            )
        )
        == []
    )
    # 打分门槛抬高：同样过不了
    assert (
        list(
            call_polymorphisms(
                _reference(),
                records,
                rates=rates,
                settings=PolymorphismSettings(e_value_cutoff=100.0),
            )
        )
        == []
    )
    # 打分门槛降为 0：照样报
    assert (
        len(
            list(
                call_polymorphisms(
                    _reference(),
                    records,
                    rates=rates,
                    settings=PolymorphismSettings(e_value_cutoff=0.0),
                )
            )
        )
        == 1
    )


# ---------------------------------------------------------------------------
# 两档一起跑
# ---------------------------------------------------------------------------


def test_call_variants_returns_both_tiers_sorted_by_position() -> None:
    records = (
        _training()
        + _mixed("T" * 8 + "A" * 12)                      # 0-based 320：混合 → 多态档
        + _mixed("T" * 20, position=_SECOND_POSITION)     # 0-based 356：全变 → 共识档
    )

    calls = list(call_variants(_reference(), records, rates=_rates()))

    assert [(call.position, call.prediction) for call in calls] == [
        (320, "polymorphism"),
        (356, "consensus"),
    ]
    assert [call.call_base for call in calls] == ["T", "T"]


def test_call_variants_can_skip_the_polymorphism_tier() -> None:
    records = (
        _training()
        + _mixed("T" * 8 + "A" * 12)
        + _mixed("T" * 20, position=_SECOND_POSITION)
    )

    calls = list(call_variants(_reference(), records, rates=_rates(), polymorphism=None))

    assert [(call.position, call.prediction) for call in calls] == [(356, "consensus")]
