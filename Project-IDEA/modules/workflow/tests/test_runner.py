"""工作流编排的端到端测试。

这里跑的是**真正的原生工作流**（需要编译好的 ``bio_native.dll``），因为要验的
正是"整条链串起来之后结果还对不对"——各步单独的判定已经由各自的测试守住了。

重点盯三类性质：

1. **统计自洽**：summary 里的条数、碱基数与真实写出的文件逐条对得上；
2. **分卷命名契约**：``runner._split_name`` 算出的名字就是原生层真的写出来的
   文件（两侧各有一份命名实现，漂移了必须能被发现）；
3. **线程数不影响结果**：这是生物模块定下的纪律，工作流也必须守住。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from modules.workflow import (
    AdapterDetectionReport,
    CutSpec,
    FilterSpec,
    OverlapSpec,
    PolySpec,
    ScanReport,
    WorkflowConfig,
    run_workflow,
)
from modules.workflow.config import WorkflowOutcome
from modules.workflow.runner import _split_name

_BASES = ("A", "T", "C", "G")

#: 测试里把去重位图缩到很小。
#:
#: 去重的默认档位是 1 GiB，二十来个用例各提交一次这么大的内存，整套测试会被
#: 内存压力拖到跑不完——而这里要验的是**处理链**，不是去重的精度。缩到 256 KiB
#: 只影响假阳性率，不影响链路上任何一步的判定，因此下面统一用它。
_TEST_DEDUP_BYTES = 1 << 18


def _config(**kwargs) -> WorkflowConfig:
    """测试用的配置：默认只改去重位图大小，其余与产品默认完全一致。"""
    kwargs.setdefault("dedup_buffer_bytes", _TEST_DEDUP_BYTES)
    return WorkflowConfig(**kwargs)


def _write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality}\n")


def _read_fastq(path: Path) -> list[tuple[str, str, str]]:
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines:
        return []
    return [(lines[i][1:], lines[i + 1], lines[i + 3]) for i in range(0, len(lines), 4)]


def _random_template(length: int, seed: int = 20260923) -> str:
    return "".join(random.Random(seed).choices(_BASES, k=length))


def _reverse_complement(sequence: str) -> str:
    table = {"A": "T", "C": "G", "G": "C", "T": "A"}
    return "".join(table[base] for base in reversed(sequence))


def _single_source(tmp_path: Path, count: int = 40, length: int = 60) -> Path:
    """造一份单端数据：所有 read 都是高质量，能通过默认过滤。"""
    path = tmp_path / "single.fq"
    records = [
        (f"READ{index}", _random_template(length, seed=index + 1), "I" * length)
        for index in range(count)
    ]
    _write_fastq(path, records)
    return path


def _paired_source(
    tmp_path: Path,
    *,
    count: int = 20,
    insert: int = 180,
    read_length: int = 120,
    adapter: str = "",
) -> tuple[Path, Path, str]:
    """造一份双端数据，返回 ``(R1 路径, R2 路径, 模板序列)``。

    两条 read 从同一个模板的两端读起，因此**天然有重叠**；``adapter`` 非空时
    在两条 read 的尾部接上接头序列（片段短于读长、读穿进了接头）。
    """
    template = _random_template(insert, seed=7)
    r1 = template[:read_length] + adapter[: max(0, read_length - insert)]
    r2 = _reverse_complement(template[-read_length:]) + adapter[: max(0, read_length - insert)]
    path1 = tmp_path / "pe_1.fq"
    path2 = tmp_path / "pe_2.fq"
    _write_fastq(path1, [(f"P{index}", r1, "I" * len(r1)) for index in range(count)])
    _write_fastq(path2, [(f"P{index}", r2, "I" * len(r2)) for index in range(count)])
    return path1, path2, template


def test_single_end_copy_keeps_content_and_counts_agree(tmp_path: Path) -> None:
    """什么都不开时应当逐条抄一份，且统计与文件内容对得上。"""
    source = _single_source(tmp_path)
    original = _read_fastq(source)

    outcome = run_workflow(
        _config(read1=source, output1=tmp_path / "out.fq")
    )

    assert _read_fastq(tmp_path / "out.fq") == original
    assert outcome.summary.total_reads == 40
    assert outcome.summary.output_reads == 40
    assert outcome.summary.filtered_reads == 0
    assert outcome.summary.total_bases == 40 * 60
    assert outcome.summary.output_bases == 40 * 60
    assert outcome.summary.unpaired_reads == 0
    # 统计是工作流自己一遍扫出来的，不是事后重扫，因此与计数天然一致。
    assert outcome.pre_stats1.total_reads == 40
    assert outcome.post_stats1.total_reads == 40
    assert outcome.retention_rate == 1.0


def test_quality_cutting_changes_sequence_and_is_counted(tmp_path: Path) -> None:
    """滑窗质量剪切真的动刀，且改动条数如实计数。"""
    source = tmp_path / "choppy.fq"
    good = "ACGTACGTACGTACGT"
    _write_fastq(
        source,
        [
            ("R1", good + "ACGTACGT", "I" * 16 + "!" * 8),
            ("R2", good + "ACGTACGT", "I" * 24),
        ],
    )

    outcome = run_workflow(
        _config(
            read1=source,
            output1=tmp_path / "out.fq",
            cut=CutSpec(tail=True, window_tail=4, quality_tail=20),
        )
    )

    records = _read_fastq(tmp_path / "out.fq")
    assert outcome.summary.trimmed_reads == 1
    # 尾部那 8 个低质量碱基被滑窗剪掉，剪到窗口均值达标为止（15 而非 16，
    # 差的那一位是窗口停止处的边界，属实现口径，与单独调用质量剪切完全一致）。
    assert records[0][1] == "ACGTACGTACGTACG"
    assert records[0][2] == "I" * 15
    assert records[1] == ("R2", good + "ACGTACGT", "I" * 24)


def test_reads_filtering_drops_and_records_failures(tmp_path: Path) -> None:
    """被过滤的 read 进失败输出，名字后带原因标签。"""
    source = tmp_path / "mixed.fq"
    _write_fastq(
        source,
        [
            ("KEEP", "ACGT" * 15, "I" * 60),
            ("SHORT", "ACGT", "I" * 4),
            ("NHEAVY", "N" * 60, "I" * 60),
        ],
    )

    outcome = run_workflow(
        _config(
            read1=source,
            output1=tmp_path / "out.fq",
            failed_out=tmp_path / "failed.fq",
        )
    )

    assert _read_fastq(tmp_path / "out.fq") == [("KEEP", "ACGT" * 15, "I" * 60)]
    failed = _read_fastq(tmp_path / "failed.fq")
    assert outcome.summary.filtered_reads == 2
    assert [name.split(" ", 1)[1] for name, _, _ in failed] == [
        "failed_too_short",
        "failed_too_many_n_bases",
    ]
    # failures 的键是结果码：16 = 过短、12 = N 碱基过多。
    assert outcome.summary.failures == {16: 1, 12: 1}


def test_paired_overlap_adapter_trimming(tmp_path: Path) -> None:
    """片段短于读长时，按 overlap 把两端都裁到插入片段长度。"""
    adapter = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"
    path1, path2, _ = _paired_source(
        tmp_path, insert=80, read_length=120, adapter=adapter
    )

    outcome = run_workflow(
        _config(
            read1=path1,
            read2=path2,
            output1=tmp_path / "out1.fq",
            output2=tmp_path / "out2.fq",
            adapter1=adapter,
        )
    )

    records1 = _read_fastq(tmp_path / "out1.fq")
    records2 = _read_fastq(tmp_path / "out2.fq")
    assert outcome.summary.adapter_trimmed_reads == 40  # 20 对 × 两端
    assert all(len(sequence) == 80 for _, sequence, _ in records1)
    assert all(len(sequence) == 80 for _, sequence, _ in records2)
    assert outcome.summary.insert_size_peak == 80


def test_paired_adapter_falls_back_to_sequence_match(tmp_path: Path) -> None:
    """overlap 判不出重叠时，退化为按给定接头序列匹配。

    这里把 ``require`` 拉到读长以上，让 overlap 永不成立，于是只有按序列匹配
    这一条路能裁掉接头——这正是长插入片段的文库要走的那条路。
    """
    adapter = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"
    path1, path2, _ = _paired_source(
        tmp_path, insert=80, read_length=120, adapter=adapter
    )

    outcome = run_workflow(
        _config(
            read1=path1,
            read2=path2,
            output1=tmp_path / "out1.fq",
            output2=tmp_path / "out2.fq",
            adapter1=adapter,
            overlap=OverlapSpec(require=999),
        )
    )

    assert outcome.summary.adapter_trimmed_reads == 40
    assert all(
        len(sequence) == 80 for _, sequence, _ in _read_fastq(tmp_path / "out1.fq")
    )
    # overlap 全判不出，片段长度因此都进了溢出桶。
    assert outcome.summary.insert_size_unknown == 20


def test_unpaired_reads_go_to_their_own_file(tmp_path: Path) -> None:
    """一对里只有一端通过时，通过的那端落进 unpaired 文件。"""
    template = _random_template(60, seed=11)
    path1 = tmp_path / "pe_1.fq"
    path2 = tmp_path / "pe_2.fq"
    _write_fastq(path1, [("P1", template, "I" * 60)])
    # R2 太短，必被过滤。
    _write_fastq(path2, [("P1", "ACGT", "IIII")])

    outcome = run_workflow(
        _config(
            read1=path1,
            read2=path2,
            output1=tmp_path / "out1.fq",
            output2=tmp_path / "out2.fq",
        )
    )

    assert _read_fastq(tmp_path / "out1.fq") == []
    assert _read_fastq(tmp_path / "out2.fq") == []
    assert _read_fastq(tmp_path / "out1.unpaired.fq") == [("P1", template, "I" * 60)]
    assert outcome.summary.unpaired_reads == 1
    # 落单的那条不计入过滤后统计——与上游口径一致。
    assert outcome.post_stats1.total_reads == 0


def test_split_names_match_what_the_native_layer_writes(tmp_path: Path) -> None:
    """分卷命名契约：本层算出的名字必须就是原生层写出的那些文件。"""
    source = _single_source(tmp_path, count=20)

    outcome = run_workflow(
        _config(
            read1=source, output1=tmp_path / "out.fq", split_records=8
        )
    )

    assert outcome.summary.split_file_count == 3
    expected = [
        _split_name(tmp_path / "out.fq", index, 4) for index in range(1, 4)
    ]
    assert list(outcome.split_files) == expected
    assert all(path.exists() for path in expected)
    assert [len(_read_fastq(path)) for path in expected] == [8, 8, 4]
    assert sorted(path.name for path in outcome.outputs) == [
        "0001.out.fq",
        "0002.out.fq",
        "0003.out.fq",
    ]


def test_split_by_file_number_counts_first(tmp_path: Path) -> None:
    """按文件数分卷会先精确数一遍总条数，再换算成每卷条数。"""
    source = _single_source(tmp_path, count=21)

    outcome = run_workflow(
        _config(
            read1=source, output1=tmp_path / "out.fq", split_number=4
        )
    )

    # 21 条、每卷 5 条 → 5 卷，最后一卷装剩下的 1 条。
    assert outcome.scan.read_count == 21
    assert [len(_read_fastq(path)) for path in outcome.split_files] == [5, 5, 5, 5, 1]
    assert outcome.summary.split_file_count == 5


def test_split_conflicts_with_explicit_unpaired_path(tmp_path: Path) -> None:
    """分卷模式下无处安放落单 read——显式给了路径要报错，而不是静默丢弃。"""
    path1, path2, _ = _paired_source(tmp_path)

    with pytest.raises(ValueError, match="落单"):
        run_workflow(
            _config(
                read1=path1,
                read2=path2,
                output1=tmp_path / "out1.fq",
                output2=tmp_path / "out2.fq",
                unpaired=tmp_path / "unpaired.fq",
                split_records=4,
            )
        )


def test_merge_mode_writes_merged_reads(tmp_path: Path) -> None:
    """合并模式：重叠的对合成一条，长度等于模板长度。"""
    path1, path2, template = _paired_source(tmp_path, insert=180, read_length=120)

    outcome = run_workflow(
        _config(
            read1=path1,
            read2=path2,
            output1=tmp_path / "out1.fq",
            output2=tmp_path / "out2.fq",
            merged_out=tmp_path / "merged.fq",
        )
    )

    assert outcome.summary.pairs_merged == 20
    assert _read_fastq(tmp_path / "out1.fq") == []
    records = _read_fastq(tmp_path / "merged.fq")
    assert len(records) == 20
    assert records[0][1] == template
    assert outcome.post_stats1.total_reads == 20


def test_two_color_system_turns_poly_g_on_automatically(tmp_path: Path) -> None:
    """二色测序仪的数据自动开 polyG 修剪；用户显式指定时以用户的为准。"""
    source = tmp_path / "nextseq.fq"
    # 读长要留够：polyG 会把那 12 个 G 剪掉，剩下的必须还过得去长度过滤。
    sequence = "ACGTACGTACGTACGTAC" + "GGGGGGGGGGGG"
    _write_fastq(source, [("NS500123:1:FC:1:1:1:1 1:N:0:ACGT", sequence, "I" * 30)])

    outcome = run_workflow(
        _config(read1=source, output1=tmp_path / "out.fq")
    )
    assert outcome.scan.is_two_color is True
    assert outcome.scan.poly_g_enabled is True
    assert _read_fastq(tmp_path / "out.fq")[0][1] == "ACGTACGTACGTACGTAC"
    assert outcome.summary.poly_trimmed_reads == 1

    # 显式关掉时不再读文件做判定，也就不会裁。
    explicit = run_workflow(
        _config(
            read1=source, output1=tmp_path / "out2.fq", trim_poly_g=False
        )
    )
    assert explicit.scan.is_two_color is False
    assert explicit.scan.poly_g_enabled is False
    assert _read_fastq(tmp_path / "out2.fq")[0][1] == sequence


def test_poly_x_and_max_length_apply_in_order(tmp_path: Path) -> None:
    """polyX 与限长截断都在链上，且截断发生在 polyX 之后。"""
    source = tmp_path / "polyx.fq"
    _write_fastq(source, [("P1", "ACGTACGTAC" + "A" * 20, "I" * 30)])

    outcome = run_workflow(
        _config(
            read1=source,
            output1=tmp_path / "out.fq",
            poly=PolySpec(x=True, min_len_x=10),
            max_length1=6,
            # 截短之后必然过不了默认的长度过滤，这里把门槛放到底，
            # 好让我们能直接看序列本身——本用例验的是两步骤的顺序。
            filter=FilterSpec(required_length=1),
        )
    )

    # polyX 先把 30 碱基切到 8，限长再从尾部截到 6。顺序反过来的话结果不是这个。
    assert _read_fastq(tmp_path / "out.fq")[0][1] == "ACGTAC"
    assert outcome.summary.poly_trimmed_reads == 1
    assert outcome.summary.trimmed_reads == 0  # 限长不计入"修剪"口径


def test_explicit_adapter_skips_detection(tmp_path: Path) -> None:
    """给了接头序列就不再检测——检测结论如实记录，没有采样。"""
    source = _single_source(tmp_path)
    adapter = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"

    outcome = run_workflow(
        _config(
            read1=source, output1=tmp_path / "out.fq", adapter1=adapter
        )
    )

    assert outcome.scan.adapter.adapter1 == adapter
    assert outcome.scan.adapter.source1 == ""
    assert outcome.scan.adapter.sampled_reads == 0


def test_index_blacklist_filters_reads(tmp_path: Path) -> None:
    """按 index 过滤在执行链的最前面，命中的 read 不进任何输出。"""
    source = tmp_path / "indexed.fq"
    _write_fastq(
        source,
        [
            ("KEEP 1:N:0:AAAACCCC", "ACGT" * 15, "I" * 60),
            ("DROP 1:N:0:TTTTGGGG", "ACGT" * 15, "I" * 60),
        ],
    )

    outcome = run_workflow(
        _config(
            read1=source,
            output1=tmp_path / "out.fq",
            index_blacklist1=("TTTTGGGG",),
        )
    )

    assert [name for name, _, _ in _read_fastq(tmp_path / "out.fq")] == [
        "KEEP 1:N:0:AAAACCCC"
    ]
    # 被 index 命中的 read 仍计入"过滤前"统计——上游在过滤之前就统计了。
    assert outcome.summary.total_reads == 2
    assert outcome.summary.index_filtered_reads == 1
    assert outcome.pre_stats1.total_reads == 2


def test_reports_are_written_where_asked(tmp_path: Path) -> None:
    """log 目录拿到 workflow.json，用户拿到 HTML。"""
    source = _single_source(tmp_path)
    html = tmp_path / "report" / "report.html"
    log_dir = tmp_path / "logs"

    outcome = run_workflow(
        _config(read1=source, output1=tmp_path / "out.fq"),
        html_path=html,
        log_dir=log_dir,
    )

    assert outcome.html_path == html
    assert outcome.log_path == log_dir / "workflow.json"

    payload = json.loads((log_dir / "workflow.json").read_text(encoding="utf-8"))
    assert payload["summary"]["before_filtering"]["total_reads"] == 40

    text = html.read_text(encoding="utf-8")
    assert "<script" not in text
    assert "src=" not in text
    assert "http://" not in text
    assert "https://" not in text.replace("http://www.w3.org/2000/svg", "")


def test_thread_count_does_not_change_the_result(tmp_path: Path) -> None:
    """线程数只影响速度，不影响输出字节与统计——这是生物模块定下的纪律。"""
    adapter = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"
    path1, path2, _ = _paired_source(tmp_path, insert=80, adapter=adapter)

    def _run(name: str, threads: int) -> tuple[str, WorkflowOutcome]:
        out1 = tmp_path / f"{name}_1.fq"
        out2 = tmp_path / f"{name}_2.fq"
        outcome = run_workflow(
            _config(
                read1=path1,
                read2=path2,
                output1=out1,
                output2=out2,
                adapter1=adapter,
                threads=threads,
            )
        )
        return out1.read_text(encoding="ascii") + out2.read_text(encoding="ascii"), outcome

    serial, outcome1 = _run("serial", 1)
    parallel, outcome8 = _run("parallel", 8)

    assert serial == parallel
    assert outcome1.summary == outcome8.summary
    assert outcome1.pre_stats1.quality_curves == outcome8.pre_stats1.quality_curves
    assert outcome1.insert_size_histogram == outcome8.insert_size_histogram


def test_empty_input_produces_empty_outputs(tmp_path: Path) -> None:
    """空输入不该崩，也不该产出半成品。"""
    source = tmp_path / "empty.fq"
    source.write_text("", encoding="ascii")

    outcome = run_workflow(
        _config(read1=source, output1=tmp_path / "out.fq")
    )

    assert outcome.summary.total_reads == 0
    assert outcome.summary.output_reads == 0
    assert (tmp_path / "out.fq").read_text(encoding="ascii") == ""
    assert outcome.retention_rate == 0.0


def test_missing_input_raises(tmp_path: Path) -> None:
    """输入不存在时报错，而不是写出一个空文件。"""
    with pytest.raises(ValueError):
        run_workflow(
            _config(
                read1=tmp_path / "nope.fq", output1=tmp_path / "out.fq"
            )
        )


def test_config_rejects_contradictions(tmp_path: Path) -> None:
    """自相矛盾的配置在构造配置时就挡住。"""
    with pytest.raises(ValueError, match="只能给一个"):
        WorkflowConfig(
            read1=tmp_path / "a.fq",
            output1=tmp_path / "b.fq",
            split_records=10,
            split_number=2,
        )
    with pytest.raises(ValueError, match="双端"):
        WorkflowConfig(
            read1=tmp_path / "a.fq", output1=tmp_path / "b.fq", correction=True
        )
    with pytest.raises(ValueError, match="质量编码"):
        WorkflowConfig(
            read1=tmp_path / "a.fq", output1=tmp_path / "b.fq", input_phred=48
        )


def test_outcome_reports_scan_and_retention(tmp_path: Path) -> None:
    """outcome 上的派生量算得对，且阶段 A 的结论如实带出来。"""
    source = _single_source(tmp_path, count=10, length=60)

    outcome = run_workflow(
        _config(
            read1=source,
            output1=tmp_path / "out.fq",
            filter=FilterSpec(required_length=1000),
        )
    )

    assert outcome.scan == ScanReport(
        read_count=0,
        is_two_color=False,
        poly_g_enabled=False,
        # 单端默认会自动检测接头，因此采样条数不为 0——结论是"没检测到"。
        adapter=AdapterDetectionReport(adapter1="", adapter2="", sampled_reads=10),
    )
    assert outcome.summary.output_reads == 0
    assert outcome.retention_rate == 0.0
    assert outcome.base_retention_rate == 0.0
