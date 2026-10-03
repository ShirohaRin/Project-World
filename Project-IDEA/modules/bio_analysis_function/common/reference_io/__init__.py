"""参考资料读取：把 GenBank / FASTA 变成"序列 + 特征 + 位置"。

这是参考比对与变异检测（breseq 方向）这条线的地基：比对器要参考序列，
突变注释要特征表与坐标。它属于模块公共层的工具——自身不回答生物学问题，
只是别的算法必须使用的读入能力，因此**没有算法广场入口**。

对外三个入口：

- :func:`read_genbank` / :func:`parse_genbank`：GenBank（序列 + 拓扑 + FEATURES 注释）；
- :func:`read_fasta` / :func:`parse_fasta`：FASTA（只有序列，拓扑按线状处理）；
- :func:`parse_location`：单独解析位置写法（GenBank 与后续 GFF3 共用）。

数据结构与坐标约定见 :mod:`.model` 的模块文档。
"""

from __future__ import annotations

from .fasta import parse_fasta, read_fasta
from .genbank import parse_genbank, parse_location, read_genbank
from .model import (
    Feature,
    Location,
    Part,
    ReferenceFormatError,
    ReferenceSequence,
    ReferenceSet,
)

__all__ = [
    "Feature",
    "Location",
    "Part",
    "ReferenceFormatError",
    "ReferenceSequence",
    "ReferenceSet",
    "parse_fasta",
    "parse_genbank",
    "parse_location",
    "read_fasta",
    "read_genbank",
]
