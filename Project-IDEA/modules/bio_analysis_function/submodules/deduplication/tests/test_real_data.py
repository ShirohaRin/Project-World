"""在真实 Illumina 数据上验证重复检测。

数据是模块共享的 ``tests/data/fastp_R1.fq``（fastp 仓库自带，9 条记录，
其中 1 条长度为 0）。数据量很小，因此**布隆过滤器的假阳性不可能出现**，
可以拿"精确去重"（Python 集合）当独立判据来对照——这比只跟自己比更有意义。
"""

from __future__ import annotations

from pathlib import Path

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.deduplication import deduplicate_fastq

_DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = _DATA_DIR / "fastp_R1.fq"

_SMALL_BUFFER = 1 << 16


def _exact_duplicate_count(source: Path) -> int:
    """精确去重：用集合老老实实数一遍，不看布隆过滤器。"""
    seen: set[bytes] = set()
    duplicates = 0
    for record in read_fastq(source):
        if record.sequence in seen:
            duplicates += 1
        else:
            seen.add(record.sequence)
    return duplicates


def test_default_analysis_level_is_one_gibibyte() -> None:
    """默认只评估 → 档位 1（1 GiB），且真实数据能正常读完。"""
    summary = deduplicate_fastq(_READS)

    assert summary.accuracy_level == 1
    assert summary.total_reads == 9
    assert 0 <= summary.duplicate_reads <= 9
    assert summary.dropped_reads == 0
    assert summary.bases_after <= summary.bases_before


def test_matches_exact_deduplication_on_small_real_data() -> None:
    """数据量远小于位图容量时，布隆过滤器的结论应与精确去重完全一致。"""
    summary = deduplicate_fastq(_READS, buffer_bytes=_SMALL_BUFFER)

    assert summary.total_reads == 9
    assert summary.duplicate_reads == _exact_duplicate_count(_READS)


def test_dedup_output_is_an_order_preserving_subset(tmp_path: Path) -> None:
    """去重输出 = 输入里"第一次出现"的那些记录，顺序不变，内容逐字节一致。"""
    output = tmp_path / "clean.fq"

    summary = deduplicate_fastq(_READS, output, buffer_bytes=_SMALL_BUFFER)

    seen: set[bytes] = set()
    expected: list[tuple[str, bytes]] = []
    for record in read_fastq(_READS):
        if record.sequence in seen:
            continue
        seen.add(record.sequence)
        expected.append((record.name, record.sequence))

    kept = list(read_fastq(output))
    assert len(kept) == summary.kept_reads
    assert [(record.name, record.sequence) for record in kept] == expected


def test_zero_length_record_is_handled() -> None:
    """共享数据里有一条长度为 0 的记录，它必须能走完整条路径而不出错。"""
    summary = deduplicate_fastq(_READS, buffer_bytes=_SMALL_BUFFER)

    lengths = [record.length for record in read_fastq(_READS)]
    assert 0 in lengths
    assert summary.bases_before == sum(lengths)
