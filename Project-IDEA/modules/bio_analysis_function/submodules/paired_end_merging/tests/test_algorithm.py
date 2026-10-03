from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.common.paired_overlap import (
    GAP_ONLY_VECTOR,
    UPSTREAM_TEST_VECTORS,
    OverlapConfig,
)
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.paired_end_merging import (
    PairedMergeConfig,
    merge_pair,
)


def record(name: str, sequence: bytes, quality: bytes | None = None) -> FastqRecord:
    return FastqRecord(name, sequence, quality or b"I" * len(sequence))


def test_upstream_vector_merge_uses_fastp_lengths() -> None:
    vector = UPSTREAM_TEST_VECTORS[0]
    config = OverlapConfig(
        diff_limit=vector["diff_limit"],
        require=vector["require"],
        diff_percent_limit=vector["diff_percent_limit"],
    )
    result = merge_pair(record("r1", vector["read1"]), record("r2", vector["read2"]), config)

    assert result is not None
    offset, overlap_len, _ = vector["expect"]
    len1 = overlap_len + offset
    len2 = len(vector["read2"]) - overlap_len
    expected = vector["read1"][:len1] + reverse_complement(vector["read2"])[overlap_len:]
    assert result.sequence == expected
    assert result.name == f"r1 merged_{len1}_{len2}"
    assert len(result.sequence) == len1 + len2


def test_positive_offset_appends_reverse_complement_tail_and_reversed_quality() -> None:
    rng = random.Random(20260920)
    fragment = "".join(rng.choices("ACGT", k=100)).encode("ascii")
    read1 = fragment[:80]
    read2 = reverse_complement(fragment[20:100])
    quality1 = bytes(range(80, 160))
    quality2 = bytes(range(40, 120))

    result = merge_pair(record("left", read1, quality1), record("right", read2, quality2))

    assert result is not None
    assert result.name == "left merged_80_20"
    assert result.sequence == read1 + fragment[80:100]
    assert result.quality == quality1 + quality2[::-1][60:80]


def test_zero_offset_has_no_read2_suffix() -> None:
    fragment = bytes(random.Random(10).choices(b"ACGT", k=80))
    read1 = fragment
    read2 = reverse_complement(fragment)

    result = merge_pair(record("r1", read1), record("r2", read2))

    assert result is not None
    assert result.name == "r1 merged_80_0"
    assert result.sequence == read1
    assert result.quality == b"I" * 80


def test_negative_offset_keeps_only_read1_overlap_prefix() -> None:
    fragment = b"TGCA" * 20
    read1 = fragment
    read2 = reverse_complement(b"A" * 20 + fragment)

    result = merge_pair(record("r1", read1), record("r2", read2))

    assert result is not None
    assert result.name == "r1 merged_80_0"
    assert result.sequence == read1


def test_no_overlap_returns_none() -> None:
    result = merge_pair(record("r1", b"A" * 80), record("r2", b"C" * 80))

    assert result is None


def test_invalid_quality_length_is_rejected() -> None:
    with pytest.raises(ValueError, match="read1"):
        merge_pair(record("r1", b"A" * 80, b"I" * 79), record("r2", b"C" * 80))


def test_paired_merge_config_validates_overlap_parameters() -> None:
    with pytest.raises(ValueError, match="require"):
        PairedMergeConfig(require=0)


def test_gap_path_marks_the_merge_result() -> None:
    vector = GAP_ONLY_VECTOR
    read1 = record("r1", vector["read1"])
    read2 = record("r2", vector["read2"])

    # 这组数据的 overlap 只有缺口那一轮认得出，无缺口轮判不重叠。
    assert merge_pair(read1, read2) is None

    config = PairedMergeConfig(
        diff_limit=vector["diff_limit"],
        require=vector["require"],
        allow_gap=True,
    )
    result = merge_pair(read1, read2, config)

    assert result is not None
    assert result.has_gap is True
