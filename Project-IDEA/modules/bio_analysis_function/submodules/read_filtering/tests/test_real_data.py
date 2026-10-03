"""在真实测序数据上验证 reads 过滤。

数据来源同其他预处理子模块：fastp 仓库自带的 ``testdata/R1.fq``（MIT 许可），
已固定为模块共享测试数据 ``tests/data/fastp_R1.fq``——真实 Illumina 单端数据，
9 条记录（1 条长度为 0），其余 8 条长度均为 151bp。

这三条用例的价值在于验证三件事：

1. **默认参数不该滥杀**。这是 fastp 自己仓库里的测试数据，按默认参数应当
   只有那条空记录被丢弃；
2. **阈值确实在起作用**。把低质量比例上限调紧，被筛掉的正是尾部劣化最严重的
   那几条 read，而不是随机波动；
3. **过滤不改写序列**。写出的记录与输入逐字节一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.read_filtering.algorithm import (
    FAIL_LENGTH,
    FAIL_QUALITY,
    ReadFilterConfig,
)
from modules.bio_analysis_function.submodules.read_filtering.runner import filter_fastq

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"


def test_default_config_on_real_data(tmp_path: Path) -> None:
    """默认参数下只有那条空记录被丢弃，其余 8 条真实 read 全部通过。"""
    output = tmp_path / "passed.fq"

    summary = filter_fastq(_READS, output)

    assert summary.total_reads == 9
    assert summary.kept_reads == 8
    assert summary.dropped_reads == 1
    assert summary.failures == {FAIL_LENGTH: 1}
    # 空记录贡献 0 个碱基，因此碱基数不变。
    assert summary.bases_before == 8 * 151
    assert summary.bases_after == 8 * 151

    kept_records = list(read_fastq(output))
    assert len(kept_records) == 8
    assert all(record.length == 151 for record in kept_records)


def test_quality_limit_selects_the_worst_reads(tmp_path: Path) -> None:
    """把低质量比例上限从 40% 收紧到 20%，被筛掉的正是尾部最差的那 3 条。

    8 条真实 read 的低质量碱基数依次为
    ``6, 38, 3, 37, 3, 37, 3, 3``（阈值 Q15、长度 151）；
    20% 对应 30.2 个碱基，因此恰好是 3 条（38 与两个 37）越过这条线。
    """
    output = tmp_path / "passed.fq"
    config = ReadFilterConfig(unqualified_percent_limit=20)

    summary = filter_fastq(_READS, output, config=config)

    assert summary.kept_reads == 5
    assert summary.failures == {FAIL_QUALITY: 3, FAIL_LENGTH: 1}


def test_strict_quality_rejects_every_real_read(tmp_path: Path) -> None:
    """Q30 + 不允许任何低质量碱基：8 条真实 read 全部被质量筛掉。

    这不是"算法太严"，而是这批数据的平均质量在 Q28~Q34 之间：
    要求每个碱基都达到 Q30，它们确实达不到。
    """
    output = tmp_path / "passed.fq"
    config = ReadFilterConfig(qualified_quality_phred=30, unqualified_percent_limit=0)

    summary = filter_fastq(_READS, output, config=config)

    assert summary.kept_reads == 0
    assert summary.failures == {FAIL_QUALITY: 8, FAIL_LENGTH: 1}
    assert output.read_bytes() == b""


def test_passing_reads_are_byte_identical_to_input(tmp_path: Path) -> None:
    """过滤只决定去留，绝不改写序列与质量。"""
    output = tmp_path / "passed.fq"

    filter_fastq(_READS, output, config=ReadFilterConfig(required_length=100))

    source_records = [
        record for record in read_fastq(_READS) if record.length >= 100
    ]
    written_records = list(read_fastq(output))

    assert len(written_records) == len(source_records) == 8
    for written, source in zip(written_records, source_records):
        assert written.name == source.name
        assert written.sequence == source.sequence
        assert written.quality == source.quality
