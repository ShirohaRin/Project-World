"""adapter_trimming：把接头从 read 上剪掉。

实现与 fastp 1.3.x 的 ``AdapterTrimmer::trimBySequence`` / ``trimByMultiSequences``
逐位对齐，可独立使用（算法广场入口或 Python API），用法见同目录 ``adapter_trimming.md``。

    algorithm.py  单条 read 的裁剪（找接头、切掉、容错）
    runner.py     文件级接口（含"接头来源"三种：手动指定 / 候选表 / 自动检测）

接头来源为"自动检测"时，会调用公共层的接头检测
:mod:`modules.bio_analysis_function.common.adapter_detection`（它是工具，不是独立入口，
见 `开发规则.md` 3.2）。
"""

from .algorithm import (
    AdapterTrimConfig,
    AdapterTrimResult,
    match_required_for,
    match_with_one_insertion,
    trim_adapter,
    trim_adapters,
)
from .runner import AdapterTrimSummary, trim_adapter_fastq

__all__ = [
    "AdapterTrimConfig",
    "AdapterTrimResult",
    "AdapterTrimSummary",
    "match_required_for",
    "match_with_one_insertion",
    "trim_adapter",
    "trim_adapter_fastq",
    "trim_adapters",
]
