"""adapter_detection：从 reads 里检测接头序列（**公共层工具**，不是算法入口）。

实现与 fastp 1.3.x 的 ``Evaluator::evalAdapterAndReadNum`` 逐位对齐。

**产出**：一条接头序列 + 来源（命中内置已知接头表 / 从数据中拼出）+ 证据
（采样规模、种子 k-mer、出现次数、富集倍数）；没检出时给出原因。
**不写任何文件**，也不改动输入数据。

**为什么是工具**：按 `开发规则.md` 3.2 的第 2 类——产出自身价值不大、
注定要被接头裁剪消费。因此它没有独立的算法广场入口，而是在接头裁剪里作为
一个选项出现（"接头来源：自动检测 / 手动指定"）。

    algorithm.py      检测算法本体（已知接头表 + k-mer 富集 + 前缀树延伸）
    runner.py         文件级接口（采样、调用算法）

已知接头表在公共层上一级：``common/known_adapters.py``（检测与裁剪共用）。
"""

from ..known_adapters import KNOWN_ADAPTERS
from .algorithm import (
    AdapterDetection,
    AdapterDetectionConfig,
    check_known_adapters,
    detect_adapter,
    detect_from_kmers,
    match_known_adapter,
)
from .runner import detect_adapter_from_file, sample_sequences

__all__ = [
    "KNOWN_ADAPTERS",
    "AdapterDetection",
    "AdapterDetectionConfig",
    "check_known_adapters",
    "detect_adapter",
    "detect_adapter_from_file",
    "detect_from_kmers",
    "match_known_adapter",
    "sample_sequences",
]
