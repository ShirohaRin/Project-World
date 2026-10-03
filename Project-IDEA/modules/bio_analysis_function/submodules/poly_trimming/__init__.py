"""poly_trimming：reads 尾部 polyG / polyX 修剪。

实现与 fastp 的 ``trimPolyG`` / ``trimPolyX`` 对齐，
可独立使用（算法广场入口或 Python API），用法见同目录 ``poly_trimming.md``。

本子模块只保留算法与文件级接口两层；FASTQ 流式读写与文件级管道位于
模块公共层 :mod:`modules.bio_analysis_function.common.fastq`。

    algorithm.py  单条 read 的 polyG / polyX 判定与裁剪
    runner.py     文件级接口（把算法接到公共层的 FASTQ 管道上）
"""

from ...common.fastq import FastqStreamSummary
from .algorithm import (
    PolyTrimConfig,
    PolyTrimResult,
    trim_poly_g,
    trim_poly_tails,
    trim_poly_x,
)
from .runner import trim_poly_fastq

__all__ = [
    "PolyTrimConfig",
    "PolyTrimResult",
    "trim_poly_g",
    "trim_poly_x",
    "trim_poly_tails",
    "FastqStreamSummary",
    "trim_poly_fastq",
]
