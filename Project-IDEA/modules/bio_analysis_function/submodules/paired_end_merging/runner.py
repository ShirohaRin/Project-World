"""双端 FASTQ 的流式成对合并。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from ...common.fastq import FastqRecord, is_gzip, read_fastq, write_fastq
from ...common.paired_overlap import OverlapConfig
from .algorithm import PairedMergeConfig, merge_pair


@dataclass(frozen=True, slots=True)
class PairedEndMergeSummary:
    """一次双端合并的成对与碱基统计。"""

    total_pairs: int
    merged_pairs: int
    unmerged_pairs: int
    input_bases: int
    output_bases: int
    gap_overlaps: int


def merge_paired_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output_path: str | Path,
    *,
    config: PairedMergeConfig | OverlapConfig | None = None,
    compress: bool | None = None,
) -> PairedEndMergeSummary:
    """流式成对读取两份 FASTQ，只写出成功合并的 read。

    ``compress=None`` 时，只要任一输入按 gzip 魔数识别为压缩，输出就使用 gzip。
    两份输入的记录数必须一致；不一致或处理失败时删除已写出的输出文件。
    """
    read1_file = Path(read1_path)
    read2_file = Path(read2_path)
    output_file = Path(output_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")
    if not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")
    if compress is None:
        compress = is_gzip(read1_file) or is_gzip(read2_file)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    sentinel = object()
    total_pairs = 0
    merged_pairs = 0
    unmerged_pairs = 0
    input_bases = 0
    output_bases = 0
    gap_overlaps = 0

    def merged_records():
        nonlocal total_pairs, merged_pairs, unmerged_pairs, input_bases, output_bases
        nonlocal gap_overlaps
        for record1, record2 in zip_longest(
            read_fastq(read1_file), read_fastq(read2_file), fillvalue=sentinel
        ):
            if record1 is sentinel or record2 is sentinel:
                raise ValueError("两份配对 FASTQ 的记录数不一致。")
            total_pairs += 1
            input_bases += record1.length + record2.length
            merged = merge_pair(record1, record2, config)
            if merged is None:
                unmerged_pairs += 1
                continue
            merged_pairs += 1
            output_bases += len(merged.sequence)
            if merged.has_gap:
                gap_overlaps += 1
            yield merged.as_fastq_record()

    try:
        written = write_fastq(merged_records(), output_file, compress=compress)
    except Exception:
        output_file.unlink(missing_ok=True)
        raise

    if written != merged_pairs:
        output_file.unlink(missing_ok=True)
        raise RuntimeError(
            f"内部统计不一致：写出 {written} 条，统计合并 {merged_pairs} 条。"
        )
    return PairedEndMergeSummary(
        total_pairs=total_pairs,
        merged_pairs=merged_pairs,
        unmerged_pairs=unmerged_pairs,
        input_bases=input_bases,
        output_bases=output_bases,
        gap_overlaps=gap_overlaps,
    )
