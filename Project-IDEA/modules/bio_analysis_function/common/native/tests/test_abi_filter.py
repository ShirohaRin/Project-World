"""原生 reads 过滤的正确性测试：与 Python 实现逐位对拍 + 失败分类一致。

验证策略与质量剪切、poly 修剪一致：**同一输入、同一组参数，两侧分别处理，
输出文件必须逐字节相同**；本算法还多一项——**按原因分类的计数也必须相同**，
因为过滤的价值一半在"丢了多少"，另一半在"为什么丢"。

另有两条不依赖 Python 实现的断言（避免"两边一起错"）：

- 结果码映射：构造每种失败各一条的输入，直接核对分类计数；
- 分类之和 = 丢弃总数，通过条的碱基数 = 写出的碱基数。

原生库未编译时整个文件跳过（而不是失败）。
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.submodules.read_filtering import (
    FAIL_COMPLEXITY,
    FAIL_LENGTH,
    FAIL_N_BASE,
    FAIL_QUALITY,
    FAIL_TOO_LONG,
    ReadFilterConfig,
    filter_fastq,
)

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

# 每组参数同时给出"传给原生版的关键字"与"传给 Python 版的配置"，
# 保证两边跑的是同一件事。两个比例参数在两侧的写法不同：
# 原生层按百分数（40.0）/ 比例（0.3）收，Python 侧本来就是这个写法。
_FILTER_CASES = {
    "默认参数": ({}, ReadFilterConfig()),
    "关闭质量过滤": (
        {"enabled_quality": False},
        ReadFilterConfig(enabled_quality=False),
    ),
    "严格质量_Q30_零容忍": (
        {"qualified_quality_phred": 30, "unqualified_percent_limit": 0.0},
        ReadFilterConfig(qualified_quality_phred=30, unqualified_percent_limit=0.0),
    ),
    "低质量比例_20%": (
        {"unqualified_percent_limit": 20.0},
        ReadFilterConfig(unqualified_percent_limit=20.0),
    ),
    "平均质量_Q30": (
        {"average_qual": 30},
        ReadFilterConfig(average_qual=30),
    ),
    "N_上限_0": (
        {"n_base_limit": 0},
        ReadFilterConfig(n_base_limit=0),
    ),
    "最短长度_150": (
        {"required_length": 150},
        ReadFilterConfig(required_length=150),
    ),
    "最长长度_100": (
        {"max_length": 100},
        ReadFilterConfig(max_length=100),
    ),
    "低复杂度开启_阈值30%": (
        {"enabled_complexity": True, "complexity_threshold": 0.3},
        ReadFilterConfig(enabled_complexity=True, complexity_threshold=0.3),
    ),
    "最长长度_100_加_低复杂度": (
        {"max_length": 100, "enabled_complexity": True},
        ReadFilterConfig(max_length=100, enabled_complexity=True),
    ),
    "全部关闭": (
        {
            "enabled_quality": False,
            "enabled_length": False,
            "enabled_complexity": False,
        },
        ReadFilterConfig(
            enabled_quality=False, enabled_length=False, enabled_complexity=False
        ),
    ),
}


@pytest.mark.parametrize("case_name", sorted(_FILTER_CASES))
def test_native_filter_matches_python(case_name: str, tmp_path: Path) -> None:
    """真实数据上，两侧的输出文件逐字节相同、分类计数也相同。"""
    native_kwargs, python_config = _FILTER_CASES[case_name]
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    native = abi.read_filter_fastq(_READS, native_output, **native_kwargs)
    python_summary = filter_fastq(_READS, python_output, config=python_config)

    assert native_output.read_bytes() == python_output.read_bytes(), (
        f"用例 {case_name}：两边输出不一致"
    )
    assert native.failures == python_summary.failures, f"用例 {case_name}：分类计数不一致"
    assert (
        native.total_reads,
        native.kept_reads,
        native.dropped_reads,
        native.bases_before,
        native.bases_after,
    ) == (
        python_summary.total_reads,
        python_summary.kept_reads,
        python_summary.dropped_reads,
        python_summary.bases_before,
        python_summary.bases_after,
    )


def test_native_filter_reports_each_reason(tmp_path: Path) -> None:
    """结果码映射：每种失败各一条的输入，直接核对分类计数。

    这条不依赖 Python 实现，是分类统计的独立判据。
    """
    source = tmp_path / "reasons.fq"
    good = "ACGT" * 40  # 160 bp，复杂度 1.0
    with source.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"@good\n{good}\n+\n{'I' * len(good)}\n")
        # 低质量：150 个 A、全部 Q10（比例 100% > 40%）
        handle.write(f"@low_quality\n{'A' * 150}\n+\n{chr(10 + 33) * 150}\n")
        # N 过多：20 个 N，质量达标
        handle.write(f"@many_n\n{'N' * 20}{'A' * 130}\n+\n{'I' * 150}\n")
        # 过短：10 bp
        handle.write(f"@short\n{'A' * 10}\n+\n{'I' * 10}\n")
        # 空 read：长度为 0，同样计入"过短"
        handle.write("@empty\n\n+\n\n")
        # 过长：200 bp，且复杂度与质量都达标
        handle.write(f"@long\n{good}{'ACGT' * 10}\n+\n{'I' * 200}\n")
        # 低复杂度：150 个 A，质量达标、长度达标
        handle.write(f"@low_complexity\n{'A' * 150}\n+\n{'I' * 150}\n")

    output = tmp_path / "passed.fq"
    summary = abi.read_filter_fastq(
        source, output, max_length=180, enabled_complexity=True
    )

    assert summary.total_reads == 7
    assert summary.kept_reads == 1
    assert summary.failures == {
        FAIL_QUALITY: 1,
        FAIL_N_BASE: 1,
        FAIL_LENGTH: 2,  # 过短 + 空 read
        FAIL_TOO_LONG: 1,
        FAIL_COMPLEXITY: 1,
    }
    # 分类之和必须等于丢弃总数，否则统计口径就散了。
    assert sum(summary.failures.values()) == summary.dropped_reads
    assert summary.bases_after == len(good)


def test_native_filter_does_not_rewrite_sequences(tmp_path: Path) -> None:
    """过滤只决定去留，写出的记录与输入逐字节一致（只去掉没通过的）。"""
    source = tmp_path / "reads.fq"
    with source.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"@keep_me\n{'ACGT' * 40}\n+\n{'I' * 160}\n")
        handle.write(f"@drop_me\n{'A' * 10}\n+\n{'I' * 10}\n")

    output = tmp_path / "passed.fq"
    abi.read_filter_fastq(source, output)

    assert output.read_bytes() == f"@keep_me\n{'ACGT' * 40}\n+\n{'I' * 160}\n".encode(
        "ascii"
    )


# --------------------------------------------------------------------------
# 合成数据：多批次、分流、线程数
# --------------------------------------------------------------------------

_SYNTHETIC_READS = 5000


def _write_synthetic_fastq(path: Path, count: int, length: int) -> None:
    """写一份"什么毛病都有"的合成 FASTQ。

    质量随位置下降（会撞上质量与长度判据）、每 17 条掺一段 N、
    每 23 条掺一段同聚物（会撞上低复杂度判据）。这样一条用例就能覆盖
    四类判据与"保留"分支，并且超过一个批次（1024 条），
    把流水的分批与分类合并路径也走到。
    """
    rng = random.Random(20260917)
    bases = "ACGT"
    quality = "".join(
        chr(33 + 40 - int(35 * (position / (length - 1)) ** 1.6))
        for position in range(length)
    )
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            sequence = "".join(rng.choices(bases, k=length))
            if index % 17 == 0:
                sequence = "N" * 12 + sequence[12:]
            if index % 23 == 0:
                sequence = "A" * 30 + sequence[30:]
            handle.write(f"@read{index}\n{sequence}\n+\n{quality}\n")


def test_native_filter_matches_python_on_many_records(tmp_path: Path) -> None:
    """多批次数据上，原生与 Python 的输出、分类计数仍然完全一致。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)
    native_output = tmp_path / "native.fq"
    python_output = tmp_path / "python.fq"

    kwargs = {"enabled_complexity": True, "required_length": 100}
    config = ReadFilterConfig(enabled_complexity=True, required_length=100)

    native = abi.read_filter_fastq(source, native_output, **kwargs)
    python_summary = filter_fastq(source, python_output, config=config)

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native.failures == python_summary.failures
    assert native.dropped_reads > 0  # 数据确实触发了过滤
    assert native.kept_reads > 0


