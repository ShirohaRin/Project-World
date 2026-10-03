"""paired_end_adapter_trimming：按 overlap 把接头从一对双端 read 上剪掉。

**设计目标：与 fastp 1.3.x 的 ``AdapterTrimmer::trimByOverlapAnalysis``
（``src/adaptertrimmer.cpp``）逐位一致**。改这里之前请先读同目录的
`paired_end_adapter_trimming.md`。

它解决的是**双端数据特有的**接头问题：插入片段比读长还短时，两条 read 都会一路读到
接头。单端做法（``submodules/adapter_trimming``）是靠比对已知接头序列去找；双端有更
直接的证据——两条 read 来自同一个片段，把它们对齐就能看出片段到哪里为止。

**几何要点**（推导见 `common/paired_overlap` 的模块文档）：只有当重叠偏移量
``offset < 0`` 时才有接头可裁，那正是"片段短于读长、两端都读穿"的情形，
此时 ``overlap_len`` 就是**片段长度**，两条 read 各自保留到片段末端即可。

本文件只做一对 read 的裁剪，不读写文件；文件级接口见 ``runner.py``。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.fastq import FastqRecord
from ...common.paired_overlap import OverlapConfig, analyze_overlap


@dataclass(frozen=True, slots=True)
class PairedAdapterTrimConfig:
    """PE 按 overlap 裁接头的参数。

    | 字段 | fastp 对应物 | 默认 | 说明 |
    | --- | --- | --- | --- |
    | `diff_limit` | `--overlap_diff_limit` | 5 | 重叠区允许的最大错配数 |
    | `require` | `--overlap_len_require` | 30 | 认定为重叠所需的最短重叠长度 |
    | `diff_percent_limit` | `--overlap_diff_percent_limit` | 0.2 | 错配数占比上限 |
    | `allow_gap` | `--allow_gap_overlap_trimming` | `False` | 是否允许 1 个插入/缺失 |
    | `front_trimmed1` | `trim_front1` 的实剪量 | 0 | R1 头部**已经被剪掉**的碱基数 |
    | `front_trimmed2` | `trim_front2` 的实剪量 | 0 | R2 头部**已经被剪掉**的碱基数 |

    ``front_trimmed1/2`` 是几何补偿量：如果数据在到本算法之前已经做过头部固定修剪
    （例如先跑过带 `trimFront` 的质量剪切），要把**当时剪掉的碱基数**填进来，保留长度
    才算得对。没剪过就保持 0。
    """

    diff_limit: int = 5
    require: int = 30
    diff_percent_limit: float = 0.2
    allow_gap: bool = False
    front_trimmed1: int = 0
    front_trimmed2: int = 0

    def __post_init__(self) -> None:
        # 借用 OverlapConfig 的校验，避免同一套边界在两处各写一遍。
        OverlapConfig(
            diff_limit=self.diff_limit,
            require=self.require,
            diff_percent_limit=self.diff_percent_limit,
            allow_gap=self.allow_gap,
        )
        if self.front_trimmed1 < 0:
            raise ValueError(
                f"front_trimmed1 不能为负数，当前为 {self.front_trimmed1}。"
            )
        if self.front_trimmed2 < 0:
            raise ValueError(
                f"front_trimmed2 不能为负数，当前为 {self.front_trimmed2}。"
            )

    def overlap_config(self) -> OverlapConfig:
        return OverlapConfig(
            diff_limit=self.diff_limit,
            require=self.require,
            diff_percent_limit=self.diff_percent_limit,
            allow_gap=self.allow_gap,
        )


@dataclass(frozen=True, slots=True)
class PairedTrimResult:
    """一次成功裁剪的结果。

    ``read1`` / ``read2`` 是裁掉接头之后的记录；``adapter1`` / ``adapter2`` 是被裁下来的
    那两段序列（上游把它们记进统计，用来汇总"检出了哪些接头"）。
    ``offset`` / ``overlap_len`` 是这次裁剪依据的 overlap 结论。
    """

    read1: FastqRecord
    read2: FastqRecord
    adapter1: bytes
    adapter2: bytes
    offset: int
    overlap_len: int

    @property
    def trimmed_bases(self) -> int:
        return len(self.adapter1) + len(self.adapter2)


def trim_pair_by_overlap(
    read1: FastqRecord,
    read2: FastqRecord,
    config: PairedAdapterTrimConfig | OverlapConfig | None = None,
) -> PairedTrimResult | None:
    """按 overlap 结论裁掉两条 read 尾部的接头；没裁到时返回 ``None``。

    参数：
        read1 / read2: 一对 read（原始方向；函数内部自己取 R2 的反向互补）。
        config: 参数；``None`` 表示默认（与 fastp 一致）。也接受裸的
            :class:`~...common.paired_overlap.OverlapConfig`（此时头部补偿量按 0 处理）。

    返回：
        :class:`PairedTrimResult`，或 ``None`` 表示**这对 read 没有可裁的接头**
        （不重叠，或偏移量不为负）。返回 ``None`` 是正常结论，不是错误。

    保留长度照上游取::

        len1 = min(len(R1), overlap_len + front_trimmed2)
        len2 = min(len(R2), overlap_len + front_trimmed1)

    注意两个补偿量是**交叉使用**的：R1 保留多长取决于 **R2** 头部被剪掉多少。这不是
    笔误——片段右端少了 ``front_trimmed2`` 个碱基，R1 要保留到的是那个新末端。
    """
    if len(read1.sequence) != len(read1.quality):
        raise ValueError("read1 的序列长度与质量长度不一致。")
    if len(read2.sequence) != len(read2.quality):
        raise ValueError("read2 的序列长度与质量长度不一致。")

    if isinstance(config, PairedAdapterTrimConfig):
        settings = config
        overlap_config: OverlapConfig = config.overlap_config()
    else:
        settings = PairedAdapterTrimConfig()
        overlap_config = config or OverlapConfig()

    overlap = analyze_overlap(read1.sequence, read2.sequence, overlap_config)
    # 只有"片段短于读长、两端都读穿"才有接头可裁；上游写的就是 offset < 0。
    if not (overlap.overlapped and overlap.offset < 0):
        return None

    overlap_len = overlap.overlap_len
    keep1 = min(len(read1.sequence), overlap_len + settings.front_trimmed2)
    keep2 = min(len(read2.sequence), overlap_len + settings.front_trimmed1)

    return PairedTrimResult(
        read1=FastqRecord(read1.name, read1.sequence[:keep1], read1.quality[:keep1]),
        read2=FastqRecord(read2.name, read2.sequence[:keep2], read2.quality[:keep2]),
        adapter1=read1.sequence[keep1:],
        adapter2=read2.sequence[keep2:],
        offset=overlap.offset,
        overlap_len=overlap_len,
    )
