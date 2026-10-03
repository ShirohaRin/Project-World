from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.common.paired_overlap import OverlapConfig
from modules.bio_analysis_function.common.sequences import complement, reverse_complement
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    UPSTREAM_TEST_VECTOR,
    correct_pair_by_overlap,
)

#: 与上游写死的两个门槛对应的质量字符：Q36（可信）与 Q14（不可信）。
GOOD = ord("E")
BAD = ord("/")

ADAPTER = b"AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"


def random_sequence(length: int, seed: int) -> bytes:
    return bytes(random.Random(seed).choices(b"ACGT", k=length))


def record(name: str, sequence: bytes, quality: bytes | None = None) -> FastqRecord:
    return FastqRecord(name, sequence, quality or b"I" * len(sequence))


def forward_pair(seed: int = 1) -> tuple[bytes, bytes]:
    """片段 100、读长 80：``offset = 20``、重叠 60。

    重叠区第 ``i`` 位对应 ``R1[20+i]`` 与 ``R2[79-i]``。
    """
    fragment = random_sequence(100, seed)
    return fragment[:80], reverse_complement(fragment[20:100])


def through_pair(seed: int = 7) -> tuple[bytes, bytes]:
    """片段 100、读长 120：两端都读穿接头，``offset = -20``、重叠 100。

    重叠区第 ``i`` 位对应 ``R1[i]`` 与 ``R2[99-i]``。
    """
    fragment = random_sequence(100, seed)
    return (
        fragment + ADAPTER[:20],
        reverse_complement(fragment) + reverse_complement(ADAPTER)[:20],
    )


def other_base(base: int) -> int:
    """挑一个与 ``base`` 不同的碱基（用来制造错配）。"""
    return ord("A") if base != ord("A") else ord("C")


def test_upstream_vector_is_reproduced_bit_for_bit() -> None:
    """上游 ``BaseCorrector::test()`` 的向量：序列、质量、修正数都要逐位一致。"""
    vector = UPSTREAM_TEST_VECTOR
    result = correct_pair_by_overlap(
        record("name", vector["read1"], vector["quality1"]),
        record("name", vector["read2"], vector["quality2"]),
        OverlapConfig(
            diff_limit=vector["diff_limit"],
            require=vector["require"],
            diff_percent_limit=vector["diff_percent_limit"],
        ),
    )

    assert result.read1.sequence == vector["expect_read1"]
    assert result.read1.quality == vector["expect_quality1"]
    assert result.read2.sequence == vector["expect_read2"]
    assert result.read2.quality == vector["expect_quality2"]
    assert result.corrected == 2
    assert result.corrected_read1 and result.corrected_read2
    assert result.corrected_reads == 2


def test_uses_read1_to_fix_read2() -> None:
    """R1 可信、R2 不可信且与 R1 不互补 → 用 R1 改 R2，并把质量值一并搬过去。"""
    read1, read2 = forward_pair()
    broken = bytearray(read2)
    broken[79] = other_base(complement(read1[20]))
    quality2 = bytearray([GOOD] * len(read2))
    quality2[79] = BAD

    result = correct_pair_by_overlap(
        record("r1", read1, bytes([GOOD] * len(read1))),
        record("r2", bytes(broken), bytes(quality2)),
    )

    assert result.corrected == 1
    assert result.corrected_read1 is False
    assert result.corrected_read2 is True
    assert result.corrected_reads == 1
    assert result.read1.sequence == read1  # R1 未被动过
    assert result.read2.sequence[79] == complement(read1[20])
    assert result.read2.quality[79] == GOOD


def test_uses_read2_to_fix_read1() -> None:
    """反过来：R2 可信、R1 不可信 → 用 R2 改 R1。"""
    read1, read2 = forward_pair()
    broken = bytearray(read1)
    broken[20] = other_base(complement(read2[79]))
    quality1 = bytearray([GOOD] * len(read1))
    quality1[20] = BAD

    result = correct_pair_by_overlap(
        record("r1", bytes(broken), bytes(quality1)),
        record("r2", read2, bytes([GOOD] * len(read2))),
    )

    assert result.corrected == 1
    assert result.corrected_read1 is True
    assert result.corrected_read2 is False
    assert result.read2.sequence == read2
    assert result.read1.sequence[20] == complement(read2[79])
    assert result.read1.quality[20] == GOOD


def test_both_high_quality_is_left_alone() -> None:
    """两边都可信却不一致：谁也说不清哪边对，一个字节都不改。"""
    read1, read2 = forward_pair()
    broken = bytearray(read2)
    broken[79] = other_base(complement(read1[20]))

    result = correct_pair_by_overlap(
        record("r1", read1, bytes([GOOD] * len(read1))),
        record("r2", bytes(broken), bytes([GOOD] * len(read2))),
    )

    assert result.corrected == 0
    assert result.corrected_reads == 0
    assert result.read1.sequence == read1
    assert result.read2.sequence == bytes(broken)


def test_both_low_quality_is_left_alone() -> None:
    """两边都不可信：同样不改。"""
    read1, read2 = forward_pair()
    broken = bytearray(read2)
    broken[79] = other_base(complement(read1[20]))
    quality1 = bytearray([GOOD] * len(read1))
    quality1[20] = BAD
    quality2 = bytearray([GOOD] * len(read2))
    quality2[79] = BAD

    result = correct_pair_by_overlap(
        record("r1", read1, bytes(quality1)),
        record("r2", bytes(broken), bytes(quality2)),
    )

    assert result.corrected == 0


def test_no_mismatch_changes_nothing() -> None:
    """两条 read 完全互补（``diff == 0``）时上游直接短路，不改任何字节。"""
    read1, read2 = forward_pair()
    result = correct_pair_by_overlap(record("r1", read1), record("r2", read2))

    assert result.corrected == 0
    assert result.read1.sequence == read1
    assert result.read2.sequence == read2


def test_unrelated_reads_change_nothing() -> None:
    """没有重叠的一对：照常输出、不改动。"""
    read1 = random_sequence(120, seed=21)
    read2 = random_sequence(120, seed=22)

    result = correct_pair_by_overlap(record("r1", read1), record("r2", read2))

    assert result.corrected == 0
    assert result.read1.sequence == read1
    assert result.read2.sequence == read2


def test_corrects_when_both_ends_read_through_adapter() -> None:
    """``offset < 0``（两端读穿）的几何下位置对应同样正确。"""
    read1, read2 = through_pair()
    broken = bytearray(read2)
    broken[99] = other_base(complement(read1[0]))
    quality2 = bytearray([GOOD] * len(read2))
    quality2[99] = BAD

    result = correct_pair_by_overlap(
        record("r1", read1, bytes([GOOD] * len(read1))),
        record("r2", bytes(broken), bytes(quality2)),
    )

    assert result.corrected == 1
    assert result.corrected_read2 is True
    assert result.read2.sequence[99] == complement(read1[0])
    assert result.read2.quality[99] == GOOD


def test_rejects_mismatched_quality_length() -> None:
    with pytest.raises(ValueError, match="read1"):
        correct_pair_by_overlap(
            record("r1", random_sequence(80, seed=31), b"I" * 79),
            record("r2", random_sequence(80, seed=32)),
        )