def test_native_filter_thread_count_does_not_change_result(tmp_path: Path) -> None:
    """线程数不得影响结果：1 / 2 / 4 / 8 线程的输出与分类完全相同。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)
    kwargs = {"enabled_complexity": True}

    summaries = {}
    for threads in (1, 2, 4, 8):
        summaries[threads] = abi.read_filter_fastq(
            source, tmp_path / f"t{threads}.fq", threads=threads, **kwargs
        )

    reference = (tmp_path / "t1.fq").read_bytes()
    for threads in (2, 4, 8):
        assert (tmp_path / f"t{threads}.fq").read_bytes() == reference, (
            f"{threads} 线程的输出与单线程不一致"
        )
        assert summaries[threads] == summaries[1]


# --------------------------------------------------------------------------
# 失败归档（failed_out）
# --------------------------------------------------------------------------


def test_native_failed_output_matches_python(tmp_path: Path) -> None:
    """失败归档与 Python 版逐字节一致：原序列 + 名字后追加原因标签。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, _SYNTHETIC_READS, 151)
    kwargs = {"enabled_complexity": True, "required_length": 100}
    config = ReadFilterConfig(enabled_complexity=True, required_length=100)

    native_failed = tmp_path / "native_failed.fq"
    python_failed = tmp_path / "python_failed.fq"
    native = abi.read_filter_fastq(
        source, tmp_path / "native.fq", failed_output_path=native_failed, **kwargs
    )
    python_summary = filter_fastq(
        source,
        tmp_path / "python.fq",
        failed_output_path=python_failed,
        config=config,
    )

    assert native_failed.read_bytes() == python_failed.read_bytes()
    # 归档的条数就是丢弃数——两处数字必须对得上，否则归档是残的。
    assert native.dropped_reads == python_summary.dropped_reads
    failed_records = list(read_fastq(native_failed))
    assert len(failed_records) == native.dropped_reads
    # 每条都带上了失败原因标签，且只有"名字 + 空格 + 标签"这一处改动。
    assert all(" " in record.name for record in failed_records)
    tagged = [record.name.split(" ")[0] for record in failed_records]
    originals = {f"read{index}" for index in range(_SYNTHETIC_READS)}
    assert set(tagged) <= originals


