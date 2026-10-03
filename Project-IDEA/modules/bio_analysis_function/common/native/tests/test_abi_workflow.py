"""原生工作流入口（bio_run_workflow）与二色判定的 ABI 测试。

Python 层的端到端测试（``modules/workflow/tests``）验的是"整条链串起来对不对"；这里验的是
**ABI 边界本身**：结构体全零的默认值约定、参数自相矛盾的报错、失败时半成品是否
被清干净、以及结果句柄的取用与销毁。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.native import abi, library_path

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)


def write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality}\n")


def read_fastq(path: Path) -> list[tuple[str, str, str]]:
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines:
        return []
    return [(lines[i][1:], lines[i + 1], lines[i + 3]) for i in range(0, len(lines), 4)]


def make_source(folder: Path, count: int = 30, length: int = 60) -> Path:
    path = folder / "source.fq"
    write_fastq(
        path,
        [
            (f"READ{index}", "ACGT" * (length // 4), "I" * length)
            for index in range(count)
        ],
    )
    return path


# --- 二色判定 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "FS100:1:FC:1:1:1:1 1:N:0:ACGT",  # iSeq 100
        "MN01234:1:FC:1:1:1:1 1:N:0:ACGT",  # MiniSeq
        "NS500123:1:FC:1:1:1:1 1:N:0:ACGT",  # NextSeq 500/550
        "NDX550:1:FC:1:1:1:1 1:N:0:ACGT",  # NextSeq 550DX
        "VL00123:1:FC:1:1:1:1 1:N:0:ACGT",  # NextSeq 1000/2000
        "A00123:1:FC:1:1:1:1 1:N:0:ACGT",  # NovaSeq 6000
        "LH00123:1:FC:1:1:1:1 1:N:0:ACGT",  # NovaSeq X
    ],
)
def test_two_color_prefixes_are_recognised(tmp_path: Path, name: str) -> None:
    """名字前缀命中仪器型号就认二色系统。"""
    source = tmp_path / "one.fq"
    write_fastq(source, [(name, "ACGTACGTACGTACGT", "I" * 16)])

    assert abi.detect_two_color_system(source) is True


def test_other_instruments_are_not_two_color(tmp_path: Path) -> None:
    """别的命名（比如 MGI）不算。"""
    source = tmp_path / "one.fq"
    write_fastq(source, [("V300012345L1C001R0010000001/1", "ACGTACGTACGTACGT", "I" * 16)])

    assert abi.detect_two_color_system(source) is False


def test_two_color_on_empty_input_is_false(tmp_path: Path) -> None:
    """空文件没有第一条 read，按"不是"处理（上游同样返回 false）。"""
    source = tmp_path / "empty.fq"
    source.write_text("", encoding="ascii")

    assert abi.detect_two_color_system(source) is False


def test_two_color_on_missing_file_raises(tmp_path: Path) -> None:
    """文件不存在要报错，不能悄悄返回 False。"""
    with pytest.raises(ValueError):
        abi.detect_two_color_system(tmp_path / "nope.fq")


# --- 工作流 -----------------------------------------------------------------


def test_copy_only_workflow_matches_input(tmp_path: Path) -> None:
    """什么都不开时逐条抄一份，汇总与四个统计槽位都自洽。"""
    source = make_source(tmp_path)
    original = read_fastq(source)
    output = tmp_path / "out.fq"

    result = abi.run_workflow(
        abi.WorkflowRequest(read1_path=str(source), output1_path=str(output))
    )

    assert read_fastq(output) == original
    assert result.summary.total_reads == 30
    assert result.summary.output_reads == 30
    assert result.summary.total_bases == 30 * 60
    assert result.pre_stats1.total_reads == 30
    assert result.post_stats1.total_reads == 30
    assert result.pre_stats1.cycles == 60


def test_stats_are_empty_when_disabled(tmp_path: Path) -> None:
    """关掉统计时四个槽位是空的，但汇总照常给出。"""
    source = make_source(tmp_path, count=2)

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            stats_enabled=False,
        )
    )

    assert result.summary.total_reads == 2
    assert result.pre_stats1.total_reads == 0
    assert result.post_stats1.total_reads == 0


def test_zeroed_sub_options_fall_back_to_defaults(tmp_path: Path) -> None:
    """子结构体全零 = 没给 = 用该算法的默认值。

    C 调用方习惯 memset 清零，而各步的默认值不是零（质量窗口 4、poly 最短 10、
    过滤阈值 15/40/5）。这条约定不成立的话，"清零之后静默换了一套参数"会很难查。
    """
    source = tmp_path / "choppy.fq"
    # 前 20 个高质量、后 20 个低质量；默认的质量剪切参数（窗口 4、Q20）应当把它剪短。
    write_fastq(source, [("R1", "ACGT" * 10, "I" * 20 + "!" * 20)])

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            cut=abi.CutSpec(tail=True),  # 其余走 CutSpec 的默认值
        )
    )

    assert result.summary.trimmed_reads == 1
    assert len(read_fastq(tmp_path / "out.fq")[0][1]) >= 15


def test_contradictory_arguments_raise(tmp_path: Path) -> None:
    """自相矛盾的参数组合必须报错，而不是悄悄挑一个执行。"""
    source = make_source(tmp_path)

    # 单端却给了 read2 输出。
    with pytest.raises(ValueError, match="read2"):
        abi.run_workflow(
            abi.WorkflowRequest(
                read1_path=str(source),
                output1_path=str(tmp_path / "out.fq"),
                output2_path=str(tmp_path / "out2.fq"),
            )
        )

    # 分卷又把落单 / 失败输出一起要上。
    with pytest.raises(ValueError, match="分卷"):
        abi.run_workflow(
            abi.WorkflowRequest(
                read1_path=str(source),
                output1_path=str(tmp_path / "out.fq"),
                failed_out=str(tmp_path / "failed.fq"),
                split_records=4,
            )
        )

    # 合并模式没给合并输出。
    with pytest.raises(ValueError, match="合并"):
        abi.run_workflow(
            abi.WorkflowRequest(
                read1_path=str(source),
                read2_path=str(source),
                output1_path=str(tmp_path / "out.fq"),
                output2_path=str(tmp_path / "out2.fq"),
                merge_enabled=True,
            )
        )


def test_missing_input_raises_and_leaves_no_output(tmp_path: Path) -> None:
    """输入不存在时报错，并且不留半成品。"""
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError):
        abi.run_workflow(
            abi.WorkflowRequest(
                read1_path=str(tmp_path / "nope.fq"), output1_path=str(output)
            )
        )

    assert not output.exists()


def test_split_writes_numbered_files(tmp_path: Path) -> None:
    """分卷的命名与条数是确定的：序号前缀加原文件名，最后一卷装余数。"""
    source = make_source(tmp_path, count=10)

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            split_records=4,
        )
    )

    assert result.summary.split_file_count == 3
    assert [len(read_fastq(tmp_path / f"{index:04d}.out.fq")) for index in (1, 2, 3)] == [
        4,
        4,
        2,
    ]


def test_insert_size_histogram_is_returned(tmp_path: Path) -> None:
    """直方图的桶数固定，溢出桶在最后一项；单端时全零。"""
    source = make_source(tmp_path, count=4)

    result = abi.run_workflow(
        abi.WorkflowRequest(read1_path=str(source), output1_path=str(tmp_path / "out.fq"))
    )

    assert len(result.insert_size_histogram) == abi.WORKFLOW_INSERT_SIZE_BUCKETS
    assert all(count == 0 for count in result.insert_size_histogram)


def test_max_reads_limits_how_much_is_read(tmp_path: Path) -> None:
    """--reads_to_process 的语义是"只读前 N 条"，被截掉的连统计都不进。"""
    source = make_source(tmp_path, count=30)

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            max_reads=7,
        )
    )

    assert result.summary.total_reads == 7
    assert result.summary.output_reads == 7
    assert result.pre_stats1.total_reads == 7
    assert len(read_fastq(tmp_path / "out.fq")) == 7


def test_failed_reads_carry_a_reason_tag(tmp_path: Path) -> None:
    """失败输出里的记录打的是**原始**序列，名字后追原因标签。"""
    source = tmp_path / "mixed.fq"
    write_fastq(source, [("SHORT", "ACGT", "IIII")])

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            failed_out=str(tmp_path / "failed.fq"),
        )
    )

    assert result.summary.filtered_reads == 1
    failed = read_fastq(tmp_path / "failed.fq")
    assert failed == [("SHORT failed_too_short", "ACGT", "IIII")]


def test_dedup_drops_duplicates_and_counts_them(tmp_path: Path) -> None:
    """去重按文件顺序判定：相同序列里第一条留下、后来的丢。

    位图给得很小（连假阳性都不影响这个用例的结论——序列完全一样，必定判重）。
    """
    source = tmp_path / "duplicated.fq"
    template = "ACGT" * 15
    write_fastq(
        source,
        [("FIRST", template, "I" * 60), ("SECOND", template, "I" * 60)],
    )

    result = abi.run_workflow(
        abi.WorkflowRequest(
            read1_path=str(source),
            output1_path=str(tmp_path / "out.fq"),
            dedup_evaluate=True,
            dedup_enabled=True,
            dedup_buffer_bytes=1 << 16,
        )
    )

    assert result.summary.duplicate_reads == 1
    assert result.summary.output_reads == 1
    assert [name for name, _, _ in read_fastq(tmp_path / "out.fq")] == ["FIRST"]
