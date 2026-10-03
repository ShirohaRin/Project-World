"""双端 read 合并：按 fastp overlap 结果输出合并后的单端 FASTQ。"""

from .algorithm import MergedRead, PairedMergeConfig, merge_pair
from .runner import PairedEndMergeSummary, merge_paired_fastq

__all__ = [
    "MergedRead",
    "PairedMergeConfig",
    "PairedEndMergeSummary",
    "merge_pair",
    "merge_paired_fastq",
]
