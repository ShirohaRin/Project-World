"""过表达序列分析的测试。

这一步的"对错"全在几个写死的阈值与判断方向上，所以测试集中在：

1. **候选筛选用 ≥、报告阈值用 >**——两套阈值，方向不同，最容易写反；
2. **长度分档是从上往下判的**（先看"≥ 读长-1"，再看 100/40/20/10）；
3. **命中之后要多跳一个片段长度**，否则重叠窗口会被重复计数；
4. **去子串用整数除法**比较倍数。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.submodules.overrepresented_sequences import (
    OverrepConfig,
    candidate_steps,
    collect_candidate_counts,
    filter_candidates,
    find_overrepresented_sequences,
    passes_report_threshold,
    remove_substrings,
    scan_sampled_read,
)

_Q40 = chr(40 + 33)


# ---------------------------------------------------------------------------
# 片段长度与候选发现
# ---------------------------------------------------------------------------


def test_candidate_steps_include_the_dynamic_one() -> None:
    assert candidate_steps(151) == (10, 20, 40, 100, 149)
    # 读长很短时动态那一段会小于 10，甚至为负——上游不去重也不过滤，
    # 只在取片段时靠"step > 长度"跳过。
    assert candidate_steps(5)[-1] == 3


def test_collect_candidate_counts_counts_every_window() -> None:
    counts = collect_candidate_counts([b"ACGTACGTAC"], seq_length=10, base_limit=1000)

    # step=10 时窗口数为 0（range(10-10) 为空）；step=8 时有两个窗口。
    assert counts == {b"ACGTACGT": 1, b"CGTACGTA": 1}


def test_collect_candidate_counts_stops_at_the_base_limit() -> None:
    """越限的那一条**也要被处理**——上游是处理完再回头检查。"""
    sequences = [b"ACGTACGTAC", b"ACGTACGTAC", b"ACGTACGTAC"]

    counts = collect_candidate_counts(sequences, seq_length=10, base_limit=15)

    # 上限 15：第一条（10）处理、第二条（累计 20 ≥ 15）处理，第三条不看。
    assert counts[b"ACGTACGT"] == 2


def test_filter_candidates_thresholds() -> None:
    hot = filter_candidates(
        {
            b"A" * 151: 3,   # 长度 ≥ 读长-1 → 阈值 3，恰好达标
            b"C" * 100: 4,   # 阈值 5，不够
            b"G" * 40: 20,   # 阈值 20，恰好达标
            b"T" * 20: 99,   # 阈值 100，不够
        },
        seq_length=151,
    )

    assert set(hot) == {b"A" * 151, b"G" * 40}


def test_filter_candidates_ignores_too_short_pieces() -> None:
    assert filter_candidates({b"A" * 9: 10_000}, seq_length=151) == {}


# ---------------------------------------------------------------------------
# 去子串
# ---------------------------------------------------------------------------


def test_remove_substrings_drops_short_piece_when_not_much_more_frequent() -> None:
    """短的只比长的多不到 10 倍 → 它只是长的一部分，剔掉。"""
    hot = remove_substrings({b"ACGTACGT": 900, b"ACGTACGTACGT": 100})

    assert set(hot) == {b"ACGTACGTACGT"}


def test_remove_substrings_keeps_short_piece_when_far_more_frequent() -> None:
    """多出 10 倍以上 → 它自己就是个独立的富集信号，留下。"""
    hot = remove_substrings({b"ACGTACGT": 1000, b"ACGTACGTACGT": 100})

    assert set(hot) == {b"ACGTACGT", b"ACGTACGTACGT"}


def test_remove_substrings_uses_integer_division() -> None:
    """比值 9.9 在整数除法下是 9（< 10），因此会被剔掉。"""
    # 99 // 10 == 9 < 10 → 剔掉；若用浮点比较 9.9 < 10 也是剔掉，
    # 但 100 // 10 == 10 就已经不剔了。这里固定住"整数除法"这个口径。
    assert set(remove_substrings({b"AC": 99, b"ACGT": 10})) == {b"ACGT"}
    assert set(remove_substrings({b"AC": 100, b"ACGT": 10})) == {b"AC", b"ACGT"}


def test_remove_substrings_ignores_identical_keys() -> None:
    assert remove_substrings({b"ACGT": 5}) == {b"ACGT": 5}


# ---------------------------------------------------------------------------
# 报告阈值
# ---------------------------------------------------------------------------


def test_report_threshold_is_strict_and_per_length() -> None:
    """阈值是"严格大于"，且按长度**精确匹配**档位。"""
    assert passes_report_threshold(10, 6, 100) is True    # 600 > 500
    assert passes_report_threshold(10, 5, 100) is False   # 500 不 > 500
    assert passes_report_threshold(20, 3, 100) is True    # 300 > 200
    assert passes_report_threshold(20, 2, 100) is False   # 200 不 > 200
    assert passes_report_threshold(40, 2, 100) is True    # 200 > 100
    assert passes_report_threshold(40, 1, 100) is False   # 100 不 > 100
    assert passes_report_threshold(100, 1, 100) is True   # 100 > 50


def test_report_threshold_default_bucket() -> None:
    """不在档位表里的长度（例如 150）走默认阈值 20。"""
    assert passes_report_threshold(150, 1, 100) is True   # 100 > 20
    assert passes_report_threshold(150, 0, 100) is False


# ---------------------------------------------------------------------------
# 采样统计
# ---------------------------------------------------------------------------


def test_scan_sampled_read_skips_a_whole_piece_after_a_hit() -> None:
    """命中之后要多跳一个片段长度，否则同一段会被重叠窗口数成多次。"""
    counts = {b"A" * 8: 0}
    distributions = {b"A" * 8: [0] * 10}

    scan_sampled_read(b"A" * 10, counts, distributions, 10, (8,))

    assert counts[b"A" * 8] == 1
    assert distributions[b"A" * 8] == [1] * 8 + [0, 0]


def test_scan_sampled_read_ignores_unknown_pieces() -> None:
    counts = {b"A" * 8: 0}
    distributions = {b"A" * 8: [0] * 10}

    scan_sampled_read(b"ACGTACGTAC", counts, distributions, 10, (8,))

    assert counts[b"A" * 8] == 0


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------


def write_fastq(path: Path, sequences: list[str]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(f"@read{index}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


#: 故意掺进去的 40bp 重复片段。**必须是非周期的**——周期串（如 ``"ACGT" * 10``）
#: 会让它自己的 10bp 子串出现几千次，那条子串又会因为"多十倍以上"的规则
#: 把这条长片段剔掉，于是什么也测不到。
_REPEAT = "".join(random.Random(7).choices("ACGT", k=40))


def contaminated_reads(count: int, seed: int) -> list[str]:
    """每条 read 都以同一段重复开头，**尾巴各不相同**。

    尾巴随机是有意的：如果尾巴固定，那么"以重复片段开头的更长窗口"也会
    每条都出现一次、同样达到阈值，按去子串的规则就会把短的这条剔掉，
    最后报出来的是那个更长的窗口。
    """
    rng = random.Random(seed)
    return [_REPEAT + "".join(rng.choices("ACGT", k=60)) for _ in range(count)]


def test_clean_data_reports_nothing(tmp_path: Path) -> None:
    """干净数据不该报过表达序列——这是常态，也是这个算法的默认结论。"""
    source = tmp_path / "clean.fq"
    rng = random.Random(20260922)
    write_fastq(source, ["".join(rng.choices("ACGT", k=40)) for _ in range(50)])

    summary = find_overrepresented_sequences(source)

    assert summary.total_reads == 50
    assert summary.total_bases == 50 * 40
    # 没有候选就不必跑第二遍扫描，所以采样条数是 0。
    assert summary.sampled_reads == 0
    assert summary.sequences == ()


def test_planted_repeat_is_found(tmp_path: Path) -> None:
    source = tmp_path / "contaminated.fq"
    write_fastq(source, contaminated_reads(300, seed=11))

    summary = find_overrepresented_sequences(
        source, config=OverrepConfig(sampling=1)
    )

    reported = {item.sequence for item in summary.sequences}
    assert _REPEAT in reported
    top = summary.sequences[0]
    assert top.sequence == _REPEAT
    assert top.length == 40
    assert top.count == 300
    assert top.estimated_count == 300
    assert top.base_percent > 0
    assert len(top.distribution) == summary.seq_length
    assert top.distribution[0] == 300


def test_sampling_scales_the_estimate(tmp_path: Path) -> None:
    """采样 1/10 时报出的采样计数是总量的十分之一，推算总量仍是全量。"""
    source = tmp_path / "contaminated.fq"
    write_fastq(source, contaminated_reads(300, seed=13))

    summary = find_overrepresented_sequences(
        source, config=OverrepConfig(sampling=10)
    )

    top = summary.sequences[0]
    assert top.count == 30   # 采样了 30 条
    assert top.estimated_count == 300


def test_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")

    summary = find_overrepresented_sequences(source)

    assert summary.total_reads == 0
    assert summary.sequences == ()


def test_missing_input_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        find_overrepresented_sequences(tmp_path / "absent.fq")


def test_invalid_sampling_rejected() -> None:
    with pytest.raises(ValueError, match="sampling"):
        OverrepConfig(sampling=0)
    with pytest.raises(ValueError, match="sampling"):
        OverrepConfig(sampling=10001)
    with pytest.raises(ValueError, match="base_limit"):
        OverrepConfig(base_limit=0)


def test_analysis_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, ["ACGT" * 10 for _ in range(20)])

    find_overrepresented_sequences(source)

    assert sorted(item.name for item in tmp_path.iterdir()) == ["reads.fq"]


def test_render_mentions_counts(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, contaminated_reads(300, seed=17))

    text = find_overrepresented_sequences(
        source, config=OverrepConfig(sampling=1)
    ).render()

    assert "检出过表达序列" in text
    assert "采样计数" in text
