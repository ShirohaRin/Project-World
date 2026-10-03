"""deduplication：重复序列检测与去重（fastp 的 ``Duplicate`` / ``--dedup``）。

实现与 fastp 1.3.x 的 ``Duplicate``（``src/duplicate.cpp``）逐位对齐——
布隆过滤器的比特布局、素数表生成方式、"序列 → 位置向量"的哈希都照抄。
可独立使用（算法广场入口或 Python API），用法见同目录 ``deduplication.md``。

    algorithm.py  序列层面的判重（布隆过滤器、素数表、位置向量）
    runner.py     文件级接口（单端 / 双端、只评估 / 去重、统计）

FASTQ 流式读写与压缩策略来自模块公共层
:mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    DEFAULT_ACCURACY_ANALYZE,
    DEFAULT_ACCURACY_DEDUP,
    SEQ_HASH_VALUE,
    DuplicateDetector,
    accuracy_memory_bytes,
    generate_prime_table,
)
from .runner import DuplicationSummary, deduplicate_fastq

__all__ = [
    "DEFAULT_ACCURACY_ANALYZE",
    "DEFAULT_ACCURACY_DEDUP",
    "SEQ_HASH_VALUE",
    "DuplicateDetector",
    "DuplicationSummary",
    "accuracy_memory_bytes",
    "deduplicate_fastq",
    "generate_prime_table",
]
