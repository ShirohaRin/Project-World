"""双端 FASTQ 的流式按-overlap 接头裁剪：成对进、成对出。

本文件负责文件级这一层（流式读、成对写出两个文件、统计、失败清理）；
一对 read 的裁剪逻辑在 ``algorithm.py``。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Final

from ...common.fastq import FastqWriter, is_gzip, read_fastq
from ...common.paired_overlap import OverlapConfig
from .algorithm import PairedAdapterTrimConfig, trim_pair_by_overlap

#: 报告里最多保留多少条"切下来的接头序列"（与单端接头裁剪的口径一致）。
_TOP_ADAPTERS: Final = 5


@dataclass(frozen=True, slots=True)
class PairedAdapterTrimSummary:
    """一次双端接头裁剪的统计。

    ``trimmed_reads`` 恒等于 ``2 * trimmed_pairs``：这个算法要么两条一起裁，
    要么都不裁（上游对两条 read 都记一次"裁过"）。
    """

    total_pairs: int
    trimmed_pairs: int
    trimmed_reads: int
    input_bases: int
    output_bases: int
    trimmed_bases: int
    top_adapters: tuple[tuple[bytes, int], ...] = ()

    @property
    def trimmed_pair_rate(self) -> float:
        """被裁过的 read 对占比。输入为空时返回 0.0。"""
        if self.total_pairs == 0:
            return 0.0
        return self.trimmed_pairs / self.total_pairs

    def render(self) -> str:
        lines = [
            f"输入 read 对：{self.total_pairs}",
            f"裁过接头的 read 对：{self.trimmed_pairs}"
            f"（{self.trimmed_pair_rate:.2%}）；按 read 计为 {self.trimmed_reads} 条",
            f"切下的接头碱基：{self.trimmed_bases}",
            f"碱基数：{self.input_bases} → {self.output_bases}",
        ]
        for adapter, count in self.top_adapters:
            preview = adapter[:40].decode("ascii", "replace")
            lines.append(f"  · 切下来的 {preview}：{count} 次")
        return "\n".join(lines)


def trim_paired_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output1_path: str | Path,
    output2_path: str | Path,
    *,
    config: PairedAdapterTrimConfig | OverlapConfig | None = None,
    compress: bool | None = None,
) -> PairedAdapterTrimSummary:
    """流式成对读取两份 FASTQ，按 overlap 裁掉接头，成对写出两份 FASTQ。

    参数：
        read1_path / read2_path: 输入的两份 FASTQ（可 gzip，按魔数识别）。
        output1_path / output2_path: 两份输出 FASTQ。
        config: 裁剪参数；``None`` 表示默认（与 fastp 一致）。
        compress: 输出是否 gzip。``None``（默认）表示**跟随输入**——任一输入是 gzip
            就让两个输出都用 gzip。

    返回：
        :class:`PairedAdapterTrimSummary`。

    说明：
        **没裁到的 read 对原样写出**——本算法只裁剪、不丢弃 read（与单端的
        接头裁剪同一口径），长度被裁到多少都不做过滤。
        两份输入的记录数必须一致；不一致或中途失败时，**两个半成品输出都会被删除**。
    """
    read1_file = Path(read1_path)
    read2_file = Path(read2_path)
    output1_file = Path(output1_path)
    output2_file = Path(output2_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")
    if not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")
    if compress is None:
        compress = is_gzip(read1_file) or is_gzip(read2_file)

    sentinel = object()
    total_pairs = 0
    trimmed_pairs = 0
    input_bases = 0
    output_bases = 0
    trimmed_bases = 0
    adapters: Counter[bytes] = Counter()

    try:
        with (
            FastqWriter(output1_file, compress=compress) as writer1,
            FastqWriter(output2_file, compress=compress) as writer2,
        ):
            for record1, record2 in zip_longest(
                read_fastq(read1_file), read_fastq(read2_file), fillvalue=sentinel
            ):
                if record1 is sentinel or record2 is sentinel:
                    raise ValueError("两份配对 FASTQ 的记录数不一致。")

                total_pairs += 1
                input_bases += record1.length + record2.length

                result = trim_pair_by_overlap(record1, record2, config)
                if result is None:
                    # 没检出可裁的接头：原样写出，保留这对 read。
                    writer1.write(record1)
                    writer2.write(record2)
                    output_bases += record1.length + record2.length
                    continue

                trimmed_pairs += 1
                trimmed_bases += result.trimmed_bases
                for adapter in (result.adapter1, result.adapter2):
                    if adapter:
                        adapters[adapter] += 1
                writer1.write(result.read1)
                writer2.write(result.read2)
                output_bases += result.read1.length + result.read2.length
    except Exception:
        output1_file.unlink(missing_ok=True)
        output2_file.unlink(missing_ok=True)
        raise

    return PairedAdapterTrimSummary(
        total_pairs=total_pairs,
        trimmed_pairs=trimmed_pairs,
        trimmed_reads=trimmed_pairs * 2,
        input_bases=input_bases,
        output_bases=output_bases,
        trimmed_bases=trimmed_bases,
        top_adapters=tuple(adapters.most_common(_TOP_ADAPTERS)),
    )
