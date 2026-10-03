"""插入片段长度分布的测试。

桶的分配是纯逻辑，所以直接用**手工构造的** ``OverlapResult`` 测，不掺字符串；
文件级那几条再用真实构造的 read 对走一遍全链路。

重点守三处边界（都照抄上游，容易"顺手改对"）：

1. **判不出**（没重叠）进溢出桶；
2. **恰好等于上限**留在上限桶里，判据是严格大于；
3. **峰值只在上限之内找**——溢出桶里混着判不出的，把它算成峰值没有意义。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.paired_overlap import OverlapResult
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.insert_size_distribution import (
    InsertSizeConfig,
    InsertSizeHistogram,
    analyze_insert_size,
)

_Q40 = chr(40 + 33)


def overlap(
    offset: int = 0,
    overlap_len: int = 50,
    *,
    read_length: int = 100,
    overlapped: bool = True,
) -> OverlapResult:
    """手工构造一份重叠结论；``insert_size`` 由这几个字段推出来。"""
    return OverlapResult(
        overlapped=overlapped,
        offset=offset,
        overlap_len=overlap_len,
        read1_length=read_length,
        read2_length=read_length,
    )


# ---------------------------------------------------------------------------
# 桶的分配
# ---------------------------------------------------------------------------


def test_fragment_longer_than_read_uses_sum_minus_overlap() -> None:
    """片段长于读长：``len1 + len2 - 重叠``。"""
    histogram = InsertSizeHistogram()
    histogram.add(overlap(offset=10, overlap_len=50, read_length=100))

    counts = histogram.summarize().histogram
    assert counts[150] == 1
    assert sum(counts) == 1


def test_fragment_shorter_than_read_uses_overlap_length() -> None:
    """两端读穿：重叠长度本身就是片段长度。"""
    histogram = InsertSizeHistogram()
    histogram.add(overlap(offset=-10, overlap_len=80, read_length=100))

    counts = histogram.summarize().histogram
    assert counts[80] == 1


def test_unoverlapped_goes_to_the_overflow_bucket() -> None:
    histogram = InsertSizeHistogram(InsertSizeConfig(max_size=10))
    histogram.add(overlap(overlapped=False))

    summary = histogram.summarize()
    assert summary.histogram[10] == 1
    assert summary.unknown_pairs == 1
    assert summary.overlapped_pairs == 0


def test_size_above_limit_goes_to_the_overflow_bucket() -> None:
    histogram = InsertSizeHistogram(InsertSizeConfig(max_size=100))
    histogram.add(overlap(offset=100, overlap_len=50, read_length=100))  # 200 - 50 = 150

    assert histogram.summarize().histogram[100] == 1


def test_size_exactly_at_limit_stays_in_its_own_bucket() -> None:
    """恰好等于上限时留在上限桶（判据是严格大于）——上游如此。"""
    histogram = InsertSizeHistogram(InsertSizeConfig(max_size=100))
    # 片段 = 重叠长度 = 100（offset <= 0 的分支）
    histogram.add(overlap(offset=0, overlap_len=100, read_length=100))

    assert histogram.summarize().histogram[100] == 1


def test_histogram_length_is_limit_plus_one() -> None:
    summary = InsertSizeHistogram(InsertSizeConfig(max_size=7)).summarize()
    assert len(summary.histogram) == 8
    assert summary.max_size == 7


def test_config_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_size"):
        InsertSizeConfig(max_size=0)


# ---------------------------------------------------------------------------
# 峰值
# ---------------------------------------------------------------------------


def test_peak_is_the_most_frequent_size() -> None:
    histogram = InsertSizeHistogram()
    for _ in range(3):
        histogram.add(overlap(offset=10, overlap_len=50, read_length=100))  # 150
    histogram.add(overlap(offset=-10, overlap_len=80, read_length=100))  # 80

    summary = histogram.summarize()
    assert summary.peak_size == 150
    assert summary.histogram[150] == 3


def test_peak_ignores_the_overflow_bucket() -> None:
    """溢出桶里混着"判不出"的对，不该被当成峰值。"""
    histogram = InsertSizeHistogram(InsertSizeConfig(max_size=200))
    for _ in range(5):
        histogram.add(overlap(overlapped=False))
    histogram.add(overlap(offset=10, overlap_len=50, read_length=100))  # 150

    summary = histogram.summarize()
    assert summary.histogram[200] == 5
    assert summary.peak_size == 150


def test_peak_breaks_ties_towards_the_smaller_size() -> None:
    """并列时取较小的（上游用严格大于，先遇到的胜出）。"""
    histogram = InsertSizeHistogram()
    histogram.add(overlap(offset=0, overlap_len=120, read_length=100))
    histogram.add(overlap(offset=0, overlap_len=130, read_length=100))

    # 片段 120 与 130 各 1 对；120 先遇到且后续没有严格更大。
    assert histogram.summarize().peak_size == 120


def test_empty_input() -> None:
    summary = InsertSizeHistogram().summarize()

    assert summary.total_pairs == 0
    assert summary.overlap_rate == 0.0
    assert summary.peak_size == 0
    assert sum(summary.histogram) == 0


def test_overlap_rate() -> None:
    histogram = InsertSizeHistogram()
    histogram.add(overlap(offset=10, overlap_len=50))
    histogram.add(overlap(overlapped=False))

    summary = histogram.summarize()
    assert summary.overlap_rate == 0.5
    assert summary.total_pairs == 2
    assert summary.overlapped_pairs == 1


def test_render_mentions_peak_and_unknown() -> None:
    histogram = InsertSizeHistogram()
    histogram.add(overlap(offset=10, overlap_len=50))

    text = histogram.summarize().render()
    assert "峰值片段长度：150" in text
    assert "判不出/超上限：0" in text


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------


def write_paired(
    folder: Path, template: str, read_length: int, count: int
) -> tuple[Path, Path]:
    """按模板造 ``count`` 对 read：R1 取头部，R2 取尾部的反向互补。

    这样两条 read 在模板上覆盖 ``[0, R)`` 与 ``[S-R, S)`` 两段，
    重叠长度 = ``2R - S``（要求 ``S < 2R``），片段长度就是 ``S``。
    """
    read1_text = template[:read_length]
    read2_text = reverse_complement(template[-read_length:].encode()).decode()
    read1 = folder / "R1.fq"
    read2 = folder / "R2.fq"
    with read1.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            handle.write(f"@read{index}/1\n{read1_text}\n+\n{_Q40 * read_length}\n")
    with read2.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            handle.write(f"@read{index}/2\n{read2_text}\n+\n{_Q40 * read_length}\n")
    return read1, read2


#: 400bp 的模板。**必须是非周期序列**：像 ``"ACGT" * 100`` 这种周期串会让
#: R1 与 R2 完全相同，重叠分析会找到意料之外的错位，测出来的"片段长度"就不可信。
_TEMPLATE = "".join(random.Random(20260922).choices("ACGT", k=400))


def test_file_level_locates_the_peak(tmp_path: Path) -> None:
    """片段 160bp、读长 120bp 的数据：峰值应当落在 160。"""
    read1, read2 = write_paired(tmp_path, _TEMPLATE[:160], 120, 5)

    summary = analyze_insert_size(read1, read2)

    assert summary.total_pairs == 5
    assert summary.overlapped_pairs == 5
    assert summary.peak_size == 160


def test_file_level_handles_unrelated_pairs(tmp_path: Path) -> None:
    """两条 read 毫不相干时判不出片段长度，应当进溢出桶而不是报错。"""
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    unrelated1 = "ACGT" * 50
    unrelated2 = "TTTT" * 50
    with read1.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"@a/1\n{unrelated1}\n+\n{_Q40 * 200}\n")
    with read2.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"@a/2\n{unrelated2}\n+\n{_Q40 * 200}\n")

    summary = analyze_insert_size(read1, read2)

    assert summary.total_pairs == 1
    assert summary.unknown_pairs == 1
    assert summary.peak_size == 0


def test_file_level_rejects_mismatched_counts(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, _TEMPLATE[:160], 120, 3)
    read2.write_text(read2.read_text().split("@read1/2")[0], encoding="ascii")

    with pytest.raises(ValueError, match="记录数不一致"):
        analyze_insert_size(read1, read2)


def test_file_level_reports_missing_input(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, _TEMPLATE[:160], 120, 1)

    with pytest.raises(ValueError, match="文件不存在"):
        analyze_insert_size(read1, tmp_path / "absent.fq")
    with pytest.raises(ValueError, match="文件不存在"):
        analyze_insert_size(tmp_path / "absent.fq", read2)


def test_file_level_writes_nothing(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, _TEMPLATE[:160], 120, 2)

    analyze_insert_size(read1, read2)

    assert sorted(item.name for item in tmp_path.iterdir()) == ["R1.fq", "R2.fq"]
