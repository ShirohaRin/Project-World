"""原生层的正确性测试：与 Python 实现逐位对拍。

验证策略：**同一个输入、同一组参数，分别用 C++ 版与 Python 版处理，
输出文件必须逐字节相同**。文件内容一致同时覆盖了算法逻辑、区间计算、
FASTQ 写出格式三件事，比只比较统计数字强得多。

Python 版是"可执行的规格说明"——它已经在 5.4.1 中与上游 fastp 的
官方测试向量逐位对齐过，因此拿它当基准是可信的。

原生库未编译时整个文件跳过（而不是失败），这样在没有编译环境的地方
（例如只跑 Python 测试的 CI）也能正常收集。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import FastqStreamSummary, read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.poly_trimming import (
    PolyTrimConfig,
    trim_poly_fastq as python_poly_trim,
)
from modules.bio_analysis_function.submodules.quality_trimming import (
    QualityCutConfig,
    trim_fastq,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

# 每组参数同时给出"传给 C++ 版的关键字"与"传给 Python 版的配置"，
# 保证两边跑的是同一件事。
_CASES = {
    "无剪切": (
        {},
        None,
        0,
        0,
    ),
    "cut_tail_Q20": (
        {"enabled_tail": True, "window_size_tail": 4, "quality_tail": 20},
        QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20),
        0,
        0,
    ),
    "cut_tail_Q30": (
        {"enabled_tail": True, "window_size_tail": 4, "quality_tail": 30},
        QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=30),
        0,
        0,
    ),
    "cut_tail_Q35": (
        {"enabled_tail": True, "window_size_tail": 4, "quality_tail": 35},
        QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=35),
        0,
        0,
    ),
    "cut_front_加_cut_tail": (
        {
            "enabled_front": True,
            "enabled_tail": True,
            "window_size_front": 4,
            "window_size_tail": 4,
            "quality_front": 20,
            "quality_tail": 30,
        },
        QualityCutConfig(
            enabled_front=True,
            enabled_tail=True,
            window_size_front=4,
            window_size_tail=4,
            quality_front=20,
            quality_tail=30,
        ),
        0,
        0,
    ),
    "cut_right_Q20": (
        {"enabled_right": True, "window_size_right": 4, "quality_right": 20},
        QualityCutConfig(enabled_right=True, window_size_right=4, quality_right=20),
        0,
        0,
    ),
    "固定修剪": (
        {},
        None,
        3,
        5,
    ),
    "窗口_2_阈值_25": (
        {"enabled_tail": True, "window_size_tail": 2, "quality_tail": 25},
        QualityCutConfig(enabled_tail=True, window_size_tail=2, quality_tail=25),
        0,
        0,
    ),
}


def test_library_loads_and_reports_version() -> None:
    assert abi.native_version() == "0.17.0"


@pytest.mark.parametrize("case_name", sorted(_CASES))
def test_native_matches_python_implementation(case_name: str, tmp_path: Path) -> None:
    """C++ 版与 Python 版的输出文件必须逐字节一致，统计也必须一致。"""
    native_kwargs, python_config, front, tail = _CASES[case_name]
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.quality_trim_fastq(
        _READS,
        native_output,
        trim_front=front,
        trim_tail=tail,
        **native_kwargs,
    )
    python_summary = trim_fastq(
        _READS,
        python_output,
        front=front,
        tail=tail,
        config=python_config,
    )

    # 文件内容逐字节相同 —— 这是最强的等价证据。
    assert native_output.read_bytes() == python_output.read_bytes(), (
        f"用例 {case_name}：两边输出不一致"
    )

    # 统计也必须一致，否则说明某一侧的计数口径有偏差。
    assert isinstance(native_summary, FastqStreamSummary)
    assert native_summary == python_summary


def test_native_output_is_readable_fastq(tmp_path: Path) -> None:
    """原生版写出的文件必须是合法 FASTQ，而不是"看起来像"的字节流。"""
    output = tmp_path / "out.fq"

    summary = abi.quality_trim_fastq(
        _READS, output, enabled_tail=True, window_size_tail=4, quality_tail=30
    )

    records = list(read_fastq(output))
    assert len(records) == summary.kept_reads
    assert all(len(record.sequence) == len(record.quality) for record in records)


def test_native_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        abi.quality_trim_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_native_reports_invalid_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="必须不小于 1"):
        abi.quality_trim_fastq(
            _READS, tmp_path / "out.fq", enabled_tail=True, window_size_tail=0
        )


def test_native_reports_format_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\n")

    with pytest.raises(ValueError, match="记录不完整"):
        abi.quality_trim_fastq(broken, tmp_path / "out.fq")


def test_native_removes_partial_output_on_failure(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下残缺的输出文件。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError):
        abi.quality_trim_fastq(broken, output)

    assert not output.exists()


# --------------------------------------------------------------------------
# gzip
# --------------------------------------------------------------------------

_CUT_TAIL = {"enabled_tail": True, "window_size_tail": 4, "quality_tail": 30}
_CUT_TAIL_CONFIG = QualityCutConfig(
    enabled_tail=True, window_size_tail=4, quality_tail=30
)


def _gzip_copy(source: Path, destination: Path) -> None:
    """把文件原样压成单成员 gzip。"""
    with gzip.open(destination, "wb") as handle:
        handle.write(source.read_bytes())


def test_native_reads_gzip_input(tmp_path: Path) -> None:
    """gzip 输入必须能正确解压，结果与用未压缩输入时一致。"""
    gz_input = tmp_path / "reads.fq.gz"
    _gzip_copy(_READS, gz_input)
    output = tmp_path / "out.fq"

    summary = abi.quality_trim_fastq(gz_input, output, **_CUT_TAIL)

    # 输出压缩方式跟随输入，因此 gzip 输入会得到 gzip 输出；
    # 要比的是**解压之后**的内容。
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    reference = tmp_path / "reference.fq"
    abi.quality_trim_fastq(_READS, reference, **_CUT_TAIL)
    assert gzip.decompress(output.read_bytes()) == reference.read_bytes()
    assert summary.kept_reads > 0


def test_native_reads_multi_member_gzip(tmp_path: Path) -> None:
    """多 member 的 gzip 必须被完整解压。

    pigz、bgzip 等工具会输出多个 gzip 成员（每块一个）。自己用 inflate
    实现容易只解出第一个成员而静默截断数据，所以这条要专门守住。
    """
    payload = _READS.read_bytes()
    third = len(payload) // 3
    # 按字节切成三段，每段压成独立的 gzip 成员再拼接。
    members = [
        gzip.compress(payload[:third]),
        gzip.compress(payload[third : third * 2]),
        gzip.compress(payload[third * 2 :]),
    ]
    multi = tmp_path / "multi.fq.gz"
    multi.write_bytes(b"".join(members))

    output = tmp_path / "out.fq"
    summary = abi.quality_trim_fastq(multi, output, **_CUT_TAIL)

    reference = tmp_path / "reference.fq"
    reference_summary = abi.quality_trim_fastq(_READS, reference, **_CUT_TAIL)
    # 输出是 gzip（跟随输入），因此比较解压后的内容
    assert gzip.decompress(output.read_bytes()) == reference.read_bytes()
    assert summary == reference_summary


def test_native_writes_gzip_when_requested(tmp_path: Path) -> None:
    """显式要求压缩时，输出必须是能被标准工具读回的 gzip。"""
    output = tmp_path / "out.fq.gz"

    abi.quality_trim_fastq(_READS, output, compress=True, **_CUT_TAIL)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    with gzip.open(output, "rb") as handle:
        decoded = handle.read()

    reference = tmp_path / "reference.fq"
    abi.quality_trim_fastq(_READS, reference, **_CUT_TAIL)
    assert decoded == reference.read_bytes()


def test_native_compress_follows_input(tmp_path: Path) -> None:
    """默认压缩方式跟随输入，与输出文件名的扩展名无关。"""
    gz_input = tmp_path / "in.fq.gz"
    _gzip_copy(_READS, gz_input)

    misleading_plain = tmp_path / "out.fq"
    abi.quality_trim_fastq(gz_input, misleading_plain, **_CUT_TAIL)
    assert misleading_plain.read_bytes()[:2] == b"\x1f\x8b"

    misleading_gz = tmp_path / "out.fq.gz"
    abi.quality_trim_fastq(_READS, misleading_gz, **_CUT_TAIL)
    assert misleading_gz.read_bytes()[:2] != b"\x1f\x8b"


def test_native_matches_python_on_gzip_input(tmp_path: Path) -> None:
    """gzip 输入下与 Python 版对拍（解压后内容一致）。"""
    gz_input = tmp_path / "reads.fq.gz"
    _gzip_copy(_READS, gz_input)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.quality_trim_fastq(gz_input, native_output, **_CUT_TAIL)
    python_summary = trim_fastq(gz_input, python_output, config=_CUT_TAIL_CONFIG)

    assert gzip.decompress(native_output.read_bytes()) == gzip.decompress(
        python_output.read_bytes()
    )
    assert native_summary == python_summary


def test_native_reports_corrupt_gzip(tmp_path: Path) -> None:
    """损坏的 gzip 必须报错，不能静默当成空文件。"""
    source = tmp_path / "broken.fq.gz"
    _gzip_copy(_READS, source)
    data = bytearray(source.read_bytes())
    data[100:200] = b"\x00" * 100  # 破坏压缩数据
    source.write_bytes(bytes(data))

    with pytest.raises(ValueError):
        abi.quality_trim_fastq(source, tmp_path / "out.fq", **_CUT_TAIL)


# --------------------------------------------------------------------------
# polyG / polyX 修剪
# --------------------------------------------------------------------------

_POLY_CASES = {
    "polyX_默认": (
        {"enabled_poly_x": True, "min_length_poly_x": 10},
        PolyTrimConfig(enabled_poly_x=True, min_length_poly_x=10),
    ),
    "polyG_默认": (
        {"enabled_poly_g": True, "min_length_poly_g": 10},
        PolyTrimConfig(enabled_poly_g=True, min_length_poly_g=10),
    ),
    "两步都开": (
        {
            "enabled_poly_g": True,
            "enabled_poly_x": True,
            "min_length_poly_g": 10,
            "min_length_poly_x": 10,
        },
        PolyTrimConfig(
            enabled_poly_g=True,
            enabled_poly_x=True,
            min_length_poly_g=10,
            min_length_poly_x=10,
        ),
    ),
    "polyX_阈值_5": (
        {"enabled_poly_x": True, "min_length_poly_x": 5},
        PolyTrimConfig(enabled_poly_x=True, min_length_poly_x=5),
    ),
    "都不开启": ({}, PolyTrimConfig()),
}


@pytest.mark.parametrize("case_name", sorted(_POLY_CASES))
def test_native_poly_matches_python(case_name: str, tmp_path: Path) -> None:
    """poly 修剪：C++ 版与 Python 版的输出必须逐字节一致。"""
    native_kwargs, python_config = _POLY_CASES[case_name]
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.poly_trim_fastq(_READS, native_output, **native_kwargs)
    python_summary = python_poly_trim(_READS, python_output, config=python_config)

    assert native_output.read_bytes() == python_output.read_bytes(), (
        f"用例 {case_name}：两边输出不一致"
    )
    assert native_summary == python_summary


def test_native_poly_keeps_all_reads(tmp_path: Path) -> None:
    """poly 修剪只裁剪、不丢弃 read。"""
    output = tmp_path / "out.fq"

    summary = abi.poly_trim_fastq(
        _READS, output, enabled_poly_x=True, min_length_poly_x=10
    )

    assert summary.dropped_reads == 0
    assert summary.kept_reads == summary.total_reads


def test_native_poly_trims_the_real_poly_a_tail(tmp_path: Path) -> None:
    """真实数据里那条 polyA 尾巴必须被切掉，且切的量与 Python 版一致。"""
    output = tmp_path / "out.fq"

    summary = abi.poly_trim_fastq(
        _READS, output, enabled_poly_x=True, min_length_poly_x=10
    )

    assert summary.changed_reads == 1
    assert summary.bases_removed == 46


def test_native_poly_rejects_invalid_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="必须不小于 1"):
        abi.poly_trim_fastq(
            _READS, tmp_path / "out.fq", enabled_poly_g=True, min_length_poly_g=0
        )


def test_native_poly_handles_gzip_input(tmp_path: Path) -> None:
    """poly 修剪同样要能吃 gzip，输出跟随输入。"""
    gz_input = tmp_path / "reads.fq.gz"
    _gzip_copy(_READS, gz_input)
    output = tmp_path / "out.fq"

    summary = abi.poly_trim_fastq(
        gz_input, output, enabled_poly_x=True, min_length_poly_x=10
    )

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.bases_removed == 46


# --------------------------------------------------------------------------
# 多线程流水线
# --------------------------------------------------------------------------
#
# 流水线把输入切成批次并行处理，因此"多线程结果是否与单线程完全一致"
# 必须专门守住：批次一多，乱序完成、槽位复用、批次内丢弃 read 的压缩
# 这些小概率路径才会真正被走到。下面的合成数据刻意超过一个批次
# （1024 条），让这些路径都被覆盖。

_SYNTHETIC_READS = 5000


def _write_synthetic_fastq(path: Path, count: int, length: int) -> None:
    """写一份带质量梯度的合成 FASTQ。

    质量从 5' 端的高质量单调降到 3' 端的低质量，因此 cut_tail 会真实触发，
    且每条 read 的剪切长度各不相同——如果流水线在批次之间搞错顺序，
    这一点会立刻体现在与单线程的输出差异上。
    """
    rng = random.Random(20260917)
    bases = "ACGT"
    quality = "".join(
        chr(33 + 40 - int(35 * (position / (length - 1)) ** 1.6))
        for position in range(length)
    )
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            handle.write("@read")
            handle.write(str(index))
            handle.write("\n")
            handle.write("".join(rng.choices(bases, k=length)))
            handle.write("\n+\n")
            handle.write(quality)
            handle.write("\n")


def test_native_thread_count_does_not_change_output(tmp_path: Path) -> None:
    """线程数不得影响结果：1 / 2 / 4 / 8 线程的输出与统计必须完全相同。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)

    summaries = {}
    for threads in (1, 2, 4, 8):
        summaries[threads] = abi.quality_trim_fastq(
            source, tmp_path / f"t{threads}.fq", threads=threads, **_CUT_TAIL
        )

    reference = (tmp_path / "t1.fq").read_bytes()
    for threads in (2, 4, 8):
        assert (tmp_path / f"t{threads}.fq").read_bytes() == reference, (
            f"{threads} 线程的输出与单线程不一致"
        )
        assert summaries[threads] == summaries[1]