def test_native_failed_output_follows_output_compression(tmp_path: Path) -> None:
    """归档与主输出同压缩方式：显式要 gzip 时两份都是 gzip。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, 300, 151)
    passed = tmp_path / "passed.fq.gz"
    failed = tmp_path / "failed.fq.gz"

    summary = abi.read_filter_fastq(
        source, passed, failed_output_path=failed, compress=True, enabled_complexity=True
    )

    assert passed.read_bytes()[:2] == b"\x1f\x8b"
    assert failed.read_bytes()[:2] == b"\x1f\x8b"
    assert len(list(read_fastq(failed))) == summary.dropped_reads


def test_native_failed_output_removed_on_failure(tmp_path: Path) -> None:
    """中途失败时，主输出与失败归档两个半成品都要删干净。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"
    failed = tmp_path / "failed.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        abi.read_filter_fastq(broken, output, failed_output_path=failed)

    assert not output.exists()
    assert not failed.exists()


# --------------------------------------------------------------------------
# 压缩与路径
# --------------------------------------------------------------------------


def test_native_filter_handles_gzip_input_and_output(tmp_path: Path) -> None:
    """gzip 输入 → 输出跟随输入压缩，内容与 Python 版一致。"""
    source = tmp_path / "many.fq"
    _write_synthetic_fastq(source, 300, 151)
    gz_input = tmp_path / "many.fq.gz"
    with gzip.open(gz_input, "wb") as handle:
        handle.write(source.read_bytes())

    output = tmp_path / "passed.fq"
    summary = abi.read_filter_fastq(gz_input, output, enabled_complexity=True)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    python_output = tmp_path / "python.fq.gz"
    python_summary = filter_fastq(
        gz_input, python_output, config=ReadFilterConfig(enabled_complexity=True)
    )
    assert gzip.decompress(output.read_bytes()) == gzip.decompress(
        python_output.read_bytes()
    )
    # 两侧的统计类型不同（公共层不复用子模块的类型），逐字段比。
    assert summary.failures == python_summary.failures
    assert (summary.total_reads, summary.kept_reads, summary.dropped_reads) == (
        python_summary.total_reads,
        python_summary.kept_reads,
        python_summary.dropped_reads,
    )


def test_native_filter_handles_non_ascii_paths(tmp_path: Path) -> None:
    """含中文的输入输出路径必须能正常读写。"""
    folder = tmp_path / "测序数据" / "过滤结果"
    folder.mkdir(parents=True)
    source = folder / "样本1.fq"
    _write_synthetic_fastq(source, 200, 151)
    output = folder / "通过.fq.gz"

    summary = abi.read_filter_fastq(source, output, compress=True)

    assert output.exists()
    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert summary.total_reads == 200


# --------------------------------------------------------------------------
# 错误处理
# --------------------------------------------------------------------------


def test_native_filter_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        abi.read_filter_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_native_filter_reports_invalid_parameters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="qualified_quality_phred"):
        abi.read_filter_fastq(_READS, tmp_path / "out.fq", qualified_quality_phred=94)
    with pytest.raises(ValueError, match="unqualified_limit_bp"):
        abi.read_filter_fastq(_READS, tmp_path / "out.fq", unqualified_percent_limit=101.0)
    with pytest.raises(ValueError, match="complexity_threshold_bp"):
        abi.read_filter_fastq(_READS, tmp_path / "out.fq", complexity_threshold=1.5)


def test_native_filter_removes_partial_output_on_failure(tmp_path: Path) -> None:
    """输入在记录中途截断时，不能留下残缺的输出文件。"""
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        abi.read_filter_fastq(broken, output)

    assert not output.exists()
