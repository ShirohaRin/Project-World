from __future__ import annotations

import pytest

from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.submodules.umi_processing import (
    UPSTREAM_INDEX_VECTOR,
    UmiConfig,
    add_umi_to_name,
    first_index,
    last_index,
    process_umi,
    trim_front,
)

UPSTREAM_NAME = UPSTREAM_INDEX_VECTOR["name"]
#: 上游向量的期望名字：UMI 插在**第一个空格之前**。
UPSTREAM_NAME_WITH_INDEX1 = (
    "NS500713:64:HFKJJBGXY:1:11101:20469:1097:TATAGCCT 1:N:0:TATAGCCT+GGTCCCGA"
)
UPSTREAM_NAME_WITH_INDEX2 = (
    "NS500713:64:HFKJJBGXY:1:11101:20469:1097:GGTCCCGA 1:N:0:TATAGCCT+GGTCCCGA"
)


def record(name: str, sequence: bytes, quality: bytes | None = None) -> FastqRecord:
    return FastqRecord(name, sequence, quality or b"I" * len(sequence))


# --------------------------------------------------------------------------
# index 解析（上游 Read::test() 验证的就是这两个）
# --------------------------------------------------------------------------


def test_upstream_index_vector_is_reproduced() -> None:
    vector = UPSTREAM_INDEX_VECTOR
    assert first_index(vector["name"]) == vector["first_index"]
    assert last_index(vector["name"]) == vector["last_index"]


def test_short_names_give_empty_index() -> None:
    """名字短于 5 个字符（上游算上了行首的 '@'）时直接给空串。"""
    assert first_index("ab") == ""
    assert last_index("ab") == ""
    assert first_index("abcd") == ""
    assert last_index("abcd") == ""


def test_names_without_index_give_empty_string() -> None:
    assert first_index("read1") == ""
    assert last_index("read1") == ""


# --------------------------------------------------------------------------
# trim_front：上游永远留至少 1 个碱基
# --------------------------------------------------------------------------


def test_trim_front_keeps_at_least_one_base() -> None:
    """上游写成 ``min(length-1, len)``，所以剪不空。"""
    trimmed, quality = trim_front(b"ACGT", b"IIII", 10)
    assert trimmed == b"T"
    assert quality == b"I"


def test_trim_front_zero_changes_nothing() -> None:
    assert trim_front(b"ACGT", b"IIII", 0) == (b"ACGT", b"IIII")


# --------------------------------------------------------------------------
# add_umi_to_name
# --------------------------------------------------------------------------


def test_umi_is_inserted_before_first_space() -> None:
    config = UmiConfig()
    assert (
        add_umi_to_name(UPSTREAM_NAME, "TATAGCCT", config)
        == UPSTREAM_NAME_WITH_INDEX1
    )


def test_umi_is_appended_when_name_has_no_space() -> None:
    assert add_umi_to_name("read1", "ACGT", UmiConfig()) == "read1:ACGT"


def test_prefix_and_delimiter_are_applied() -> None:
    config = UmiConfig(prefix="UMI", delimiter="/")
    assert add_umi_to_name("read1", "ACGT", config) == "read1/UMI_ACGT"


# --------------------------------------------------------------------------
# 六种位置
# --------------------------------------------------------------------------


def test_index1_takes_first_index_and_keeps_sequence() -> None:
    sequence = b"ACGTACGTACGT"
    result = process_umi(
        record(UPSTREAM_NAME, sequence), config=UmiConfig(location="index1")
    )

    assert result.umi == "TATAGCCT"
    assert result.read1.sequence == sequence  # index 不在序列里，不剪
    assert result.read1.name == UPSTREAM_NAME_WITH_INDEX1


def test_index2_takes_last_index_of_read2_and_tags_both() -> None:
    result = process_umi(
        record(UPSTREAM_NAME, b"ACGTACGT"),
        record(UPSTREAM_NAME, b"TGCATGCA"),
        config=UmiConfig(location="index2"),
    )

    assert result.umi == "GGTCCCGA"
    assert result.read1.name == UPSTREAM_NAME_WITH_INDEX2
    assert result.read2 is not None
    assert result.read2.name == UPSTREAM_NAME_WITH_INDEX2
    # 两条 read 的序列都不动
    assert result.read1.sequence == b"ACGTACGT"
    assert result.read2.sequence == b"TGCATGCA"


