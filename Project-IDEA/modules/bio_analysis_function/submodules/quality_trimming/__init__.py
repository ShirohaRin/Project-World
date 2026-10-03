"""quality_trimming：reads 滑窗质量剪切。

实现与 fastp 的 ``cut_front`` / ``cut_right`` / ``cut_tail`` 对齐，
可独立使用（算法广场入口或 Python API），用法见同目录 ``quality_trimming.md``。

本子模块只保留算法与文件级接口两层；FASTQ 流式读写与文件级管道位于
模块公共层 :mod:`modules.bio_analysis_function.common.fastq`，
需要直接读写 FASTQ 时请从那里导入。

    algorithm.py  单条 read 的核心算法
    runner.py     文件级接口（把算法接到公共层的 FASTQ 管道上）
"""

from ...common.fastq import FastqStreamSummary
from .algorithm import QualityCutConfig, TrimResult, trim_and_cut
from .runner import trim_fastq

__all__ = [
    "QualityCutConfig",
    "TrimResult",
    "trim_and_cut",
    "FastqStreamSummary",
    "trim_fastq",
]
