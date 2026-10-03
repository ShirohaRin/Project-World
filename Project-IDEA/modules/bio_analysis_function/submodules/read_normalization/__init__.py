"""read_normalization：reads 规范化（质量编码转换 + MGI 名字修复）。

处理链最前面的一步：把格式上需要先归置的东西归置好。可独立使用
（算法广场入口或 Python API），用法见同目录 ``read_normalization.md``。

    algorithm.py  记录级转换（质量重编码、名字修复、编码探测）
    runner.py     文件级接口（单端 / 双端、失败清理）

FASTQ 流式读写与压缩策略来自模块公共层
:mod:`modules.bio_analysis_function.common.fastq`，
名字解析来自 :mod:`modules.bio_analysis_function.common.read_names`。
"""

from .algorithm import (
    PHRED_OFFSET_33,
    PHRED_OFFSET_64,
    NormalizeConfig,
    NormalizeOutcome,
    convert_phred64_to_phred33,
    detect_phred_offset,
    normalize_record,
)
from .runner import NormalizeSummary, detect_quality_offset, normalize_fastq

__all__ = [
    "PHRED_OFFSET_33",
    "PHRED_OFFSET_64",
    "NormalizeConfig",
    "NormalizeOutcome",
    "NormalizeSummary",
    "convert_phred64_to_phred33",
    "detect_phred_offset",
    "detect_quality_offset",
    "normalize_fastq",
    "normalize_record",
]