def test_native_parallel_matches_python_on_many_records(tmp_path: Path) -> None:
    """多批次数据上，多线程原生结果仍与 Python 实现逐字节一致。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.quality_trim_fastq(
        source, native_output, threads=4, **_CUT_TAIL
    )
    python_summary = trim_fastq(source, python_output, config=_CUT_TAIL_CONFIG)

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native_summary == python_summary
    assert native_summary.changed_reads > 0  # 数据确实触发了剪切


def test_native_parallel_drops_reads_correctly(tmp_path: Path) -> None:
    """批次内有 read 被丢弃时，压缩后的记录顺序仍然正确。

    固定前端修剪长度超过 read 长度，所有 read 都会被丢弃——
    这是流水线里"就地压掉丢弃记录"那条路径的极端情形。
    """
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, 3000, 151)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.quality_trim_fastq(
        source, native_output, threads=4, trim_front=160
    )
    python_summary = trim_fastq(source, python_output, front=160)

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native_summary == python_summary
    assert native_summary.dropped_reads == 3000
    assert native_summary.kept_reads == 0


def test_native_parallel_drops_only_some_reads(tmp_path: Path) -> None:
    """部分丢弃 + 部分保留时，被丢弃的 read 不能被错位顶替。

    取固定前端修剪 149：151 bp 的 read 只剩 2 bp 且都被保留，
    而 0 bp 的 read（真实数据里存在）会被丢弃。混在一批里，
    能同时走到"保留"与"丢弃后前移"两条分支。
    """
    source = tmp_path / "mixed.fq"
    with source.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(3000):
            if index % 3 == 0:
                continue  # 留着空位，让文件里的 read 长度不齐
            handle.write(f"@read{index}\n{'A' * 151}\n+\n{'I' * 151}\n")
            if index % 7 == 0:
                handle.write(f"@short{index}\n\n+\n\n")  # 零长度 read

    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native_summary = abi.quality_trim_fastq(
        source, native_output, threads=4, trim_front=150
    )
    python_summary = trim_fastq(source, python_output, front=150)

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native_summary == python_summary


def test_native_poly_thread_count_does_not_change_output(tmp_path: Path) -> None:
    """poly 修剪同样要保证线程数不影响结果。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)

    single = abi.poly_trim_fastq(
        source, tmp_path / "single.fq", threads=1, enabled_poly_x=True, min_length_poly_x=10
    )
    parallel = abi.poly_trim_fastq(
        source, tmp_path / "parallel.fq", threads=8, enabled_poly_x=True, min_length_poly_x=10
    )

    assert (tmp_path / "single.fq").read_bytes() == (tmp_path / "parallel.fq").read_bytes()
    assert single == parallel


