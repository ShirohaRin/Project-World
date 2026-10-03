"""按 index 过滤的测试。

判定逻辑很短，但藏着两处容易写错的地方：

1. **只比两者中较短的长度** —— 黑名单写 ``ACGT``、read 的 index 是 ``AC``
   时算命中（只比前 2 位）。这一条最容易"顺手改成比全长"。
2. **名字里取不到 index 时返回空串**，而空 target 与任何非空黑名单都能
   "全同"匹配 —— 于是**全被丢掉**。这是上游的行为，必须写下来，
   免得有人踩到之后以为是 bug。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.index_filtering import (
    IndexFilterConfig,
    filter_by_index_fastq,
    is_filtered,
    matches_blacklist,
)

_Q40 = chr(40 + 33)

#: Illumina 风格的 read 名：第一段 index = ACGTACGT，最后一段 = TTTTGGGG。
_NAME1 = "read1 1:N:0:ACGTACGT+TTTTGGGG"
_NAME2 = "read1 2:N:0:ACGTACGT+TTTTGGGG"


# ---------------------------------------------------------------------------
# 黑名单匹配
# ---------------------------------------------------------------------------


def test_exact_match() -> None:
    assert matches_blacklist(("ACGT",), "ACGT", 0) is True
    assert matches_blacklist(("ACGT",), "ACGA", 0) is False


def test_only_the_shorter_length_is_compared() -> None:
    """黑名单比目标长时只比目标那么长；反过来也一样。"""
    assert matches_blacklist(("ACGTACGT",), "AC", 0) is True
    assert matches_blacklist(("AC",), "ACGTACGT", 0) is True
    assert matches_blacklist(("ACGT",), "TT", 0) is False


def test_empty_target_matches_any_blacklist() -> None:
    """空 target 与任何非空黑名单都能匹配——上游如此，是个陷阱。"""
    assert matches_blacklist(("ACGT",), "", 0) is True


def test_empty_blacklist_matches_nothing() -> None:
    assert matches_blacklist((), "ACGT", 0) is False


def test_threshold_allows_mismatches() -> None:
    assert matches_blacklist(("ACGT",), "ACGA", 0) is False  # 1 个错配 > 0
    assert matches_blacklist(("ACGT",), "ACGA", 1) is True
    assert matches_blacklist(("ACGT",), "ACAA", 1) is False  # 2 个错配 > 1


def test_threshold_short_circuits_correctly() -> None:
    """提前退出时错配数已经大于阈值，所以最后用 ``<=`` 判断是安全的。"""
    assert matches_blacklist(("AAAA",), "TTTT", 2) is False
    assert matches_blacklist(("AAAA",), "AAAT", 3) is True


def test_negative_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="threshold"):
        IndexFilterConfig(threshold=-1)


# ---------------------------------------------------------------------------
# 该丢哪一条
# ---------------------------------------------------------------------------


def test_single_end_uses_first_index_of_read1() -> None:
    config = IndexFilterConfig(blacklist1=("ACGTACGT",))

    assert is_filtered(_NAME1, None, config) is True
    assert is_filtered("read1 1:N:0:GGGGGGGG+TTTTGGGG", None, config) is False


def test_paired_end_uses_last_index_of_read2() -> None:
    config = IndexFilterConfig(blacklist2=("TTTTGGGG",))

    assert is_filtered(_NAME1, _NAME2, config) is True
    assert (
        is_filtered(_NAME1, "read1 2:N:0:ACGTACGT+CCCCCCCC", config) is False
    )


def test_paired_end_filters_when_either_end_hits() -> None:
    config = IndexFilterConfig(blacklist1=("ACGTACGT",), blacklist2=("CCCCCCCC",))

    # R1 命中
    assert is_filtered(_NAME1, "read1 2:N:0:ACGTACGT+GGGGGGGG", config) is True
    # R2 命中（R2 的最后一段是 CCCC...）
    assert (
        is_filtered("read1 1:N:0:TTTTTTTT+TTTTGGGG", "read1 2:N:0:ACGTACGT+CCCCCCCC", config)
        is True
    )
    # 都不命中
    assert is_filtered("read1 1:N:0:TTTTTTTT+TTTTGGGG", "x 2:N:0:ACGTACGT+GGGGGGGG", config) is False


def test_empty_blacklists_filter_nothing() -> None:
    config = IndexFilterConfig()

    assert config.enabled is False
    assert is_filtered(_NAME1, _NAME2, config) is False


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------


def write_fastq(path: Path, records: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence in records:
            handle.write(f"@{name}\n{sequence}\n+\n{_Q40 * len(sequence)}\n")


def test_file_level_single_end(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    write_fastq(
        source,
        [
            ("keep 1:N:0:GGGGGGGG+TTTTGGGG", "ACGT" * 10),
            ("drop 1:N:0:ACGTACGT+CCCCCCCC", "TTTT" * 10),
        ],
    )

    summary = filter_by_index_fastq(
        source, output, config=IndexFilterConfig(blacklist1=("ACGTACGT",))
    )

    assert summary.total_reads == 2
    assert summary.filtered_reads == 1
    assert summary.kept_reads == 1
    assert [record.name.split()[0] for record in read_fastq(output)] == ["keep"]


def test_file_level_paired_drops_both_sides(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(
        read1,
        [
            ("keep/1 1:N:0:GGGGGGGG+TTTTGGGG", "ACGT" * 10),
            ("drop/1 1:N:0:ACGTACGT+TTTTGGGG", "ACGT" * 10),
        ],
    )
    write_fastq(
        read2,
        [
            ("keep/2 2:N:0:GGGGGGGG+TTTTGGGG", "ACGT" * 10),
            ("drop/2 2:N:0:ACGTACGT+TTTTGGGG", "ACGT" * 10),
        ],
    )
    out1 = tmp_path / "clean_R1.fq"
    out2 = tmp_path / "clean_R2.fq"

    summary = filter_by_index_fastq(
        read1,
        out1,
        read2_path=read2,
        output2_path=out2,
        config=IndexFilterConfig(blacklist1=("ACGTACGT",)),
    )

    assert summary.paired is True
    assert summary.total_reads == 2  # 双端按对计
    assert summary.filtered_reads == 1
    assert [record.name.split()[0] for record in read_fastq(out1)] == ["keep/1"]
    assert [record.name.split()[0] for record in read_fastq(out2)] == ["keep/2"]


def test_file_level_without_blacklist_copies_everything(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    write_fastq(source, [("a", "ACGT"), ("b", "TTTT")])

    summary = filter_by_index_fastq(source, output)

    assert summary.enabled is False
    assert summary.filtered_reads == 0
    assert output.read_bytes() == source.read_bytes()


def test_file_level_recovers_no_index_as_a_trap(tmp_path: Path) -> None:
    """名字里没有 index 时会被全部丢掉——这是上游行为，如实固定住。"""
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    write_fastq(source, [("no_index_here", "ACGT"), ("also_plain", "TTTT")])

    summary = filter_by_index_fastq(
        source, output, config=IndexFilterConfig(blacklist1=("ACGTACGT",))
    )

    assert summary.filtered_reads == 2
    assert output.read_bytes() == b""


def test_file_level_rejects_mismatched_counts(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, [("a/1", "ACGT"), ("b/1", "ACGT")])
    write_fastq(read2, [("a/2", "ACGT")])
    out1 = tmp_path / "o1.fq"
    out2 = tmp_path / "o2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        filter_by_index_fastq(
            read1, out1, read2_path=read2, output2_path=out2
        )

    assert not out1.exists() and not out2.exists()


def test_file_level_paired_needs_both_outputs(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, [("a/1", "ACGT")])
    write_fastq(read2, [("a/2", "ACGT")])

    with pytest.raises(ValueError, match="两份输出路径"):
        filter_by_index_fastq(read1, tmp_path / "o1.fq", read2_path=read2)


def test_file_level_second_output_without_second_input(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, [("a", "ACGT")])

    with pytest.raises(ValueError, match="第二份输出路径"):
        filter_by_index_fastq(source, tmp_path / "o1.fq", output2_path=tmp_path / "o2.fq")


def test_file_level_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        filter_by_index_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_file_level_removes_partial_output(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        filter_by_index_fastq(broken, output)

    assert not output.exists()
