"""原生去重的正确性测试：与 Python 实现逐位对拍。

验证策略与其他算法一致：**同一输入、同一组参数，两侧分别处理，输出文件必须
逐字节相同**，统计也必须相同。

本模块多一层强度：**位图大小直接决定判重结论**（位置向量要对位图取模，
位图小则假阳性多），所以只要哈希的某一位与 Python 侧不同，"哪几条被判为重复"
就会立刻分叉。因此除了常规用例，还有一组**极小位图 + 大量近重复数据**的用例，
它等价于对整条哈希链（哈希表、素数表、uint64 回绕、取模）做逐位对拍——
若把素数表写成"前 N 个连续素数"，常规用例可能照样通过（都是"不重复"），
这组用例会当场暴露。

原生库未编译时整个文件跳过（而不是失败）。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.deduplication import (
    deduplicate_fastq as python_deduplicate_fastq,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

#: 两侧必须传同一个位图大小，否则假阳性率不同、结论就会分叉。
_SMALL_BUFFER = 1 << 16
#: 极小位图（512 字节 / 缓冲区）：用来把"哈希实现是否逐位一致"压出来。
#: 位图越小、数据越多，假阳性越多，判定序列对哈希的每一位就越敏感。
_TINY_BUFFER = 512

_Q40 = chr(40 + 33)


def write_fastq_file(path: Path, records: list[tuple[str, str]]) -> None:
    """按 (名字, 序列) 写一份 FASTQ，质量统一用 Q40。"""
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence in records:
            handle.write(f"@{name}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


def _write_synthetic(path: Path, count: int, length: int, seed: int) -> None:
    """造一份"重复与近重复混杂"的合成 FASTQ。

    每 200 条构成一轮：第 0~99 条是独特的随机序列（会被后续轮次复用成重复），
    第 100~199 条是"把前一条改一个碱基"得到的近重复。这样重复率既不低到
    让判重路径走不到，也不高到让所有 read 都撞成一条。
    """
    rng = random.Random(seed)
    unique = ["".join(rng.choices("ACGT", k=length)) for _ in range(100)]
    near = []
    for sequence in unique:
        position = rng.randrange(length)
        replacement = rng.choice([base for base in "ACGT" if base != sequence[position]])
        near.append(sequence[:position] + replacement + sequence[position + 1 :])

    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            step = index % 200
            sequence = unique[step] if step < 100 else near[step - 100]
            handle.write(f"@read{index}\n{sequence}\n+\n{_Q40 * length}\n")


# ---------------------------------------------------------------------------
# 单端
# ---------------------------------------------------------------------------


def test_native_single_matches_python_on_real_data(tmp_path: Path) -> None:
    """真实小数据上，两侧的摘要与输出文件都一致。"""
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native = abi.deduplicate_fastq(
        _READS, native_output, buffer_bytes=_SMALL_BUFFER
    )
    python_summary = python_deduplicate_fastq(
        _READS, python_output, buffer_bytes=_SMALL_BUFFER
    )

    assert native_output.read_bytes() == python_output.read_bytes()
    assert _comparable(native) == _comparable(python_summary)


def test_native_analysis_only_writes_nothing(tmp_path: Path) -> None:
    """只评估模式：C 侧不产文件、dropped 为 0，与 Python 侧一致。"""
    native = abi.deduplicate_fastq(_READS, buffer_bytes=_SMALL_BUFFER)
    python_summary = python_deduplicate_fastq(_READS, buffer_bytes=_SMALL_BUFFER)

    assert native.dropped_reads == 0
    assert list(tmp_path.iterdir()) == []
    assert _comparable(native) == _comparable(python_summary)


def test_native_single_matches_python_on_synthetic_data(tmp_path: Path) -> None:
    """1200 条、含重复与近重复：输出逐字节一致、统计一致。"""
    source = tmp_path / "synthetic.fq"
    _write_synthetic(source, 1200, 100, seed=20260922)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native = abi.deduplicate_fastq(
        source, native_output, buffer_bytes=_SMALL_BUFFER
    )
    python_summary = python_deduplicate_fastq(
        source, python_output, buffer_bytes=_SMALL_BUFFER
    )

    assert native_output.read_bytes() == python_output.read_bytes()
    assert _comparable(native) == _comparable(python_summary)
    assert native.duplicate_reads > 0
    assert native.kept_reads > 0


def _write_distinct(path: Path, count: int, length: int, seed: int) -> None:
    """写一份**全部互不相同**的随机序列。

    真重复为零，因此判重路径上任何"重复"都只可能来自**假阳性**——
    假阳性的具体位置完全由哈希决定，这正是对拍哈希实现的最强场景。
    """
    rng = random.Random(seed)
    seen: set[str] = set()
    with path.open("w", encoding="ascii", newline="\n") as handle:
        while len(seen) < count:
            sequence = "".join(rng.choices("ACGT", k=length))
            if sequence in seen:
                continue
            seen.add(sequence)
            handle.write(f"@read{len(seen)}\n{sequence}\n+\n{_Q40 * length}\n")


def test_native_matches_python_under_heavy_false_positives(tmp_path: Path) -> None:
    """全独特序列 + 极小位图：把整条哈希链压到逐位一致。

    位图只有 512 字节（4096 比特/缓冲区）而输入是 3000 条互不相同的 60bp 序列，
    真重复为零、假阳性成千上百。此时"哪几条被判为重复"**完全**由哈希的每一位
    决定：哈希表、素数表、uint64 回绕、取模，任何一处与 Python 侧不同，
    判定序列立刻分叉。

    这条用例是**故障注入验证过的**：把 C 侧素数表改成"从 10001 起的前 N 个素数"
    （而非上游的"每万区间取第一个素数"）后，本用例失败；改回即通过。
    只靠"真重复很多"的常规用例抓不住这种错——那些用例里重复本来就是真的，
    哈希怎么变都判得出来。
    """
    source = tmp_path / "distinct.fq"
    _write_distinct(source, 3000, 60, seed=13)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native = abi.deduplicate_fastq(
        source, native_output, buffer_bytes=_TINY_BUFFER
    )
    python_summary = python_deduplicate_fastq(
        source, python_output, buffer_bytes=_TINY_BUFFER
    )

    assert native_output.read_bytes() == python_output.read_bytes()
    assert _comparable(native) == _comparable(python_summary)
    # 真重复为零，所以这里每一条都是假阳性——它证明位图确实被挤到碰撞。
    assert native.duplicate_reads > 0
    assert _exact_duplicate_count(source) == 0


def test_native_dedup_keeps_first_occurrence(tmp_path: Path) -> None:
    """保序：留下的是"第一次出现"的那条。"""
    source = tmp_path / "reads.fq"
    sequence = "ACGT" * 25
    write_fastq_file(
        source,
        [("first", sequence), ("second", sequence), ("third", sequence)],
    )
    output = tmp_path / "native.fq"

    summary = abi.deduplicate_fastq(source, output, buffer_bytes=_SMALL_BUFFER)

    assert (summary.total_reads, summary.duplicate_reads) == (3, 2)
    assert [record.name for record in read_fastq(output)] == ["first"]


# ---------------------------------------------------------------------------
# 双端
# ---------------------------------------------------------------------------


def write_paired(tmp_path: Path, count: int, seed: int) -> tuple[Path, Path]:
    """成对的 R1/R2：第 index 与 index+3 对相同，其余各自独特。"""
    rng = random.Random(seed)
    base1 = ["".join(rng.choices("ACGT", k=80)) for _ in range(count)]
    base2 = ["".join(rng.choices("ACGT", k=80)) for _ in range(count)]
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(
        read1, [(f"read{index}/1", base1[index % 3]) for index in range(count)]
    )
    write_fastq_file(
        read2, [(f"read{index}/2", base2[index % 3]) for index in range(count)]
    )
    return read1, read2


@pytest.mark.parametrize("dedup", [False, True])
def test_native_paired_matches_python(tmp_path: Path, dedup: bool) -> None:
    read1, read2 = write_paired(tmp_path, 300, seed=7)
    native1 = tmp_path / "native_R1.fq"
    native2 = tmp_path / "native_R2.fq"
    python1 = tmp_path / "python_R1.fq"
    python2 = tmp_path / "python_R2.fq"

    native = abi.deduplicate_fastq(
        read1,
        native1 if dedup else None,
        read2_path=read2,
        output2_path=native2 if dedup else None,
        buffer_bytes=_SMALL_BUFFER,
    )
    python_summary = python_deduplicate_fastq(
        read1,
        python1 if dedup else None,
        read2_path=read2,
        output2_path=python2 if dedup else None,
        buffer_bytes=_SMALL_BUFFER,
    )

    assert native.paired is True
    assert _comparable(native) == _comparable(python_summary)
    assert native.duplicate_reads > 0
    if dedup:
        assert native1.read_bytes() == python1.read_bytes()
        assert native2.read_bytes() == python2.read_bytes()
        assert len(list(read_fastq(native1))) == len(list(read_fastq(native2)))


def test_native_paired_mismatched_counts_reported(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq_file(read1, [("a/1", "ACGT" * 20), ("b/1", "TTTT" * 20)])
    write_fastq_file(read2, [("a/2", "GGGG" * 20)])
    output1 = tmp_path / "out_R1.fq"
    output2 = tmp_path / "out_R2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        abi.deduplicate_fastq(
            read1,
            output1,
            read2_path=read2,
            output2_path=output2,
            buffer_bytes=_SMALL_BUFFER,
        )

    assert not output1.exists()
    assert not output2.exists()


# ---------------------------------------------------------------------------
# 档位、压缩、路径
# ---------------------------------------------------------------------------


def test_native_reports_accuracy_level(tmp_path: Path) -> None:
    """C 侧回填实际使用的档位：只评估 1、去重 3、显式传值照传。"""
    analyzed = abi.deduplicate_fastq(_READS, buffer_bytes=_SMALL_BUFFER)
    assert analyzed.accuracy_level == 1

    deduped = abi.deduplicate_fastq(
        _READS, tmp_path / "out.fq", buffer_bytes=_SMALL_BUFFER
    )
    assert deduped.accuracy_level == 3

    explicit = abi.deduplicate_fastq(
        _READS, buffer_bytes=_SMALL_BUFFER, accuracy_level=6
    )
    assert explicit.accuracy_level == 6


def test_native_rejects_invalid_accuracy_level(tmp_path: Path) -> None:
    """档位越界报错；``0`` 与负数表示"按模式取默认"，不是越界。"""
    with pytest.raises(ValueError, match="accuracy_level"):
        abi.deduplicate_fastq(_READS, accuracy_level=7)
    with pytest.raises(ValueError, match="accuracy_level"):
        abi.deduplicate_fastq(_READS, accuracy_level=99)
    assert abi.deduplicate_fastq(_READS, accuracy_level=0).accuracy_level == 1
    assert abi.deduplicate_fastq(_READS, accuracy_level=-1).accuracy_level == 1


def test_native_handles_gzip_input_and_output(tmp_path: Path) -> None:
    """gzip 输入 → 输出跟随输入压缩，内容与 Python 版一致。"""
    source = tmp_path / "synthetic.fq"
    _write_synthetic(source, 400, 100, seed=3)
    gz_input = tmp_path / "synthetic.fq.gz"
    with gzip.open(gz_input, "wb") as handle:
        handle.write(source.read_bytes())

    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"
    native = abi.deduplicate_fastq(
        gz_input, native_output, buffer_bytes=_SMALL_BUFFER
    )
    python_summary = python_deduplicate_fastq(
        gz_input, python_output, buffer_bytes=_SMALL_BUFFER
    )

    assert native_output.read_bytes()[:2] == b"\x1f\x8b"
    # gzip 头的元数据（原文件名、时间戳）两侧写法不同，因此比解压后的内容。
    assert gzip.decompress(native_output.read_bytes()) == gzip.decompress(
        python_output.read_bytes()
    )
    assert _comparable(native) == _comparable(python_summary)


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    folder = tmp_path / "测序数据" / "去重结果"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    _write_synthetic(source, 200, 100, seed=5)
    output = folder / "去重后.fq.gz"

    summary = abi.deduplicate_fastq(
        source, output, compress=True, buffer_bytes=_SMALL_BUFFER
    )

    assert output.exists()
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.total_reads == 200


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        abi.deduplicate_fastq(tmp_path / "absent.fq", buffer_bytes=_SMALL_BUFFER)


def test_native_rejects_invalid_path_combinations(tmp_path: Path) -> None:
    read1, read2 = write_paired(tmp_path, 10, seed=11)
    output1 = tmp_path / "out_R1.fq"
    output2 = tmp_path / "out_R2.fq"

    with pytest.raises(ValueError, match="同时给出两份输出路径"):
        abi.deduplicate_fastq(
            read1, output1, read2_path=read2, buffer_bytes=_SMALL_BUFFER
        )
    with pytest.raises(ValueError, match="第二份输出路径"):
        abi.deduplicate_fastq(
            read1, output1, output2_path=output2, buffer_bytes=_SMALL_BUFFER
        )


def test_native_removes_partial_output_on_failure(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下残缺的输出文件。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        abi.deduplicate_fastq(broken, output, buffer_bytes=_SMALL_BUFFER)

    assert not output.exists()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _comparable(summary) -> tuple[int, int, int, int, int, int]:
    """取出两侧同名同义的六个字段，用于跨类型比较。"""
    return (
        summary.total_reads,
        summary.duplicate_reads,
        summary.kept_reads,
        summary.dropped_reads,
        summary.bases_before,
        summary.bases_after,
    )


def _exact_duplicate_count(source: Path) -> int:
    """精确去重（不看布隆过滤器），用来证明假阳性确实存在。"""
    seen: set[bytes] = set()
    duplicates = 0
    for record in read_fastq(source):
        if record.sequence in seen:
            duplicates += 1
        else:
            seen.add(record.sequence)
    return duplicates
