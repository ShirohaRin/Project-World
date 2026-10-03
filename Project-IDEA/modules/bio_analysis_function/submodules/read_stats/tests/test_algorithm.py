"""统计累加的测试。

重点守四件容易写错、又不容易被肉眼发现的事：

1. **Q20/Q30 的边界**（恰好等于阈值算达标）、**Q40 的取值区间**；
2. **"低 3 位分桶"**——它让大小写自动合并成同一桶，是上游刻意的技巧；
3. **某位置没有该碱基时，质量曲线取整体均值兜底**，不是记 0；
4. **5-mer 的增量更新**，尤其是遇到 `N` 之后要"从头重算 5 个"那条路径。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.submodules.read_stats import (
    KMER_BUCKETS,
    ReadStatsCollector,
    base_bucket,
    base_code,
    kmer_name,
)

#: Phred 值 → 质量字符（Phred+33）。
def q(phred: int) -> str:
    return chr(phred + 33)


def collect(*reads: tuple[str, str]) -> object:
    collector = ReadStatsCollector()
    for sequence, quality in reads:
        collector.add(sequence.encode("ascii"), quality.encode("ascii"))
    return collector.summarize()


# ---------------------------------------------------------------------------
# 基础量与比例
# ---------------------------------------------------------------------------


def test_basic_counts() -> None:
    summary = collect(("ACGT", q(40) * 4), ("ACGTA", q(40) * 5))

    assert summary.total_reads == 2
    assert summary.total_bases == 9
    assert summary.mean_length == 4  # 整数除法：9 // 2
    assert summary.cycles == 5
    assert summary.length_counts == {4: 1, 5: 1}


def test_empty_input() -> None:
    summary = collect()

    assert summary.total_reads == 0
    assert summary.total_bases == 0
    assert summary.mean_length == 0
    assert summary.cycles == 0
    assert summary.q20_rate == 0.0
    assert summary.gc_content == 0.0
    # 曲线键都在，但都是空序列（cycles 为 0）。
    assert all(curve == () for curve in summary.quality_curves.values())
    assert all(curve == () for curve in summary.content_curves.values())


def test_empty_read_is_counted_but_adds_nothing() -> None:
    summary = collect(("", ""), ("ACGT", q(40) * 4))

    assert summary.total_reads == 2
    assert summary.total_bases == 4
    assert summary.cycles == 4
    assert summary.length_counts == {0: 1, 4: 1}


def test_sequence_and_quality_length_must_match() -> None:
    collector = ReadStatsCollector()
    with pytest.raises(ValueError, match="不一致"):
        collector.add(b"ACGT", b"II")


# ---------------------------------------------------------------------------
# 质量口径
# ---------------------------------------------------------------------------


def test_q20_and_q30_boundaries() -> None:
    """Q20 是 ``>= '5'``、Q30 是 ``>= '?'``；恰好等于算达标。"""
    # 四个碱基分别是 Q19 / Q20 / Q29 / Q30
    summary = collect(("ACGT", q(19) + q(20) + q(29) + q(30)))

    assert summary.q20_bases == 3  # Q20、Q29、Q30 都算 Q20 及以上
    assert summary.q30_bases == 1  # 只有 Q30
    assert summary.q20_bases > summary.q30_bases  # Q30 也计入 Q20


def test_q40_counts_from_q40_up_to_the_ceiling() -> None:
    """Q40 把 Q40~Q93 全加起来（Q39 不算）。"""
    summary = collect(("ACGTAC", q(39) + q(40) + q(41) + q(93) + q(0) + q(20)))

    assert summary.q40_bases == 3  # Q40、Q41、Q93
    assert summary.q40_rate == 0.5


def test_quality_histogram_keys_are_phred_values() -> None:
    summary = collect(("ACGT", q(0) + q(20) + q(20) + q(40)))

    assert summary.quality_histogram == {0: 1, 20: 2, 40: 1}


def test_gc_content_counts_both_g_and_c() -> None:
    summary = collect(("ACGTACGT", q(40) * 8))

    assert summary.gc_bases == 4
    assert summary.gc_content == 0.5


def test_uppercase_and_lowercase_share_a_bucket() -> None:
    """ASCII 的大小写差 32（8 的倍数），因此低 3 位相同、进同一个桶。

    这是上游"按低 3 位分桶"的重要后果：**含量曲线天然合并大小写**。
    """
    assert base_bucket(ord("A")) == base_bucket(ord("a")) == 1
    assert base_bucket(ord("T")) == base_bucket(ord("t")) == 4
    assert base_bucket(ord("C")) == base_bucket(ord("c")) == 3
    assert base_bucket(ord("G")) == base_bucket(ord("g")) == 7
    assert base_bucket(ord("N")) == base_bucket(ord("n")) == 6

    summary = collect(("acgt", q(40) * 4))
    # a 在第 1 位、c 第 2、g 第 3、t 第 4（下标从 0 起）
    assert summary.content_curves["A"][0] == 1.0
    assert summary.content_curves["C"][1] == 1.0
    assert summary.content_curves["G"][2] == 1.0
    assert summary.content_curves["T"][3] == 1.0
    assert summary.gc_content == 0.5


# ---------------------------------------------------------------------------
# 曲线
# ---------------------------------------------------------------------------


def test_quality_curve_is_per_cycle() -> None:
    summary = collect(
        ("ACGT", q(10) + q(20) + q(30) + q(40)),
        ("ACGT", q(20) + q(20) + q(30) + q(40)),
    )

    mean = summary.quality_curves["mean"]
    assert len(mean) == 4
    assert mean[0] == 15.0  # (10+20)/2
    assert mean[1] == 20.0
    assert mean[2] == 30.0
    assert mean[3] == 40.0


def test_base_quality_curve_falls_back_to_overall_mean() -> None:
    """某个位置没有这个碱基时，取**整体均值**——否则曲线会凭空掉到底。"""
    # 第 2 个位置没有 T；该位置整体均值 = (20 + 40) / 2 = 30
    summary = collect(
        ("AC", q(20) + q(40)),
        ("AG", q(20) + q(40)),
    )

    assert summary.content_curves["T"][1] == 0.0
    assert summary.quality_curves["T"][1] == summary.quality_curves["mean"][1]


def test_content_curve_sums_to_one_over_acgtn() -> None:
    summary = collect(("ACGTN", q(40) * 5))

    per_cycle = sum(summary.content_curves[base][0] for base in ("A", "T", "C", "G", "N"))
    assert per_cycle == 1.0
    # 整条 read 的 GC 是 2/5；单个位置的 GC 只会是 0 或 1。
    assert summary.gc_content == 0.4
    assert summary.content_curves["GC"][0] == 0.0
    assert summary.content_curves["GC"][1] == 1.0


def test_short_reads_do_not_add_zero_positions() -> None:
    """读长不一致时，长 read 多出来的位置只有它自己贡献——不该被短 read 拉平。"""
    summary = collect(("AC", q(40) * 2), ("ACGTAC", q(40) * 6))

    assert summary.cycles == 6
    assert summary.total_bases == 8
    assert summary.content_curves["A"][0] == 1.0  # 两条都是 A
    assert summary.content_curves["A"][3] == 0.0  # 第 4 个位置只有长的那条，且它是 T
    assert summary.content_curves["T"][3] == 1.0
    assert summary.content_curves["G"][2] == 1.0


# ---------------------------------------------------------------------------
# 5-mer
# ---------------------------------------------------------------------------


def test_base_code_follows_upstream_encoding() -> None:
    assert (base_code(ord("A")), base_code(ord("T"))) == (0, 1)
    assert (base_code(ord("C")), base_code(ord("G"))) == (2, 3)
    # 小写与 N 都不是合法碱基（上游同样如此——k-mer 只认大写 ACGT）
    assert base_code(ord("a")) == -1
    assert base_code(ord("N")) == -1


def test_kmer_name_round_trip() -> None:
    assert kmer_name(0) == "AAAAA"
    assert kmer_name(108) == "ATCGA"
    assert kmer_name(KMER_BUCKETS - 1) == "GGGGG"
    with pytest.raises(ValueError, match="k-mer 下标"):
        kmer_name(KMER_BUCKETS)


def test_kmer_counts_single_read() -> None:
    """``ATCGA`` 的编码：A=0 T=1 C=2 G=3 A=0 → 0b0001101100 = 108。"""
    summary = collect(("ATCGA", q(40) * 5))

    assert summary.kmer_counts[108] == 1
    assert sum(summary.kmer_counts) == 1


def test_kmer_needs_five_bases() -> None:
    summary = collect(("ATCG", q(40) * 4))

    assert sum(summary.kmer_counts) == 0


def test_kmer_uses_incremental_update() -> None:
    """六条连续的 A：第 1 个 5-mer 从头算，第 2 个从上一次平移而来。"""
    summary = collect(("AAAAAA", q(40) * 6))

    assert summary.kmer_counts[0] == 2
    assert sum(summary.kmer_counts) == 2


def test_kmer_resets_after_n() -> None:
    """遇到 N 之后必须**从头重算** 5 个碱基，不能沿用平移过的中间值。

    ``AAAANAAAA`` 里没有任何合法的连续 5-mer；若实现偷懒沿用旧值，
    这里会算出一个不该存在的 k-mer。
    """
    summary = collect(("AAAANAAAA", q(40) * 9))

    assert sum(summary.kmer_counts) == 0


def test_kmer_recovers_after_n() -> None:
    """N 之后的合法 5-mer 仍要被数到——重算用的是原始序列的相邻 5 个碱基。"""
    summary = collect(("NATCGA", q(40) * 6))

    assert summary.kmer_counts[108] == 1
    assert sum(summary.kmer_counts) == 1


def test_kmer_ignores_lowercase() -> None:
    """小写碱基不是合法 k-mer 输入（上游 BASE2VAL 同样返回 -1）。"""
    summary = collect(("atcga", q(40) * 5))

    assert sum(summary.kmer_counts) == 0


# ---------------------------------------------------------------------------
# 汇总可重复
# ---------------------------------------------------------------------------


def test_summarize_is_repeatable() -> None:
    collector = ReadStatsCollector()
    collector.add(b"ACGT", (q(40) * 4).encode("ascii"))

    first = collector.summarize()
    second = collector.summarize()

    assert first == second


def test_collectors_do_not_share_state() -> None:
    first = ReadStatsCollector()
    first.add(b"ACGT", (q(40) * 4).encode("ascii"))
    second = ReadStatsCollector()

    assert second.summarize().total_reads == 0
