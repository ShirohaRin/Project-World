"""双端 overlap 分析的测试。

第一组用例直接复现上游 ``OverlapAnalysis::test()`` 的两组向量（逐位比对 offset、
重叠长度与错配数），这是最硬的证据；其余用例覆盖三种几何情形（正向、反向、带缺口）
与门槛行为。

数据都是定种子的随机序列，因此结果可复现。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.paired_overlap import (
    GAP_ONLY_VECTOR,
    UPSTREAM_TEST_VECTORS,
    OverlapConfig,
    analyze_overlap,
    diff_with_one_insertion,
)
from modules.bio_analysis_function.common.sequences import reverse_complement

BASES = "ACGT"


def random_sequence(length: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return "".join(rng.choices(BASES, k=length)).encode("ascii")


# --------------------------------------------------------------------------
# 上游自带向量
# --------------------------------------------------------------------------


@pytest.mark.parametrize("vector", UPSTREAM_TEST_VECTORS)
def test_upstream_vectors(vector: dict) -> None:
    """上游 ``OverlapAnalysis::test()`` 的两组向量：结果必须逐位一致。"""
    config = OverlapConfig(
        diff_limit=vector["diff_limit"],
        require=vector["require"],
        diff_percent_limit=vector["diff_percent_limit"],
    )

    result = analyze_overlap(vector["read1"], vector["read2"], config)

    offset, overlap_len, diff = vector["expect"]
    assert result.overlapped
    assert (result.offset, result.overlap_len, result.diff) == (offset, overlap_len, diff)


def test_protected_prefix_notices_mismatches_only_after_50_bases() -> None:
    """第二组向量的含义：前 50 个碱基对得上就认，哪怕后面 30 个全错。

    这是上游刻意的设计——"先匹配上、后面才开始错"正是接头读通的样子。
    """
    vector = UPSTREAM_TEST_VECTORS[1]

    result = analyze_overlap(
        vector["read1"],
        vector["read2"],
        OverlapConfig(diff_limit=0, require=30, diff_percent_limit=0.0),
    )

    assert result.overlapped
    assert result.diff == 30  # 报出去的是整段的真实错配数，不是"前 50 个里的"


# --------------------------------------------------------------------------
# 三种几何情形
# --------------------------------------------------------------------------


def test_forward_overlap_when_insert_is_longer_than_read() -> None:
    """正向偏移（``offset > 0``）：片段**长于**读长，两条 read 只在中间重叠。

    片段 100 bp、读长 80 bp：两条 read 都读不到接头，重叠 60 bp。
    片段长度按上游口径是 ``len1 + len2 - overlap_len``。
    """
    fragment = random_sequence(100, seed=1)
    read1 = fragment[0:80]
    read2 = reverse_complement(fragment[20:100])

    result = analyze_overlap(read1, read2)

    assert result.overlapped
    assert result.offset == 20
    assert result.overlap_len == 60
    assert result.diff == 0
    assert result.insert_size == 100


def test_reverse_overlap_when_adapter_is_sequenced() -> None:
    """反向偏移（``offset < 0``）：片段**短于**读长，两端都读进了接头。

    构造方式：让 rc2 比 r1 多出一段前缀 ``X``，于是 r1 整体对应 rc2 的中间——
    这正是"r2 读进了接头"的几何形态。注意接头出现在 **rc2 的开头**：
    反向互补把 r2 尾部的接头翻到了最前面。
    """
    prefix = random_sequence(20, seed=2)
    read1 = random_sequence(80, seed=3)
    read2 = reverse_complement(prefix + read1)

    result = analyze_overlap(read1, read2)

    assert result.overlapped
    assert result.offset == -20
    assert result.overlap_len == 80
    assert result.diff == 0
    # 两端读穿时，重叠长度本身就是片段长度。
    assert result.insert_size == 80


def test_gap_path_only_accepts_tail_insertions() -> None:
    """上游的缺口路径**只认末端缺口**——这是它的实现方式导致的，不是本实现的偏差。

    ``Matcher::diffWithOneInsertion`` 的判定循环会在"前缀累计错配"第一次超限时
    直接返回 -1，**即使它已经找到过一个更小的差值**。于是插入位置离被比较区域的
    末尾越远，前缀累计越早超限，结果就是 -1；只有靠近末尾的缺口才活得下来。
    """
    read1 = random_sequence(80, seed=4)

    # 末端附近多一个碱基：认得出
    tail_gap = read1[:78] + b"A" + read1[78:]
    assert diff_with_one_insertion(tail_gap, read1, 79, 5) == 0

    # 中间多一个碱基：前缀累计很快就会超限 → 返回 -1
    middle_gap = read1[:40] + b"A" + read1[40:]
    assert diff_with_one_insertion(middle_gap, read1, 79, 5) == -1

    # 完全不同：也是 -1
    assert diff_with_one_insertion(random_sequence(80, seed=9), read1, 79, 5) == -1


def test_allow_gap_does_not_change_confident_results() -> None:
    """打开 allow_gap 不会改变"本来就很确定"的那些结果（默认向量逐位一致）。"""
    for vector in UPSTREAM_TEST_VECTORS:
        base = OverlapConfig(
            diff_limit=vector["diff_limit"],
            require=vector["require"],
            diff_percent_limit=vector["diff_percent_limit"],
        )
        with_gap = OverlapConfig(
            diff_limit=vector["diff_limit"],
            require=vector["require"],
            diff_percent_limit=vector["diff_percent_limit"],
            allow_gap=True,
        )

        strict = analyze_overlap(vector["read1"], vector["read2"], base)
        lenient = analyze_overlap(vector["read1"], vector["read2"], with_gap)

        assert strict == lenient


def test_allow_gap_helps_only_in_a_narrow_window() -> None:
    """``allow_gap`` 会改变结论，但窗口很窄——这组向量是窗口内的实例。

    三条要同时成立：

    1. **重叠区不长**。无缺口那一轮只判定前 50 个碱基，重叠区一大，它在正确
       错位量上就先认下了（实测：读长 30~151、重叠动辄上百的随机用例里，
       打开开关一次都没改变结论）；
    2. **indel 落在靠末端的一小段里**。``diffWithOneInsertion`` 的前缀累计一旦
       超限就直接返回 -1，中间部位的 indel 会被判死（见上一个用例）；
    3. **无缺口那一轮恰好过不去**。indel 把后面的碱基整体错位，按位比对时
       差得太多，于是前 50 个碱基里的错配数超限。

    这也说明：实用价值主要落在"短重叠 + 单碱基 indel"这一类难例上，而不是
    常规的双端数据。

    向量本身是 ``algorithm.GAP_ONLY_VECTOR``，公共层与原生层的测试共用这一份。
    """
    vector = GAP_ONLY_VECTOR
    strict_config = OverlapConfig(
        diff_limit=vector["diff_limit"], require=vector["require"]
    )
    gap_config = OverlapConfig(
        diff_limit=vector["diff_limit"], require=vector["require"], allow_gap=True
    )

    strict = analyze_overlap(vector["read1"], vector["read2"], strict_config)
    lenient = analyze_overlap(vector["read1"], vector["read2"], gap_config)

    assert not strict.overlapped
    assert lenient.overlapped
    assert lenient.has_gap
    assert (lenient.offset, lenient.overlap_len, lenient.diff) == vector["expect"]


# --------------------------------------------------------------------------
# 门槛与配置
# --------------------------------------------------------------------------


def test_random_pair_is_not_overlapped() -> None:
    result = analyze_overlap(random_sequence(150, seed=5), random_sequence(150, seed=6))

    assert not result.overlapped
    assert result.insert_size == 0
    assert "未检出" in result.render()


def test_overlap_shorter_than_require_is_rejected() -> None:
    """重叠长度达不到 require 就一律不认（上游注释：不确定就别认）。

    注意扫描上界：正向那一段是 ``offset < len1 - require``，所以"只重叠到末尾
    ``require`` 个碱基"的位置**不会被试到**。这里把门槛降到 5 才认出来
    （重叠 10 个碱基）。
    """
    fragment = random_sequence(100, seed=7)
    read1 = fragment[0:80]
    read2 = reverse_complement(fragment[70:100])  # 只重叠 10 个碱基

    assert not analyze_overlap(read1, read2).overlapped

    relaxed = analyze_overlap(read1, read2, OverlapConfig(require=5))
    assert relaxed.overlapped
    assert relaxed.overlap_len == 10


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError, match="require"):
        OverlapConfig(require=0)
    with pytest.raises(ValueError, match="diff_limit"):
        OverlapConfig(diff_limit=-1)
    with pytest.raises(ValueError, match="diff_percent_limit"):
        OverlapConfig(diff_percent_limit=1.5)
