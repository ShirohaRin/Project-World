"""PE 按 overlap 裁接头：成对 FASTQ 进，成对 FASTQ 出。

与 fastp 1.3.x 的 ``AdapterTrimmer::trimByOverlapAnalysis`` 逐位对齐。
它处理的是**双端数据特有的**接头情形——插入片段比读长还短时两条 read 都读到了接头；
判据来自两条 read 的前后重叠（``common/paired_overlap``，工具），不需要预先知道接头序列。

可独立使用（算法广场入口或 Python API），用法见同目录 ``paired_end_adapter_trimming.md``。
"""

from .algorithm import (
    PairedAdapterTrimConfig,
    PairedTrimResult,
    trim_pair_by_overlap,
)
from .runner import PairedAdapterTrimSummary, trim_paired_fastq

__all__ = [
    "PairedAdapterTrimConfig",
    "PairedAdapterTrimSummary",
    "PairedTrimResult",
    "trim_pair_by_overlap",
    "trim_paired_fastq",
]
