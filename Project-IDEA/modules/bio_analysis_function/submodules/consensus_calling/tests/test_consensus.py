"""共识打分与判定的定点用例：似然比的数值、两个阈值、并列规则、不可判情形。

为了数字能手算，把**建表**与**判定**分成两份数据：

- *训练*数据：一条 996M 的完全匹配 read，覆盖参考的 [4, 1000) —— 于是错误率表里每个
  (碱基, 质量 40) 行都恰好 249 个匹配观测，``P(X|X,40) = 250/254``、``P(其它|X,40) = 1/254``；
- *判定*数据：只覆盖某个位置的一批短 read。

真实流程里这两步用的是**同一份数据**（样本里变异占比 <1‰，污染可忽略）；这里分开纯粹是为了
让期望值可用解析式写出来。用同一份数据自训练的效果另有一条用例专门盯着。
"""

from __future__ import annotations

import math

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.consensus_calling import (
    ConsensusSettings,
    build_error_rates,
    call_consensus,
    iter_pileup,
    score_position,
)

_REFERENCE = "ACGT" * 250  # 1000 bp：四种碱基各 250 次，位置 i 的碱基是 "ACGT"[i % 4]
_SEQ_ID = "chr"
_Q40 = "I"


def _reference(sequence: str = _REFERENCE, seq_id: str = _SEQ_ID) -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=seq_id, sequence=sequence)])


def _record(
    name: str,
    position: int,
    cigar: str,
    sequence: str,
    *,
    qualities: str = "*",
    reference_name: str = _SEQ_ID,
) -> SamRecord:
    return SamRecord(
        query_name=name,
        flag=0,
        reference_name=reference_name,
        position=position,
        mapping_quality=60,
        cigar=parse_cigar(cigar),
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities=qualities,
    )


def _training_records() -> list[SamRecord]:
    """覆盖 [4, 1000) 的一条完全匹配 read：每个 (碱基, 质量) 行恰好 249 个匹配观测。"""
    return [
        _record(
            "train",
            position=5,
            cigar="996M",
            sequence=_REFERENCE[4:],
            qualities=_Q40 * 996,
        )
    ]


def _variant_records(
    position: int, call_base: str, support: int, reference_support: int
) -> list[SamRecord]:
    """在 ``position``（0-based）造一批只覆盖该位置的短 read。"""
    records = [
        _record(f"var-{index}", position=position + 1, cigar="1M", sequence=call_base, qualities=_Q40)
        for index in range(support)
    ]
    records.extend(
        _record(f"ref-{index}", position=position + 1, cigar="1M", sequence=_REFERENCE[position], qualities=_Q40)
        for index in range(reference_support)
    )
    return records


def _clean_rates():
    return build_error_rates(_reference(), _training_records())


# ---------------------------------------------------------------------------
# 打分本身
# ---------------------------------------------------------------------------


def test_likelihood_ratio_matches_the_hand_computed_value() -> None:
    """8 条支持 T、2 条支持参考 C 时，比值应当正好是 6·log₁₀(251)。"""
    records = _variant_records(position=1, call_base="T", support=8, reference_support=2)
    rates = _clean_rates()
    calls = list(call_consensus(_reference(), records, rates=rates))

    assert len(calls) == 1
    call = calls[0]
    assert (call.seq_id, call.position) == (_SEQ_ID, 1)
    assert (call.reference_base, call.call_base) == ("C", "T")
    # 每条 T 观测贡献 log₁₀(P(T|T,40)/P(T|C,40)) = log₁₀(250)；每条 C 观测贡献它的相反数
    assert call.likelihood_ratio == pytest.approx(6 * math.log10(250))
    assert call.consensus_score == pytest.approx(6 * math.log10(250) - math.log10(len(_REFERENCE)))
    assert call.frequency == pytest.approx(0.8)
    assert (call.total_reads, call.variant_reads) == (10, 8)


def test_position_score_exposes_every_candidate_and_keeps_the_reference_at_zero() -> None:
    records = _variant_records(position=1, call_base="T", support=8, reference_support=2)
    column = next(
        column
        for column in iter_pileup(_reference(), records)
        if column.position == 1
    )
    score = score_position(column, _clean_rates())

    assert score is not None
    assert set(score.ratios) == set("ACGT")
    assert score.ratios["C"] == pytest.approx(0.0)  # 参考自己跟自己比
    assert score.best_base == "T"
    assert score.best_ratio == score.ratios["T"]
    assert score.is_variant is True
    assert (score.total_reads, score.variant_reads) == (10, 8)
    assert score.frequency == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# 两个阈值
# ---------------------------------------------------------------------------


def test_frequency_cutoff_is_enforced_independently() -> None:
    """同一份数据把频率阈值抬到 0.9，就不再报出——而比值本身没变。"""
    records = _variant_records(position=1, call_base="T", support=8, reference_support=2)
    rates = _clean_rates()

    relaxed = list(call_consensus(_reference(), records, rates=rates))
    strict = list(
        call_consensus(_reference(), records, rates=rates, settings=ConsensusSettings(frequency_cutoff=0.9))
    )
    assert len(relaxed) == 1
    assert strict == []
    assert relaxed[0].frequency == pytest.approx(0.8)  # 频率仍是 0.8，只是没过阈值


