"""双端 FASTQ 的流式重叠区碱基校正：成对进、成对出。

本文件负责文件级这一层（流式读、成对写出两个文件、统计、失败清理）；
一对 read 的校正逻辑在 ``algorithm.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from ...common.fastq import FastqWriter, is_gzip, read_fastq
from ...common.paired_overlap import OverlapConfig
from .algorithm import correct_pair_by_overlap


@dataclass(frozen=True, slots=True)
class PairedCorrectionSummary:
    """一次双端碱基校正的统计。

    校正**不改变 read 长度**，因此 ``input_bases`` 与 ``output_bases`` 恒等——
    保留两个字段是为了与其它预处理算法同一套口径，便于前端统一渲染。
    """

    total_pairs: int
    corrected_pairs: int
    corrected_reads: int
    corrected_bases: int
    input_bases: int
    output_bases: int

    @property
    def corrected_pair_rate(self) -> float:
        """被改过碱基的 read 对占比。输入为空时返回 0.0。"""
        if self.total_pairs == 0:
            return 0.0
        return self.corrected_pairs / self.total_pairs

    def render(self) -> str:
        return "\n".join(
            [
                f"输入 read 对：{self.total_pairs}",
                f"改过碱基的 read 对：{self.corrected_pairs}"
                f"（{self.corrected_pair_rate:.2%}），涉及 {self.corrected_reads} 条 read",
                f"改正的碱基：{self.corrected_bases}",
                f"碱基数：{self.input_bases} → {self.output_bases}（校正不改长度）",
            ]
        )


def correct_paired_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output1_path: str | Path,
    output2_path: str | Path,
    *,
    config: OverlapConfig | None = None,
    compress: bool | None = None,
) -> PairedCorrectionSummary:
    """流式成对读取两份 FASTQ，校正重叠区的错配碱基，成对写出两份 FASTQ。

    参数：
        read1_path / read2_path: 输入的两份 FASTQ（可 gzip，按魔数识别）。
        output1_path / output2_path: 两份输出 FASTQ。
        config: overlap 参数；``None`` 表示默认（与 fastp 一致）。
            两个质量门槛（Q30 / Q14）是上游写死的常量，不在这里。
        compress: 输出是否 gzip。``None``（默认）表示**跟随输入**。

    返回：
        :class:`PairedCorrectionSummary`。

    说明：
        **所有 read 都会被原样或修正后写出**——本算法不做过滤、不丢 read，
        也没检出重叠的对照常输出。两份输入的记录数必须一致；不一致或中途失败时，
        **两个半成品输出都会被删除**。
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
    corrected_pairs = 0
    corrected_reads = 0
    corrected_bases = 0
    input_bases = 0
    output_bases = 0

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

                result = correct_pair_by_overlap(record1, record2, config)
                if result.corrected:
                    corrected_pairs += 1
                    corrected_reads += result.corrected_reads
                    corrected_bases += result.corrected

                writer1.write(result.read1)
                writer2.write(result.read2)
                output_bases += result.read1.length + result.read2.length
    except Exception:
        output1_file.unlink(missing_ok=True)
        output2_file.unlink(missing_ok=True)
        raise

    return PairedCorrectionSummary(
        total_pairs=total_pairs,
        corrected_pairs=corrected_pairs,
        corrected_reads=corrected_reads,
        corrected_bases=corrected_bases,
        input_bases=input_bases,
        output_bases=output_bases,
    )
