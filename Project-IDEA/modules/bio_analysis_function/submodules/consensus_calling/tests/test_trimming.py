"""read 端裁剪的定点用例 + 两条端到端验证。

裁剪表的期望值全部能手算（参考取 `TTAAAAGGG`、`TTAGAGAGCC` 这类小序列），
堆叠/判定的用例则验证"被裁的碱基不进证据"这件事真的生效，最后一条端到端把
"已知突变集合 → 检出集合"逐项对拍。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.consensus_calling import (
    ConsensusSettings,
    ReferenceTrimmer,
    build_error_rates,
    build_trimming,
    call_consensus,
    iter_pileup,
)

_SEQ_ID = "chr"
_Q40 = "I"
#: 四个 8 bp 的同聚物块：任何位置都在一个重复区间里，便于手算两端要裁多少。
_HOMOPOLYMER = "AAAAAAAA" + "CCCCCCCC" + "GGGGGGGG" + "TTTTTTTT"


def _reference(sequence: str) -> ReferenceSet:
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


def _random_reference(length: int, seed: int) -> str:
    return "".join(random.Random(seed).choices("ACGT", k=length))


def _training_records(sequence: str) -> list[SamRecord]:
    """覆盖参考 [4, 末尾) 的一条完全匹配 read——用来把错误率表校准到"干净"状态。

    刻意跳过前 4 个碱基：这样 0–3 这几个位置不会凭空多出一条"支持参考"的观测，
    可以拿来构造"证据只来自测试数据"的用例；其余位置仍有它贡献的一条参考观测
    （真实数据里绝大多数 read 本来就支持参考，这不算作弊）。
    """
    return [
        _record(
            "train",
            position=5,
            cigar=f"{len(sequence) - 4}M",
            sequence=sequence[4:],
            qualities=_Q40 * (len(sequence) - 4),
        )
    ]


# ---------------------------------------------------------------------------
# 裁剪表本身
# ---------------------------------------------------------------------------


def test_homopolymer_trims_reach_the_run_ends() -> None:
    """`TTAAAAGGG`：左边 TT、中间 AAAA、右边 GGG——裁到各自重复区的那一端。"""
    trimmer = ReferenceTrimmer("TTAAAAGGG")

    # 位置 0（第一个 T）：读段从这里开始，前 2 个碱基都在 TT 里
    assert trimmer.left_trim(0) == 2
    assert trimmer.right_trim(0) == 1
    # 位置 2（第一个 A）：向右 4 个 A 都不确定；向左只到 2 自己
    assert trimmer.left_trim(2) == 4
    assert trimmer.right_trim(2) == 1
    # 位置 5（最后一个 A）：对称过来
    assert trimmer.left_trim(5) == 1
    assert trimmer.right_trim(5) == 4
    # 位置 6（第一个 G）：向右 3 个 G 都不确定
    assert trimmer.left_trim(6) == 3
    assert trimmer.right_trim(6) == 1
    # 位置 8（最后一个 G）
    assert trimmer.left_trim(8) == 1
    assert trimmer.right_trim(8) == 3


def test_tandem_repeat_with_two_base_unit() -> None:
    """`TTAGAGAGCC`：中间是三个 AG 拷贝，位置 2 属于区间 [2, 8)。"""
    trimmer = ReferenceTrimmer("TTAGAGAGCC")

    assert trimmer.repeat_extent(2) == (2, 8, 2)
    assert trimmer.left_trim(2) == 6  # 从区间头开始：整段都不确定
    assert trimmer.right_trim(2) == 1
    # 位置 6（最后一个 AG 的 A）：向右只剩 2 个碱基（AG），向左 5 个都在重复里
    assert trimmer.left_trim(6) == 2
    assert trimmer.right_trim(6) == 5
    # 尾部的 CC 自己就是一个周期 1 的重复（两拷贝），另算一个区间
    assert trimmer.repeat_extent(8) == (8, 10, 1)
    assert trimmer.left_trim(8) == 2
    assert trimmer.right_trim(8) == 1


def test_positions_outside_any_repeat_keep_the_minimum_one() -> None:
    trimmer = ReferenceTrimmer("ACGT")
    for position in range(4):
        assert trimmer.left_trim(position) == 1
        assert trimmer.right_trim(position) == 1
        assert trimmer.repeat_extent(position) is None


def test_max_unit_bounds_which_repeats_count() -> None:
    """`ACGACGACG` 是周期 3 的重复：把上限压到 2 就看不见它了。"""
    narrow = ReferenceTrimmer("ACGACGACG", max_unit=2)
    assert narrow.left_trim(0) == 1

    wide = ReferenceTrimmer("ACGACGACG", max_unit=3)
    assert wide.repeat_extent(0) == (0, 9, 3)
    assert wide.left_trim(0) == 9
    assert wide.right_trim(0) == 1


def test_single_copy_is_not_a_repeat() -> None:
    """只出现一次的序列不算重复——否则一次偶然相同的碱基就能凭空造出裁剪。"""
    trimmer = ReferenceTrimmer("ACGTTGCA")
    assert trimmer.repeat_extent(0) is None
    assert trimmer.left_trim(0) == 1


def test_invalid_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="重复长度上限"):
        ReferenceTrimmer("ACGT", max_unit=0)
    with pytest.raises(IndexError, match="越出参考长度"):
        ReferenceTrimmer("ACGT").repeat_extent(4)


def test_build_trimming_makes_one_table_per_sequence() -> None:
    reference = ReferenceSet.of(
        [
            ReferenceSequence(seq_id="chr", sequence=_HOMOPOLYMER),
            ReferenceSequence(seq_id="plasmid", sequence="ACGT"),
        ]
    )
    trimming = build_trimming(reference)

    assert sorted(trimming) == ["chr", "plasmid"]
    assert trimming["chr"].left_trim(0) == 8
    assert trimming["plasmid"].left_trim(0) == 1


# ---------------------------------------------------------------------------
# 堆叠里的裁剪标记
# ---------------------------------------------------------------------------


def test_only_the_read_ends_are_marked_as_trimmed() -> None:
    """20M 从 A 区开头起、读到 G 区里：两端被裁，中间的 C 区照常可用。"""
    reference = _reference(_HOMOPOLYMER)
    trimming = build_trimming(reference)
    record = _record("r", position=1, cigar="20M", sequence=_HOMOPOLYMER[:20], qualities=_Q40 * 20)

    columns = {
        column.position: column
        for column in iter_pileup(reference, [record], trimming=trimming)
    }
    for position in range(20):
        observation = columns[position].bases[0]
        # 左端裁 8 个（A 区）、右端裁 4 个（从位置 16 起进入 G 区）
        expected = position < 8 or position > 15
        assert observation.trimmed is expected, f"位置 {position} 的裁剪标记不对"
        assert observation.base == _HOMOPOLYMER[position]  # 被裁的碱基照样记下来


def test_without_trimming_no_observation_is_marked() -> None:
    reference = _reference(_HOMOPOLYMER)
    record = _record("r", position=1, cigar="20M", sequence=_HOMOPOLYMER[:20], qualities=_Q40 * 20)

    columns = list(iter_pileup(reference, [record]))
    assert all(not column.bases[0].trimmed for column in columns)


def test_deletion_observations_also_carry_the_flag() -> None:
    reference = _reference(_HOMOPOLYMER)
    trimming = build_trimming(reference)
    # 3M1D2M：整条 read 都在 A 区里，两端都裁 → 连缺失证据一起被标掉
    sequence = _HOMOPOLYMER[:3] + _HOMOPOLYMER[4:6]
    record = _record("del", position=1, cigar="3M1D2M", sequence=sequence, qualities=_Q40 * 5)

    columns = {
        column.position: column
        for column in iter_pileup(reference, [record], trimming=trimming)
    }
    assert columns[3].deletions[0].trimmed is True
    assert all(
        observation.trimmed
        for column in columns.values()
        for observation in column.bases
    )


# ---------------------------------------------------------------------------
# 裁剪对错误率表与判定的影响
# ---------------------------------------------------------------------------


def test_trimmed_observations_stay_out_of_the_error_rate_table() -> None:
    reference = _reference(_HOMOPOLYMER)
    trimming = build_trimming(reference)
    record = _record("r", position=1, cigar="20M", sequence=_HOMOPOLYMER[:20], qualities=_Q40 * 20)

    plain = build_error_rates(reference, [record])
    trimmed = build_error_rates(reference, [record], trimming=trimming)

    assert plain.summary()["observations"] == 20
    assert plain.summary()["skipped_trimmed"] == 0
    assert trimmed.summary()["observations"] == 8  # 只有中间 C 区的 8 个碱基可用
    assert trimmed.summary()["skipped_trimmed"] == 12


def test_trimming_lets_a_real_variant_show_up() -> None:
    """末端落在重复里的"噪声"read 原本会把频率压到阈值之下——裁掉之后变异才浮出来。

    数据：参考是随机序列（基本没有重复，裁剪只作用于 read 自己的首尾），测试位置取 3
    ——**校准用的那条 training read 刻意不覆盖 0–3**，所以这个位置的证据只来自测试数据。
    - 6 条**支持变异**的 read：cigar ``5M``，变异碱基落在 read 的第 3 位（不在被裁的端上）；
    - 4 条**支持参考**的 read：cigar ``1M``，正好只覆盖该位——它们的碱基就是自己左端第 0 位，
      属于"末端落在边界上"的那类证据，裁剪后不参与判定。
    不裁剪：频率 6/10 = 0.6、比值也只剩 2 倍对数，两个条件都过不了；裁剪后：频率 1.0、比值 ≈14 → 报出。
    """
    sequence = _random_reference(1000, seed=20260928)
    reference = _reference(sequence)
    trimming = build_trimming(reference)
    rates = build_error_rates(reference, _training_records(sequence))

    position = 3
    reference_base = sequence[position]
    variant_base = next(base for base in "ACGT" if base != reference_base)

    records = []
    for index in range(6):  # 变异碱基位于 read 的第 3 位
        start = position - 2
        piece = list(sequence[start : start + 5])
        piece[2] = variant_base
        records.append(
            _record(f"var-{index}", position=start + 1, cigar="5M", sequence="".join(piece), qualities=_Q40 * 5)
        )
    for index in range(4):  # 只覆盖这一位，正好是被裁的末端
        records.append(
            _record(
                f"noise-{index}",
                position=position + 1,
                cigar="1M",
                sequence=reference_base,
                qualities=_Q40,
            )
        )

    untrimmed = list(call_consensus(reference, records, rates=rates))
    assert untrimmed == []

    trimmed = list(call_consensus(reference, records, rates=rates, trimming=trimming))
    assert len(trimmed) == 1
    assert (trimmed[0].position, trimmed[0].reference_base, trimmed[0].call_base) == (
        position,
        reference_base,
        variant_base,
    )
    assert trimmed[0].frequency == pytest.approx(1.0)
    assert trimmed[0].total_reads == 6  # 4 条噪声 read 的证据被裁掉了


# ---------------------------------------------------------------------------
# 端到端对拍：已知突变集合 vs 检出集合
# ---------------------------------------------------------------------------


def test_end_to_end_known_substitutions_are_recovered_exactly() -> None:
    """合成"已知替换"的数据集：检出集合与真值集合逐项吻合（精确率与召回率都是 1）。"""
    sequence = _random_reference(2000, seed=20260929)
    reference = _reference(sequence)
    trimming = build_trimming(reference)

    positions = (100, 700, 900)  # 都落在校准 read 覆盖的区间里
    truth = {
        position: next(base for base in "ACGT" if base != sequence[position])
        for position in positions
    }
    records = _training_records(sequence)
    for position, variant_base in truth.items():
        start = position - 2  # 变异碱基落在 read 的第 3 位，避开被裁的两端
        piece = list(sequence[start : start + 5])
        piece[2] = variant_base
        for index in range(20):
            records.append(
                _record(
                    f"var-{position}-{index}",
                    position=start + 1,
                    cigar="5M",
                    sequence="".join(piece),
                    qualities=_Q40 * 5,
                )
            )

    calls = list(
        call_consensus(
            reference,
            records,
            settings=ConsensusSettings(),
            trimming=trimming,
        )
    )

    detected = {call.position: call.call_base for call in calls}
    assert detected == truth  # 既不许漏（召回 1），也不许多报（精确 1）
    for call in calls:
        assert call.reference_base == sequence[call.position]
        assert call.variant_reads == 20
        # 每个位置的证据 = 20 条变异 read + 校准 read 贡献的一条参考观测
        assert call.total_reads == 21
        assert call.frequency == pytest.approx(20 / 21)
        assert call.consensus_score >= 10