def test_e_value_cutoff_is_enforced_independently() -> None:
    records = _variant_records(position=1, call_base="T", support=8, reference_support=2)
    rates = _clean_rates()
    score = 6 * math.log10(250) - 3  # ≈ 11.39

    assert len(list(call_consensus(_reference(), records, rates=rates, settings=ConsensusSettings(e_value_cutoff=11.0)))) == 1
    assert list(call_consensus(_reference(), records, rates=rates, settings=ConsensusSettings(e_value_cutoff=11.5))) == []
    assert score == pytest.approx(11.3876, abs=1e-3)


# ---------------------------------------------------------------------------
# 不报的情形
# ---------------------------------------------------------------------------


def test_matching_positions_are_never_called() -> None:
    """只有完全匹配的数据时：一条都不报。"""
    rates = _clean_rates()
    assert list(call_consensus(_reference(), _training_records(), rates=rates)) == []
    # 逐位置看：最佳碱基就是参考碱基，比值为 0
    column = next(iter(iter_pileup(_reference(), _training_records())))
    score = score_position(column, rates)
    assert score is not None
    assert score.is_variant is False
    assert score.best_base == score.reference_base
    assert score.best_ratio == pytest.approx(0.0)


def test_uniform_table_reports_nothing() -> None:
    """表里一个观测都没有（所有行都是均匀分布）时全部并列 → 保留参考、不报。

    这同时说明 ``rates`` 参数**确实被用上了**：换一张空表，结果就变了。
    """
    empty_rates = build_error_rates(_reference(), [])
    records = _variant_records(position=1, call_base="T", support=20, reference_support=0)

    assert list(call_consensus(_reference(), records, rates=empty_rates)) == []
    # 同样的数据配一张训练好的表，就能报出来（说明差异只来自表）
    assert len(list(call_consensus(_reference(), records, rates=_clean_rates()))) == 1


def test_undecidable_positions_return_none() -> None:
    # 参考碱基是 N：无从定义"与参考不同"
    ambiguous_reference = _reference("AAANAAAA")
    record = _record("over-n", position=4, cigar="1M", sequence="A", qualities=_Q40)
    column = next(iter(iter_pileup(ambiguous_reference, [record])))
    assert score_position(column, _clean_rates()) is None

    # 该位置只有 N 观测：没有可判读的碱基
    record = _record("read-n", position=2, cigar="1M", sequence="N", qualities=_Q40)
    column = next(iter(iter_pileup(_reference(), [record])))
    assert score_position(column, _clean_rates()) is None


# ---------------------------------------------------------------------------
# 顺序、多位置、以及"同一份数据自训练"
# ---------------------------------------------------------------------------


def test_multiple_calls_come_out_sorted_by_position() -> None:
    records = [
        *_variant_records(position=1, call_base="T", support=8, reference_support=2),  # C→T
        *_variant_records(position=6, call_base="A", support=8, reference_support=2),  # G→A
    ]
    calls = list(call_consensus(_reference(), records, rates=_clean_rates()))

    assert [(call.position, call.reference_base, call.call_base) for call in calls] == [
        (1, "C", "T"),
        (6, "G", "A"),
    ]


def test_a_generator_source_is_materialized_for_the_two_pass_run() -> None:
    """``rates`` 不给时要扫两遍——迭代器必须先被定住，否则第二遍什么都读不到。

    这里刻意让**同一份数据自训练**：30 条变异 read、没有反向证据，变异自身对错误率表的
    污染（把 P(变异碱基|参考碱基) 抬高）不足以压过阈值，所以仍然应当报出。
    """
    records = [
        *_training_records(),
        *_variant_records(position=1, call_base="T", support=30, reference_support=0),
    ]
    calls = list(call_consensus(_reference(), (record for record in records)))

    assert len(calls) == 1
    assert calls[0].position == 1
    assert calls[0].frequency == pytest.approx(1.0)
    # 自训练时 (C,40) 行被污染成 C:249 / T:30 → 比值 = 30·log₁₀(250·284/(254·31))
    expected_ratio = 30 * (math.log10(250 / 254) - math.log10(31 / 284))
    assert calls[0].likelihood_ratio == pytest.approx(expected_ratio)
    assert calls[0].consensus_score == pytest.approx(expected_ratio - 3)


def test_contaminated_table_can_swallow_a_weak_variant() -> None:
    """同一份数据自训练时的**已知现象**：变异只占少数时会被自己的污染压住。

    8 条变异 + 2 条参考：变异观测把 ``P(T|C,40)`` 从 1/254 抬到 9/264，比值掉到 ≈6.9，
    分数 ≈3.9 < 10 → 不报。真实数据里变异占比 <1‰、每行的观测数以十万计，这种污染可忽略；
    但在小样本上它是真的存在的——所以这里用测试钉住，免得以后被当成 bug。
    """
    records = [
        *_training_records(),
        *_variant_records(position=1, call_base="T", support=8, reference_support=2),
    ]
    assert list(call_consensus(_reference(), records)) == []
