"""在真实测序数据上验证滑窗质量剪切。

数据来源：fastp 仓库自带的 ``testdata/R1.fq``（MIT 许可），
已复制为本模块共享测试数据 ``tests/data/fastp_R1.fq``。
它是真实的 Illumina 单端数据，共 9 条记录：第 1 条长度为 0（上游数据里
确实存在这个边界记录），其余 8 条长度均为 151bp。

选它做真实数据验证的三个理由：
1. 它是**真实仪器产出**的质量分布，不是合成数据，能暴露合成数据测不到的边界；
2. 它自带长度为 0 的记录，正好覆盖"窗口放不下"的丢弃路径；
3. 它与 ``test_algorithm.py`` 里那条上游单元测试向量来自同一个项目，
   两者合起来构成"单元级逐位对齐 + 真实数据行为"的完整证据链。

期望值全部由本实现的实际输出固定而来，并在注释里写明其含义。
"""

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.quality_trimming import (
    QualityCutConfig,
    trim_and_cut,
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"

_TAIL_20 = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=20)
_TAIL_30 = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=30)
_TAIL_35 = QualityCutConfig(enabled_tail=True, window_size_tail=4, quality_tail=35)
_RIGHT_20 = QualityCutConfig(enabled_right=True, window_size_right=4, quality_right=20)


@pytest.fixture(scope="module")
def records():
    return list(read_fastq(_READS))


@pytest.fixture(scope="module")
def non_empty(records):
    return [record for record in records if record.length > 0]


def _lengths(results) -> list[int | None]:
    """把剪切结果转成便于断言的"长度或 None"列表。"""
    return [None if result is None else len(result.sequence) for result in results]


def test_reads_real_illumina_data(records) -> None:
    """能正确读入真实数据，且长度分布与上游数据一致。"""
    assert len(records) == 9
    assert records[0].length == 0
    assert all(record.length == 151 for record in records[1:])
    assert all(len(record.sequence) == len(record.quality) for record in records)


def test_empty_record_is_dropped_by_window_check(records) -> None:
    """长度为 0 的记录放不下任何窗口，应当被丢弃而不是返回空 read。"""
    empty = records[0]

    assert empty.length == 0
    assert trim_and_cut(empty.sequence, empty.quality, config=_TAIL_20) is None


def test_higher_threshold_removes_more_bases(non_empty) -> None:
    """阈值越高切得越多——这是最直观的单调性验证。

    在 cut_tail、窗口 4 下实测：
        Q20 -> 全部保持 151（这批数据尾部质量确实达标）
        Q30 -> 3 条被切到 143，共去掉 8bp
        Q35 -> 同样 3 条被切到 130，共去掉 21bp
    注意被切的始终是同一批 read，说明这是数据本身的尾部质量特征，
    而不是随机波动。
    """
    assert _lengths(
        [trim_and_cut(r.sequence, r.quality, config=_TAIL_20) for r in non_empty]
    ) == [151] * 8
    assert _lengths(
        [trim_and_cut(r.sequence, r.quality, config=_TAIL_30) for r in non_empty]
    ) == [151, 143, 151, 143, 151, 143, 151, 151]
    assert _lengths(
        [trim_and_cut(r.sequence, r.quality, config=_TAIL_35) for r in non_empty]
    ) == [151, 130, 151, 130, 151, 130, 151, 151]


def test_right_cut_is_far_more_aggressive_on_real_data(non_empty) -> None:
    """cut_right 在真实数据上比 cut_tail 激进得多。

    同样是 Q20/窗口 4，cut_tail 一个碱基都没切，而 cut_right 把多数 read
    砍到 41~88bp。原因在于判定目标完全不同：cut_tail 找的是"从右往左第一个
    达标的窗口"，只要尾部有一段好碱基就停；cut_right 找的是"从左往右第一个
    不达标的窗口"，一旦遇到局部塌陷就停，不管后面是否恢复。

    这正是真实数据里"中间低质量、后面又好回来"的典型表现，
    合成数据很难自然产生这种形状。
    """
    tail_lengths = _lengths(
        [trim_and_cut(r.sequence, r.quality, config=_TAIL_20) for r in non_empty]
    )
    right_lengths = _lengths(
        [trim_and_cut(r.sequence, r.quality, config=_RIGHT_20) for r in non_empty]
    )

    assert tail_lengths == [151] * 8
    assert right_lengths == [88, 41, 151, 41, 151, 41, 151, 151]
    # 逐条比较：cut_right 的结果绝不会比 cut_tail 保留得更多
    for tail_length, right_length in zip(tail_lengths, right_lengths):
        assert right_length <= tail_length
