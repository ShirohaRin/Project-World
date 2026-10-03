"""index_filtering：按 index（barcode）黑名单过滤 reads。

把不属于本样本的 index（交叉污染、index hopping）在比对之前筛掉。
可独立使用（算法广场入口或 Python API），用法见同目录 ``index_filtering.md``。

    algorithm.py  黑名单匹配（含上游"只比较短长度"的口径）
    runner.py     文件级接口（单端 / 双端、失败清理）

名字里的 index 解析来自模块公共层
:mod:`modules.bio_analysis_function.common.read_names`，
FASTQ 流式读写来自 :mod:`modules.bio_analysis_function.common.fastq`。
"""

from .algorithm import (
    DEFAULT_THRESHOLD,
    IndexFilterConfig,
    is_filtered,
    matches_blacklist,
)
from .runner import IndexFilterSummary, filter_by_index_fastq

__all__ = [
    "DEFAULT_THRESHOLD",
    "IndexFilterConfig",
    "IndexFilterSummary",
    "filter_by_index_fastq",
    "is_filtered",
    "matches_blacklist",
]
