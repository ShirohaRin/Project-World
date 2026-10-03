"""按 fastp ``OverlapAnalysis::merge`` 规则合并一对双端 read。"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.fastq import FastqRecord
from ...common.paired_overlap import OverlapConfig, analyze_overlap
from ...common.sequences import reverse_complement


@dataclass(frozen=True, slots=True)
class PairedMergeConfig:
    """双端合并参数；overlap 门槛与 fastp 命令行默认值一致。"""

    diff_limit: int = 5
    require: int = 30
    diff_percent_limit: float = 0.2
    allow_gap: bool = False

    def __post_init__(self) -> None:
        OverlapConfig(
            diff_limit=self.diff_limit,
            require=self.require,
            diff_percent_limit=self.diff_percent_limit,
            allow_gap=self.allow_gap,
        )

    def overlap_config(self) -> OverlapConfig:
        return OverlapConfig(
            diff_limit=self.diff_limit,
            require=self.require,
            diff_percent_limit=self.diff_percent_limit,
            allow_gap=self.allow_gap,
        )


@dataclass(frozen=True, slots=True)
class MergedRead:
    """一对 read 的合并结果；未检测到 overlap 时由 ``merge_pair`` 返回 None。"""

    name: str
    sequence: bytes
    quality: bytes
    # 这次合并的 overlap 结论是否来自"允许 1 个插入/缺失"那一轮。
    has_gap: bool = False

    def as_fastq_record(self) -> FastqRecord:
        return FastqRecord(self.name, self.sequence, self.quality)


def merge_pair(
    read1: FastqRecord,
    read2: FastqRecord,
    config: PairedMergeConfig | OverlapConfig | None = None,
) -> MergedRead | None:
    """按 fastp 的单对 read 合并规则返回合并 read。

    overlap 区不做质量共识修正：read1 的序列和质量始终作为主串，read2 只在
    ``offset > 0`` 时从 overlap 之后的尾部补入；read2 的质量只反转，不解码。
    """
    if len(read1.sequence) != len(read1.quality):
        raise ValueError("read1 的序列长度与质量长度不一致。")
    if len(read2.sequence) != len(read2.quality):
        raise ValueError("read2 的序列长度与质量长度不一致。")

    overlap_config = (
        config.overlap_config() if isinstance(config, PairedMergeConfig) else config
    )
    overlap = analyze_overlap(read1.sequence, read2.sequence, overlap_config)
    if not overlap.overlapped:
        return None

    reverse2 = reverse_complement(read2.sequence)
    reverse2_quality = read2.quality[::-1]
    overlap_len = overlap.overlap_len
    len1 = overlap_len + max(0, overlap.offset)
    len2 = len(read2.sequence) - overlap_len if overlap.offset > 0 else 0

    sequence = read1.sequence[:len1]
    quality = read1.quality[:len1]
    if overlap.offset > 0:
        sequence += reverse2[overlap_len : overlap_len + len2]
        quality += reverse2_quality[overlap_len : overlap_len + len2]

    return MergedRead(
        name=f"{read1.name} merged_{len1}_{len2}",
        sequence=sequence,
        quality=quality,
        has_gap=overlap.has_gap,
    )
