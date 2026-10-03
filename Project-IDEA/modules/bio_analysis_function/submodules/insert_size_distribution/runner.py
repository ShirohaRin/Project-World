"""文件级接口：把双端插入片段长度统计跑在一对 FASTQ 上。

薄薄一层——逐对读入、交给 :mod:`common.paired_overlap` 判断重叠、
把结论喂给 ``algorithm.py`` 的直方图。**不产出任何文件**。
"""

from __future__ import annotations

from itertools import zip_longest
from pathlib import Path

from ...common.fastq import read_fastq
from ...common.paired_overlap import OverlapConfig, analyze_overlap
from .algorithm import InsertSizeConfig, InsertSizeHistogram, InsertSizeSummary


def analyze_insert_size(
    read1_path: str | Path,
    read2_path: str | Path,
    *,
    config: InsertSizeConfig | None = None,
    overlap: OverlapConfig | None = None,
) -> InsertSizeSummary:
    """流式成对读完两份 FASTQ，统计插入片段长度分布。

    参数：
        read1_path / read2_path: 输入的两份 FASTQ（可 gzip，按魔数识别）。
        config: 直方图参数（上限）；``None`` 表示默认 512。
        overlap: 重叠判定的参数（最短重叠、错配上限等）；
            ``None`` 表示与 fastp 命令行默认一致。

    返回：
        :class:`InsertSizeSummary`，含直方图、峰值与"判不出"的条数。

    说明：
        **本算法只读不写**，不产出任何测序文件。
        两份输入的记录数必须一致，不一致抛 ``ValueError``。
        片段长度是**推算**出来的（靠重叠关系倒推），不是测出来的：
        找不到重叠的那些对进"判不出"桶，``overlap_rate`` 告诉你这个桶有多大。
        重叠判定的参数会直接影响结果——要求越严，判不出的越多。

    异常：
        ``ValueError``：文件不存在，或两份 FASTQ 的记录数不一致。
    """
    read1_file = Path(read1_path)
    read2_file = Path(read2_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")
    if not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")

    histogram = InsertSizeHistogram(config)
    sentinel = object()
    pairs = zip_longest(
        read_fastq(read1_file), read_fastq(read2_file), fillvalue=sentinel
    )
    for record1, record2 in pairs:
        if record1 is sentinel or record2 is sentinel:
            raise ValueError("两份配对 FASTQ 的记录数不一致。")
        histogram.add(analyze_overlap(record1.sequence, record2.sequence, overlap))
    return histogram.summarize()