def test_native_parallel_handles_gzip_input_and_output(tmp_path: Path) -> None:
    """多线程 + gzip 输入输出：解压与压缩分别在不同线程上，结果仍要一致。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)
    gz_input = tmp_path / "many.fq.gz"
    _gzip_copy(source, gz_input)

    summary = abi.quality_trim_fastq(gz_input, tmp_path / "out.fq", threads=4, **_CUT_TAIL)

    single = tmp_path / "single"       # 单线程跑同一份 gzip 输入
    abi.quality_trim_fastq(gz_input, single, threads=1, **_CUT_TAIL)

    assert gzip.decompress((tmp_path / "out.fq").read_bytes()) == gzip.decompress(
        single.read_bytes()
    )
    assert summary == abi.quality_trim_fastq(
        gz_input, tmp_path / "again.fq", threads=1, **_CUT_TAIL
    )


def test_native_handles_non_ascii_paths(tmp_path: Path) -> None:
    """含中文（非 ASCII）的输入输出路径必须能正常读写。

    这条曾经是真实缺陷：原生层直接把 UTF-8 字节交给 zlib 的窄字符接口，
    而 Windows 的窄字符接口用当前代码页（中文系统上是 GBK）解释路径，
    于是含中文的输出"写成功"却出现在一个名字乱码的文件里，
    调用方在自己的输出路径上扑空。现在统一转宽字符后再调系统接口。
    """
    folder = tmp_path / "测序数据" / "结果目录"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    _write_synthetic_fastq(source, 200, 151)
    output = folder / "修剪后.fq.gz"

    summary = abi.quality_trim_fastq(source, output, threads=4, compress=True, **_CUT_TAIL)

    assert output.exists()
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    with gzip.open(output, "rb") as handle:
        decoded = handle.read()
    assert decoded.count(b"\n") == summary.kept_reads * 4
    assert summary.kept_reads == 200

