"""overrepresented_sequences：过表达序列分析（fastp 的 over-representation analysis）。

找出数据里异常高频的片段——接头残留、污染、rRNA 之类的线索。
可独立使用（算法广场入口或 Python API），用法见同目录
``overrepresented_sequences.md``。

    algorithm.py  纯计数与筛选（候选发现、阈值判定、采样统计）
    runner.py     文件级接口（两遍扫描）

FASTQ 流式读取来自模块公共层
:mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    CANDIDATE_BASE_LIMIT,
    MAX_SAMPLING,
    MIN_SAMPLING,
    SEQ_LENGTH_SAMPLE,
    OverrepConfig,
    OverrepSummary,
    OverrepresentedSequence,
    candidate_steps,
    collect_candidate_counts,
    filter_candidates,
    passes_report_threshold,
    remove_substrings,
    scan_sampled_read,
)
from .runner import find_overrepresented_sequences

__all__ = [
    "CANDIDATE_BASE_LIMIT",
    "MAX_SAMPLING",
    "MIN_SAMPLING",
    "SEQ_LENGTH_SAMPLE",
    "OverrepConfig",
    "OverrepSummary",
    "OverrepresentedSequence",
    "candidate_steps",
    "collect_candidate_counts",
    "filter_candidates",
    "find_overrepresented_sequences",
    "passes_report_threshold",
    "remove_substrings",
    "scan_sampled_read",
]
