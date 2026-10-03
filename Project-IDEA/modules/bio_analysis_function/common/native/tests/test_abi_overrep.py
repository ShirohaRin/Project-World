"""原生过表达序列分析的对拍测试。

对拍要求：**检出条数、顺序、每条的序列、采样计数、推算总量、位置分布
全部相同**。顺序也要一致（两侧都按"计数降序、长度升序、字典序"排），
因为它决定了报告里谁排在前面。

两个"陷阱"数据专门造出来：
- 干净的随机数据（一条都不该检出）；
- 掺入非周期重复片段的数据（必须检出那一条，而且**只**检出那一条）。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.overrepresented_sequences import (
    OverrepConfig,
    find_overrepresented_sequences as python_find,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

_Q40 = chr(40 + 33)

#: 非周期重复片段。周期串（如 "ACGT"*10）会被它自己的短子串剔掉，测不到东西。
_REPEAT = "".join(random.Random(7).choices("ACGT", k=40))


def write_fastq(path: Path, sequences: list[str]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index, sequence in enumerate(sequences):
            handle.write(f"@read{index}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


def contaminated(count: int, seed: int) -> list[str]:
    """每条 read 都以同一段重复开头，尾巴各不相同（理由见模块测试）。"""
    rng = random.Random(seed)
    return [_REPEAT + "".join(rng.choices("ACGT", k=60)) for _ in range(count)]


def assert_same(native: abi.NativeOverrepSummary, python_summary) -> None:
    assert native.total_reads == python_summary.total_reads
    assert native.total_bases == python_summary.total_bases
    assert native.sampled_reads == python_summary.sampled_reads
    assert native.seq_length == python_summary.seq_length
    assert native.sampling == python_summary.sampling

    assert len(native.sequences) == len(python_summary.sequences)
    for found, reference in zip(native.sequences, python_summary.sequences):
        assert found.sequence == reference.sequence
        assert found.count == reference.count
        assert found.estimated_count == reference.estimated_count
        assert found.base_percent == reference.base_percent
        assert found.distribution == reference.distribution


def test_native_matches_python_on_planted_repeat(tmp_path: Path) -> None:
    source = tmp_path / "contaminated.fq"
    write_fastq(source, contaminated(300, seed=11))

    native = abi.find_overrepresented_sequences(source, sampling=1)
    python_summary = python_find(source, config=OverrepConfig(sampling=1))

    assert_same(native, python_summary)
    assert [_REPEAT] == [item.sequence for item in native.sequences]


def test_native_sampling_mode_is_forwarded(tmp_path: Path) -> None:
    """采样率要一路传到原生层：它决定采样计数与报告阈值两道口径。"""
    source = tmp_path / "contaminated.fq"
    write_fastq(source, contaminated(300, seed=13))

    native = abi.find_overrepresented_sequences(source, sampling=10)
    python_summary = python_find(source, config=OverrepConfig(sampling=10))

    assert_same(native, python_summary)
    assert native.sequences[0].count == 30


def test_native_matches_python_on_clean_data(tmp_path: Path) -> None:
    """干净数据一条都不该检出——这条最容易在"阈值方向写反"时失败。"""
    source = tmp_path / "clean.fq"
    rng = random.Random(20260922)
    write_fastq(source, ["".join(rng.choices("ACGT", k=40)) for _ in range(50)])

    native = abi.find_overrepresented_sequences(source)
    python_summary = python_find(source)

    assert_same(native, python_summary)
    assert native.sequences == ()


def test_native_respects_small_base_limit(tmp_path: Path) -> None:
    """把候选扫描的碱基上限压小：候选集随之变化，两侧必须同步。"""
    source = tmp_path / "contaminated.fq"
    write_fastq(source, contaminated(60, seed=17))

    native = abi.find_overrepresented_sequences(
        source, sampling=1, base_limit=40 * 20
    )
    python_summary = python_find(
        source, config=OverrepConfig(sampling=1, base_limit=40 * 20)
    )

    assert_same(native, python_summary)


def test_native_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.fq"
    source.write_bytes(b"")

    native = abi.find_overrepresented_sequences(source)
    python_summary = python_find(source)

    assert_same(native, python_summary)
    assert native.total_reads == 0


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        abi.find_overrepresented_sequences(tmp_path / "absent.fq")


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "富集分析"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    write_fastq(source, contaminated(300, seed=19))

    native = abi.find_overrepresented_sequences(source, sampling=1)
    python_summary = python_find(source, config=OverrepConfig(sampling=1))

    assert_same(native, python_summary)
