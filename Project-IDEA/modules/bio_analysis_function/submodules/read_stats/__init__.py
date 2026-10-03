"""read_stats：reads 质量统计（fastp 的 ``Stats``）。

实现与 fastp 1.3.x 的 ``Stats``（``src/stats.cpp``）逐位对齐——按位置的质量曲线、
碱基含量曲线、Q20/Q30/Q40 口径、5-mer 计数全部照抄。可独立使用（算法广场入口
或 Python API），用法见同目录 ``read_stats.md``。

    algorithm.py  统计累加与汇总（曲线、比例、k-mer）
    runner.py     文件级接口（流式读入）、JSON 序列化、可读报告

FASTQ 流式读取来自模块公共层
:mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    BASE_BUCKET_COUNT,
    BASE_CODES,
    CURVE_BASES,
    CYCLE_MARGIN,
    KMER_BUCKETS,
    KMER_LENGTH,
    MAX_PHRED,
    Q20_CHAR_CODE,
    Q30_CHAR_CODE,
    Q40_PHRED,
    ReadStatsCollector,
    ReadStatsSummary,
    base_bucket,
    base_code,
    kmer_name,
)
from .report import render_html
from .runner import render_report, report_to_json, stat_fastq

__all__ = [
    "BASE_BUCKET_COUNT",
    "BASE_CODES",
    "CURVE_BASES",
    "CYCLE_MARGIN",
    "KMER_BUCKETS",
    "KMER_LENGTH",
    "MAX_PHRED",
    "Q20_CHAR_CODE",
    "Q30_CHAR_CODE",
    "Q40_PHRED",
    "ReadStatsCollector",
    "ReadStatsSummary",
    "base_bucket",
    "base_code",
    "kmer_name",
    "render_html",
    "render_report",
    "report_to_json",
    "stat_fastq",
]
