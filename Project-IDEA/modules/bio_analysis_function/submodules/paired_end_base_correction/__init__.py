"""重叠区碱基校正：用双端重叠里高质量的碱基改正对侧的低质量碱基。

与 fastp 1.3.x 的 ``BaseCorrector::correctByOverlapAnalysis`` 逐位对齐。
成对 FASTQ 进、成对 FASTQ 出，**不改长度、不丢 read**。

可独立使用（算法广场入口或 Python API），用法见同目录
``paired_end_base_correction.md``。
"""

from .algorithm import (
    UPSTREAM_TEST_VECTOR,
    CorrectionResult,
    correct_pair_by_overlap,
)
from .runner import PairedCorrectionSummary, correct_paired_fastq

__all__ = [
    "CorrectionResult",
    "PairedCorrectionSummary",
    "UPSTREAM_TEST_VECTOR",
    "correct_pair_by_overlap",
    "correct_paired_fastq",
]
