"""UMI 处理：把 UMI 从序列或 index 提取出来、挂到 read 名字上。

与 fastp 1.3.x 的 ``UmiProcessor``（含它用到的 ``Read::trimFront`` /
``firstIndex`` / ``lastIndex``）逐位对齐。**单端与双端都支持**。

可独立使用（算法广场入口或 Python API），用法见同目录 ``umi_processing.md``。
"""

from .algorithm import (
    UMI_LOCATIONS,
    UPSTREAM_INDEX_VECTOR,
    UmiConfig,
    UmiResult,
    add_umi_to_name,
    first_index,
    last_index,
    process_umi,
    trim_front,
)
from .runner import UmiSummary, process_umi_fastq

__all__ = [
    "UMI_LOCATIONS",
    "UPSTREAM_INDEX_VECTOR",
    "UmiConfig",
    "UmiResult",
    "UmiSummary",
    "add_umi_to_name",
    "first_index",
    "last_index",
    "process_umi",
    "process_umi_fastq",
    "trim_front",
]
