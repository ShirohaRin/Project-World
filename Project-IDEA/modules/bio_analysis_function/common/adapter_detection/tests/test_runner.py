"""文件级接口的测试：采样、压缩输入、路径与错误处理。"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.adapter_detection.algorithm import (
    AdapterDetectionConfig,
)
from modules.bio_analysis_function.common.adapter_detection.runner import (
    detect_adapter_from_file,
    sample_sequences,
)
from modules.bio_analysis_function.common.adapter_detection.tests.test_algorithm import (
    SMALL_TABLE,
    TRUSEQ,
    make_reads,
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"


def write_fastq(path: Path, sequences: list[bytes]) -> None:
    """把序列写成 FASTQ（质量串只占位，检测不看质量）。"""
    with path.open("wb") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(b"@read%d\n" % index)
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def test_detects_adapter_from_file(tmp_path: Path) -> None:
    """文件级接口能把接头检出来（缩表 + 小样本，控制纯 Python 的耗时）。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_reads(2000, TRUSEQ, 400))

    result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)
    )

    assert result.detected
    assert result.adapter == TRUSEQ
    assert result.sampled_reads == 2000
    # 带接头的 read length 取决于随机插入片段长度（50~130），因此碱基数不是一个定值；
    # 这里只守住区间：每条 83~151 个碱基。
    assert 2000 * 83 <= result.sampled_bases <= 2000 * 151


def test_sampling_stops_at_read_limit(tmp_path: Path) -> None:
    """采样条数上限生效。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_reads(300, TRUSEQ, 60))

    sequences = sample_sequences(
        source, AdapterDetectionConfig(min_reads=1, max_reads=100, adapters=())
    )

    assert len(sequences) == 100


def test_sampling_stops_at_base_limit(tmp_path: Path) -> None:
    """采样碱基数上限生效：累计碱基数达标即停（达标那一条会被带上）。"""
    source = tmp_path / "reads.fq"
    write_fastq(source, make_reads(300, TRUSEQ, 60))

    sequences = sample_sequences(
        source, AdapterDetectionConfig(min_reads=1, max_bases=1000, adapters=())
    )

    total = sum(len(sequence) for sequence in sequences)
    assert total >= 1000
    # 去掉最后一条就低于上限，说明正好在达标处停下
    assert total - len(sequences[-1]) < 1000


def test_sampling_keeps_whole_file_when_small(tmp_path: Path) -> None:
    source = tmp_path / "reads.fq"
    write_fastq(source, make_reads(12, TRUSEQ, 3))

    sequences = sample_sequences(source, AdapterDetectionConfig(min_reads=1))

    assert len(sequences) == 12


def test_gzip_input_is_read_by_magic(tmp_path: Path) -> None:
    """gzip 输入按内容识别（不看扩展名），结果与纯文本一致。"""
    plain = tmp_path / "reads.fq"
    write_fastq(plain, make_reads(2000, TRUSEQ, 400))
    packed = tmp_path / "reads.dat"  # 扩展名故意不写 .gz
    with gzip.open(packed, "wb") as handle:
        handle.write(plain.read_bytes())

    config = AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)
    from_gzip = detect_adapter_from_file(packed, config=config)
    from_plain = detect_adapter_from_file(plain, config=config)

    assert from_gzip.adapter == from_plain.adapter == TRUSEQ


def test_non_ascii_path(tmp_path: Path) -> None:
    """含中文的路径必须能正常读取。"""
    folder = tmp_path / "测序数据"
    folder.mkdir()
    source = folder / "样本1.fq"
    write_fastq(source, make_reads(2000, TRUSEQ, 400))

    result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)
    )

    assert result.adapter == TRUSEQ


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        detect_adapter_from_file(tmp_path / "absent.fq")


def test_format_error_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        detect_adapter_from_file(broken)


def test_zero_length_reads_do_not_break_detection(tmp_path: Path) -> None:
    """零长度 read 不能把检测搞崩（真实数据里就有这种记录）。"""
    source = tmp_path / "reads.fq"
    sequences = [b""] + make_reads(2000, TRUSEQ, 400)
    write_fastq(source, sequences)

    result = detect_adapter_from_file(
        source, config=AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)
    )

    assert result.detected
    assert result.sampled_reads == 2001
