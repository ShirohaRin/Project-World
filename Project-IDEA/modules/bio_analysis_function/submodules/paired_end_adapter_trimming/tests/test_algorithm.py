from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    PairedAdapterTrimConfig,
    trim_pair_by_overlap,
)

#: 一条 Illumina 接头序列，用来构造"读穿"的数据。
ADAPTER = b"AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"


def random_sequence(length: int, seed: int) -> bytes:
    return bytes(random.Random(seed).choices(b"ACGT", k=length))


def record(name: str, sequence: bytes, quality: bytes | None = None) -> FastqRecord:
    return FastqRecord(name, sequence, quality or b"I" * len(sequence))


def through_reads(fragment: bytes, overhang: int) -> tuple[bytes, bytes]:
    """构造一对"片段短于读长、两端都读穿接头"的 read。

    R1 = 片段 + 接头前 ``overhang`` 个碱基；
    R2 = 片段的反向互补 + 接头的反向互补（同样只读穿 ``overhang`` 个）。
    """
    return (
        fragment + ADAPTER[:overhang],
        reverse_complement(fragment) + reverse_complement(ADAPTER)[:overhang],
    )


def test_trims_both_reads_when_both_ends_read_through() -> None:
    """两端读穿：两条 read 都裁到片段长度，接头的两段被切下来。"""
    fragment = random_sequence(100, seed=1)
    read1, read2 = through_reads(fragment, overhang=20)

    result = trim_pair_by_overlap(record("r1", read1), record("r2", read2))

    assert result is not None
    assert result.read1.sequence == fragment
    assert result.read2.sequence == reverse_complement(fragment)
    assert result.adapter1 == ADAPTER[:20]
    assert result.adapter2 == reverse_complement(ADAPTER)[:20]
    assert result.overlap_len == 100
    assert result.offset == -20
    assert result.trimmed_bases == 40


def test_keeps_read_when_only_adapter_overhang_differs() -> None:
    """穿出去的长度不同（R1 只穿出 20 个、R2 整条接头都读到了），两条 read 仍各裁到片段长度。"""
    fragment = random_sequence(90, seed=2)
    read1 = fragment + ADAPTER[:20]
    read2 = reverse_complement(fragment) + reverse_complement(ADAPTER)

    result = trim_pair_by_overlap(record("r1", read1), record("r2", read2))

    assert result is not None
    assert result.read1.sequence == fragment
    assert result.read2.sequence == reverse_complement(fragment)
    assert len(result.adapter1) == 20
    assert result.adapter2 == reverse_complement(ADAPTER)


def test_front_trimmed_is_compensated_crosswise() -> None:
    """头部补偿量是**交叉使用**的：R1 保留多长取决于 R2 被剪掉多少。

    这里只剪了 R1 的头 10 个碱基：R1 应保留到片段末端（``fragment[10:]``，90 个），
    而 R2 保留整条片段（100 个）。
    """
    fragment = random_sequence(100, seed=3)
    read1 = (fragment + ADAPTER[:20])[10:]
    read2 = reverse_complement(fragment) + reverse_complement(ADAPTER)[:20]

    result = trim_pair_by_overlap(
        record("r1", read1),
        record("r2", read2),
        PairedAdapterTrimConfig(front_trimmed1=10),
    )

    assert result is not None
    assert result.read1.sequence == fragment[10:]
    assert len(result.read1.sequence) == 90
    assert result.read2.sequence == reverse_complement(fragment)


def test_quality_is_trimmed_along_with_sequence() -> None:
    """质量串跟着序列一起截断，切点与序列完全一致。"""
    fragment = random_sequence(100, seed=4)
    read1, read2 = through_reads(fragment, overhang=20)
    quality1 = bytes(range(33, 153))
    quality2 = bytes(reversed(range(33, 153)))

    result = trim_pair_by_overlap(
        record("r1", read1, quality1), record("r2", read2, quality2)
    )

    assert result is not None
    assert result.read1.quality == quality1[:100]
    assert result.read2.quality == quality2[:100]
    assert len(result.read1.quality) == len(result.read1.sequence)


def test_returns_none_when_fragment_is_longer_than_read() -> None:
    """片段长于读长（``offset > 0``）：两条 read 都没读穿，没有接头可裁。"""
    fragment = random_sequence(160, seed=5)
    read1 = fragment[:120]
    read2 = reverse_complement(fragment[40:160])

    assert trim_pair_by_overlap(record("r1", read1), record("r2", read2)) is None


def test_returns_none_when_offset_is_zero() -> None:
    """片段长度恰好等于读长（``offset == 0``）：上游不把它当接头处理。"""
    fragment = random_sequence(120, seed=6)

    assert (
        trim_pair_by_overlap(
            record("r1", fragment), record("r2", reverse_complement(fragment))
        )
        is None
    )


def test_returns_none_for_unrelated_reads() -> None:
    assert (
        trim_pair_by_overlap(
            record("a", random_sequence(120, seed=7)),
            record("b", random_sequence(120, seed=8)),
        )
        is None
    )


def test_accepts_plain_overlap_config() -> None:
    """也接受裸的 ``OverlapConfig``（此时头部补偿量按 0 处理）。"""
    from modules.bio_analysis_function.common.paired_overlap import OverlapConfig

    fragment = random_sequence(100, seed=9)
    read1, read2 = through_reads(fragment, overhang=20)

    result = trim_pair_by_overlap(
        record("r1", read1), record("r2", read2), OverlapConfig(require=20)
    )

    assert result is not None
    assert result.read1.sequence == fragment


def test_rejects_mismatched_quality_length() -> None:
    with pytest.raises(ValueError, match="read1"):
        trim_pair_by_overlap(
            record("r1", random_sequence(120, seed=10), b"I" * 119),
            record("r2", random_sequence(120, seed=11)),
        )


def test_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="front_trimmed1"):
        PairedAdapterTrimConfig(front_trimmed1=-1)
    with pytest.raises(ValueError, match="require"):
        PairedAdapterTrimConfig(require=0)