def test_read1_takes_prefix_and_trims_it_plus_skip() -> None:
    config = UmiConfig(location="read1", length=8, skip=2)
    result = process_umi(record("read1", b"ACGTACGT" + b"TT" + b"GGGG"), config=config)

    assert result.umi == "ACGTACGT"
    assert result.read1.sequence == b"GGGG"  # 剪掉 UMI 8 个 + 跳过 2 个
    assert result.read1.name == "read1:ACGTACGT"


def test_read2_takes_prefix_of_read2_only() -> None:
    config = UmiConfig(location="read2", length=4)
    result = process_umi(
        record("r1", b"AAAA" + b"CCCC"),
        record("r2", b"GGGG" + b"TTTT"),
        config=config,
    )

    assert result.umi == "GGGG"
    assert result.read1.sequence == b"AAAACCCC"  # R1 不动
    assert result.read1.name == "r1:GGGG"        # 但两条都挂上
    assert result.read2 is not None
    assert result.read2.sequence == b"TTTT"
    assert result.read2.name == "r2:GGGG"


def test_per_index_joins_both_indexes() -> None:
    result = process_umi(
        record(UPSTREAM_NAME, b"ACGT"),
        record(UPSTREAM_NAME, b"TGCA"),
        config=UmiConfig(location="per_index"),
    )

    assert result.umi == "TATAGCCT_GGTCCCGA"
    assert result.read1.name.startswith(
        "NS500713:64:HFKJJBGXY:1:11101:20469:1097:TATAGCCT_GGTCCCGA "
    )
    assert result.read2 is not None
    assert result.read2.name == result.read1.name


def test_per_read_joins_both_read_prefixes() -> None:
    config = UmiConfig(location="per_read", length=4)
    result = process_umi(
        record("r1", b"AAAA" + b"CCCC"),
        record("r2", b"GGGG" + b"TTTT"),
        config=config,
    )

    assert result.umi == "AAAA_GGGG"
    assert result.read1.sequence == b"CCCC"
    assert result.read2 is not None
    assert result.read2.sequence == b"TTTT"
    assert result.read1.name == "r1:AAAA_GGGG"
    assert result.read2.name == "r2:AAAA_GGGG"


# --------------------------------------------------------------------------
# 单端与退化情形
# --------------------------------------------------------------------------


def test_single_end_leaves_out_read2_modes() -> None:
    """单端数据选到需要 R2 的来源时取不到 UMI——名字与序列原样保留。"""
    sequence = b"ACGTACGT"
    result = process_umi(
        record(UPSTREAM_NAME, sequence), None, config=UmiConfig(location="index2")
    )

    assert result.umi == ""
    assert result.read2 is None
    assert result.read1.name == UPSTREAM_NAME
    assert result.read1.sequence == sequence


def test_single_end_read1_mode_works() -> None:
    result = process_umi(
        record("read1", b"ACGTACGT" + b"GGGG"),
        None,
        config=UmiConfig(location="read1", length=8),
    )

    assert result.umi == "ACGTACGT"
    assert result.read1.sequence == b"GGGG"
    assert result.read2 is None


def test_per_index_without_index_leaves_a_delimiter() -> None:
    """名字里没有 index 时，上游仍无条件挂标签，于是留下一个分隔符。"""
    result = process_umi(
        record("read1", b"ACGT"), None, config=UmiConfig(location="per_index")
    )

    assert result.umi == ""
    assert result.read1.name == "read1:"


def test_umi_longer_than_read_keeps_one_base() -> None:
    result = process_umi(
        record("read1", b"ACGT"), config=UmiConfig(location="read1", length=10)
    )

    assert result.umi == "ACGT"
    assert result.read1.sequence == b"T"  # trimFront 至少留 1 个
    assert result.read1.quality == b"I"


def test_quality_is_trimmed_along_with_sequence() -> None:
    result = process_umi(
        record("read1", b"ACGTACGT" + b"GGGG", b"0123456789AB"),
        config=UmiConfig(location="read1", length=8),
    )

    assert result.read1.quality == b"89AB"
    assert len(result.read1.quality) == len(result.read1.sequence)


# --------------------------------------------------------------------------
# 参数校验
# --------------------------------------------------------------------------


def test_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="location"):
        UmiConfig(location="nowhere")
    with pytest.raises(ValueError, match="length"):
        UmiConfig(length=-1)
    with pytest.raises(ValueError, match="skip"):
        UmiConfig(skip=-1)
    with pytest.raises(ValueError, match="delimiter"):
        UmiConfig(delimiter="")
