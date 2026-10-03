"""insert_size_distribution：双端插入片段长度分布（fastp 的 insert size histogram）。

靠两条 read 的重叠关系倒推每对的片段长度，汇成直方图并找出峰值。
可独立使用（算法广场入口或 Python API），用法见同目录
``insert_size_distribution.md``。

    algorithm.py  直方图累加与峰值判定
    runner.py     文件级接口（成对流式读入）

重叠判定来自模块公共层 :mod:`modules.bio_analysis_function.common.paired_overlap`，
FASTQ 流式读取来自 :mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    DEFAULT_MAX_SIZE,
    InsertSizeConfig,
    InsertSizeHistogram,
    InsertSizeSummary,
)
from .runner import analyze_insert_size

__all__ = [
    "DEFAULT_MAX_SIZE",
    "InsertSizeConfig",
    "InsertSizeHistogram",
    "InsertSizeSummary",
    "analyze_insert_size",
]
