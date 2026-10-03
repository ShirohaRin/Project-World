"""接头裁剪算法本体的测试。

第一组用例直接复现上游 ``AdapterTrimmer::test()`` 的两组向量（逐位比对），
这是最硬的证据；其余用例覆盖它在真实数据上会遇到的四种边界：

- **负起点**（接头二聚体：read 开头少几个碱基）；
- **允许 1 个插入**（read 中间多一个碱基）；
- **允许 1 个缺失**（read 少一个碱基）；
- **小于最短匹配长度**（接头太短就直接不裁）。
"""

from __future__ import annotations

import pytest

from modules.bio_analysis_function.submodules.adapter_trimming.algorithm import (
    AdapterTrimConfig,
    match_required_for,
    trim_adapter,
    trim_adapters,
)

TRUSEQ = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
_TRUSEQ_BYTES = TRUSEQ.encode("ascii")


def quality_of(sequence: bytes) -> bytes:
    return b"I" * len(sequence)


# --------------------------------------------------------------------------
# 上游自带测试向量
# --------------------------------------------------------------------------


def test_upstream_vector_single_adapter() -> None:
    """上游 ``AdapterTrimmer::test()`` 的第一组向量：单条指定接头。"""
    read = b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG"
    quality = b"///EEEEEEEEEEEEEEEEEEEEEEEEEE////EEEEEEEEEEEEE////E////E"
    adapter = "TTTTCCACGGGGATACTACTG"

    result = trim_adapter(read, quality, adapter)

    assert result.trimmed
    assert result.sequence == b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAA"
    assert result.removed == b"TTTTCCCCGGGG"
    assert result.position == 44
    # 质量串必须同步截短
    assert len(result.quality) == len(result.sequence)


def test_upstream_vector_multi_sequences() -> None:
    """上游第二组向量：候选表模式（依次尝试，命中即裁）。"""
    read = (
        b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG"
        b"AAATTTCCCGGGAAATTTCCCGGGATCGATCGATCGATCGAATTCC"
    )
    adapters = [
        "GCTAGCTAGCTAGCTA",
        "AAATTTCCCGGGAAATTTCCCGGG",
        "ATCGATCGATCGATCG",
        "AATTCCGGAATTCCGG",
    ]

    result = trim_adapters(read, quality_of(read), adapters)

    assert result.trimmed
    assert result.sequence == b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG"


# --------------------------------------------------------------------------
# 边界：负起点、插入、缺失、最短匹配
# --------------------------------------------------------------------------


def test_negative_start_handles_adapter_dimer() -> None:
    """read 开头少了接头的头 4 个碱基（接头二聚体）时，整条 read 都算接头。

    上游从负位置开始扫就是为了这个：Illumina 的 A-tailing 会让接头二聚体的
    read 开头少一个 A，因此比对允许把接头的头几个碱基跳过。
    """
    read = _TRUSEQ_BYTES[4:]  # 接头从第 5 个碱基开始的 29 个碱基

    result = trim_adapter(read, quality_of(read), TRUSEQ)

    assert result.trimmed
    assert result.sequence == b""
    assert result.position == -4
    assert result.removed == _TRUSEQ_BYTES[: len(_TRUSEQ_BYTES) - 4]


def test_tolerates_one_extra_base_in_read() -> None:
    """read 中间多一个碱基（测序插入）也要能认出来。"""
    read = _TRUSEQ_BYTES[:10] + b"T" + _TRUSEQ_BYTES[10:]

    result = trim_adapter(read, quality_of(read), TRUSEQ)

    assert result.trimmed
    assert result.position == 0
    assert result.sequence == b""


def test_tolerates_one_missing_base_in_read() -> None:
    """read 少一个碱基（测序缺失）也要能认出来。

    **切点是 21 而不是 0**，这暴露了上游一个值得记住的写法：插入/缺失那两段比对
    锚定在 read 的**开头**，随 pos 变动的只有比对长度。所以这条 read 是被"插入分支"
    在 pos=21 处命中的（那时比对长度落到 10，恰好只比到 read 开头的 10 个碱基）。
    结果仍然是"认出来了"，但切点并不落在接头的真实位置——见 README 的已知限制。
    """
    read = _TRUSEQ_BYTES[:10] + _TRUSEQ_BYTES[11:]

    result = trim_adapter(read, quality_of(read), TRUSEQ)

    assert result.trimmed
    assert result.position == 21
    assert result.sequence == read[:21]


def test_adapter_shorter_than_match_required_is_ignored() -> None:
    """接头比最短匹配长度还短时直接不裁（上游 `if(alen < matchReq) return false`）。"""
    read = b"ACGTACGTACGTACGTACGT"

    result = trim_adapter(read, quality_of(read), "ACG")

    assert not result.trimmed
    assert result.sequence == read


def test_gap_tolerance_can_be_disabled() -> None:
    """关掉插入/缺失容错时，只有等长比对能命中。"""
    read = _TRUSEQ_BYTES[:10] + b"T" + _TRUSEQ_BYTES[10:]
    config = AdapterTrimConfig(allow_one_gap=False)

    result = trim_adapter(read, quality_of(read), TRUSEQ, config)

    assert not result.trimmed


# --------------------------------------------------------------------------
# 不误伤与参数
# --------------------------------------------------------------------------


def test_read_without_adapter_is_untouched() -> None:
    read = b"ACGT" * 40

    result = trim_adapter(read, quality_of(read), TRUSEQ)

    assert not result.trimmed
    assert result.sequence == read
    assert result.quality == quality_of(read)


def test_empty_adapter_is_ignored() -> None:
    read = b"ACGTACGTACGT"

    result = trim_adapter(read, quality_of(read), "")

    assert not result.trimmed
    assert result.sequence == read


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="不一致"):
        trim_adapter(b"ACGT", b"III", TRUSEQ)


def test_match_required_scales_with_candidate_count() -> None:
    """候选越多、要求越长：4 → 5（>16 条）→ 6（>256 条）。"""
    assert match_required_for(1) == 4
    assert match_required_for(16) == 4
    assert match_required_for(17) == 5
    assert match_required_for(256) == 5
    assert match_required_for(257) == 6


def test_multi_sequences_trim_successively() -> None:
    """候选表模式会**依次**裁：一条 read 上可能裁掉两段。"""
    first = "AAAATTTT"
    second = "CCCCGGGG"
    read = (b"ACGT" * 5) + first.encode() + (b"TTTT" * 3) + second.encode()

    result = trim_adapters(read, quality_of(read), [first, second])

    assert result.trimmed
    # 先按 first 裁到 "ACGT"×5，再按 second 找不到（已被裁掉），因此结果只剩前半段
    assert result.sequence == b"ACGT" * 5


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError, match="match_required"):
        AdapterTrimConfig(match_required=0)
