"""read_filtering：reads 过滤（质量 / N 含量 / 长度 / 低复杂度）。

实现与 fastp 1.3.x 的 ``Filter::passFilter`` 逐位对齐，
可独立使用（算法广场入口或 Python API），用法见同目录 ``read_filtering.md``。

    algorithm.py  单条 read 的判定（结果码、质量统计、复杂度）
    runner.py     文件级接口（流式读写、按原因分类的统计）

FASTQ 流式读写与压缩策略来自模块公共层
:mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    FAILURE_LABELS,
    FAILURE_ORDER,
    FAIL_COMPLEXITY,
    FAIL_LENGTH,
    FAIL_N_BASE,
    FAIL_QUALITY,
    FAIL_TOO_LONG,
    PASS_FILTER,
    QualityMetrics,
    ReadFilterConfig,
    count_quality_metrics,
    filter_verdict,
    passes_filter,
    passes_low_complexity,
    verdict_label,
)
from .runner import FilterSummary, filter_fastq

__all__ = [
    "FAILURE_LABELS",
    "FAILURE_ORDER",
    "FAIL_COMPLEXITY",
    "FAIL_LENGTH",
    "FAIL_N_BASE",
    "FAIL_QUALITY",
    "FAIL_TOO_LONG",
    "PASS_FILTER",
    "FilterSummary",
    "QualityMetrics",
    "ReadFilterConfig",
    "count_quality_metrics",
    "filter_fastq",
    "filter_verdict",
    "passes_filter",
    "passes_low_complexity",
    "verdict_label",
]
