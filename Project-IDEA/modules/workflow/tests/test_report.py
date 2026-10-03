"""工作流报告的测试。

与 read_stats 的报告测试同源，守的是几条**硬性质**——自包含、逐字节可复现、
图上真有那么多点、双端前后曲线不等长也能正常渲染、JSON 能被 ``dumps`` 出去。
这里**不跑**真正的原生工作流（那要编译好的 dll 与真实数据），而是手工构造
``WorkflowOutcome``，把报告层单独拎出来测。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from modules.bio_analysis_function.common.native.abi import (
    NativeReadStats,
    NativeWorkflowSummary,
)
from modules.workflow.config import (
    AdapterDetectionReport,
    ScanReport,
    WorkflowConfig,
    WorkflowOutcome,
)
from modules.workflow.report import (
    build_workflow_json,
    render_workflow_html,
)

_POLYLINE_POINTS = re.compile(r'points="([^"]*)"')


def _point_counts(html: str) -> list[int]:
    """每条折线的点数。"""
    return [len(points.split()) for points in _POLYLINE_POINTS.findall(html)]


def _stats(reads: int, cycles: int, *, mean_length: int = 4) -> NativeReadStats:
    """一份构造出来的 read 统计：曲线长度 = cycles，读长固定。"""
    quality = (30.0,) * cycles
    content = (0.25,) * cycles
    zeros = (0.0,) * cycles
    return NativeReadStats(
        total_reads=reads,
        total_bases=reads * mean_length,
        q20_bases=reads * mean_length,
        q30_bases=reads * mean_length // 2,
        q40_bases=0,
        gc_bases=reads * mean_length // 4,
        mean_length=mean_length,
        cycles=cycles,
        max_length=mean_length,
        quality_curves={
            "mean": quality,
            "A": quality,
            "T": quality,
            "C": quality,
            "G": quality,
            "N": zeros,
        },
        content_curves={
            "A": content,
            "T": content,
            "C": content,
            "G": content,
            "N": zeros,
            "GC": (0.5,) * cycles,
        },
        quality_histogram={30: reads * mean_length},
        kmer_counts=(),
        length_counts={mean_length: reads},
    )


def _summary(paired: bool) -> NativeWorkflowSummary:
    total_reads = 200 if paired else 100
    output_reads = 180 if paired else 90
    return NativeWorkflowSummary(
        total_reads=total_reads,
        total_bases=total_reads * 4,
        output_reads=output_reads,
        output_bases=output_reads * 4,
        unpaired_reads=2 if paired else 0,
        pairs_total=100 if paired else 0,
        normalized_reads=0,
        index_filtered_reads=0,
        umi_tagged_reads=0,
        duplicate_reads=3,
        dedup_accuracy_level=1,
        trimmed_reads=5,
        poly_trimmed_reads=0,
        adapter_trimmed_reads=7,
        adapter_dimer_pairs=0,
        corrected_pairs=2 if paired else 0,
        corrected_bases=4 if paired else 0,
        filtered_reads=10,
        failures={12: 4, 20: 6},
        pairs_merged=0,
        gap_overlap_pairs=0,
        insert_size_peak=150,
        insert_size_unknown=3,
        split_file_count=0,
    )


def _outcome(
    paired: bool = False, *, pre_cycles: int = 20, post_cycles: int = 20
) -> WorkflowOutcome:
    config = WorkflowConfig(
        read1=Path("in_R1.fq"),
        output1=Path("out_R1.fq"),
        read2=Path("in_R2.fq") if paired else None,
        output2=Path("out_R2.fq") if paired else None,
    )
    scan = ScanReport(
        read_count=100,
        is_two_color=True,
        poly_g_enabled=True,
        adapter=AdapterDetectionReport(
            adapter1="AGATCGGAAGAGC",
            source1="known",
            sampled_reads=1000,
        ),
    )
    # 直方图：150 个空桶 + 长度 150 处 4 对 + 10 个空桶 + 末尾溢出桶（判不出 3 对）。
    histogram = (0,) * 150 + (4,) + (0,) * 10 + (3,)
    return WorkflowOutcome(
        config=config,
        scan=scan,
        summary=_summary(paired),
        pre_stats1=_stats(100, pre_cycles),
        pre_stats2=_stats(100, pre_cycles) if paired else _stats(0, 0),
        post_stats1=_stats(90, post_cycles),
        post_stats2=_stats(90, post_cycles) if paired else _stats(0, 0),
        insert_size_histogram=histogram,
        outputs=(Path("out_R1.fq"),) + ((Path("out_R2.fq"),) if paired else ()),
    )


# ---------------------------------------------------------------------------
# 硬性质
# ---------------------------------------------------------------------------


def test_html_is_self_contained() -> None:
    for paired in (False, True):
        html = render_workflow_html(_outcome(paired=paired))

        assert html.startswith("<!DOCTYPE html>")
        assert "<style>" in html
        assert "<script" not in html.lower()          # 不执行任何脚本
        assert " src=" not in html                     # 不引用外部资源
        # 只有 SVG 的 xmlns 是允许的 http 声明，去掉它之后不该再有 http。
        stripped = html.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http://" not in stripped and "https://" not in stripped
        assert html.rstrip().endswith("</html>")


def test_html_is_reproducible() -> None:
    """不带时间戳，所以同一份数据两次渲染必须逐字节相同。"""
    outcome = _outcome(paired=True)

    assert render_workflow_html(outcome) == render_workflow_html(outcome)


def test_curve_points_match_the_input_length() -> None:
    """质量对比图有两条曲线（前 / 后），点数等于输入的 cycle 数。"""
    outcome = _outcome(paired=False, pre_cycles=18, post_cycles=18)

    quality = (
        render_workflow_html(outcome)
        .split("质量曲线（过滤前后）")[1]
        .split("碱基含量")[0]
    )

    assert _point_counts(quality) == [18, 18]


def test_paired_curves_stop_at_their_own_length() -> None:
    """双端且前后长度不同：横轴取较长的那条，短的那条到自己的长度就收线。

    这条性质是有意为之：过滤后读长会变短，若把短曲线补 0 补齐，图上就会出现
    一段贴着横轴的"零值"，读起来像是"后面那些位置质量为 0"，而事实是那些
    位置根本没有碱基。
    """
    outcome = _outcome(paired=True, pre_cycles=24, post_cycles=10)

    quality = (
        render_workflow_html(outcome)
        .split("质量曲线（过滤前后）")[1]
        .split("碱基含量")[0]
    )

    # R1、R2 各一张对比图：过滤前 24 点、过滤后 10 点。
    assert _point_counts(quality) == [24, 10, 24, 10]
    # 横轴刻度仍然按较长的那条标。
    assert ">24<" in quality


# ---------------------------------------------------------------------------
# JSON 日志
# ---------------------------------------------------------------------------


def test_json_is_plain_serializable() -> None:
    payload = build_workflow_json(_outcome(paired=True))

    assert isinstance(json.dumps(payload, ensure_ascii=False), str)
    assert payload["summary"]["sequencing"] == "paired end"
    # failures 的键是结果码整数，写进 JSON 必须是字符串。
    assert set(payload["filtering_result"]["failures"]) == {"12", "20"}
    # command 里不能残留 Path 之类的非 JSON 对象。
    assert isinstance(payload["command"]["read1"], str)
    assert isinstance(payload["outputs"]["files"], list)


def test_single_end_json_reports_single_end() -> None:
    payload = build_workflow_json(_outcome(paired=False))

    assert payload["summary"]["sequencing"] == "single end"
    assert "read2_mean_length" not in payload["summary"]["before_filtering"]
    assert "base_correction" not in payload


def test_insert_size_histogram_keeps_only_non_zero_buckets() -> None:
    payload = build_workflow_json(_outcome(paired=True))

    assert payload["insert_size"]["peak"] == 150
    assert payload["insert_size"]["unknown"] == 3
    # 只有长度 150 那一桶非零；末尾溢出桶不算真实长度。
    assert payload["insert_size"]["histogram"] == {"150": 4}


# ---------------------------------------------------------------------------
# 内容
# ---------------------------------------------------------------------------


def test_insert_size_only_for_paired() -> None:
    assert "insert_size" in build_workflow_json(_outcome(paired=True))
    assert "insert_size" not in build_workflow_json(_outcome(paired=False))
    assert "插入片段长度分布" in render_workflow_html(_outcome(paired=True))
    assert "插入片段长度分布" not in render_workflow_html(_outcome(paired=False))


def test_failure_table_lists_reasons() -> None:
    failures = (
        render_workflow_html(_outcome())
        .split("过滤结果")[1]
        .split("质量曲线")[0]
    )

    assert "N 碱基过多" in failures
    assert "质量不合格" in failures
    assert "4" in failures and "6" in failures


def test_step_table_hides_untouched_steps() -> None:
    steps = (
        render_workflow_html(_outcome())
        .split("各步改动量")[1]
        .split("阶段 A 结论")[0]
    )

    assert "接头裁剪" in steps and "去重" in steps
    assert "规范化" not in steps      # 计数为 0，不该出现
    assert "poly 修剪" not in steps   # 同上


def test_scan_section_shows_detected_adapter() -> None:
    html = render_workflow_html(_outcome(paired=True))

    assert "AGATCGGAAGAGC" in html
    assert "已知接头表" in html
