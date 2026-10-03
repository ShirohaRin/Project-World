"""UMI 提取的文件级接口：单端与双端各一条路径。

单端只读一份、写一份；双端成对读、成对写。两条路径共用同一份逐条逻辑
（``algorithm.py`` 的 ``process_umi``），只是输入输出的条数不同。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from ...common.fastq import FastqWriter, is_gzip, read_fastq, write_fastq
from .algorithm import UmiConfig, process_umi


@dataclass(frozen=True, slots=True)
class UmiSummary:
    """一次 UMI 提取的统计。

    计数都按 **read 条数**算（双端时一对算两条），这样单端与双端的结果可直接比较。
    ``reads_with_umi`` 的判据是"名字真的被改动了"——`per_index` 在名字里没有 index 时
    也会留下一个分隔符，那种情况算改动过。
    """

    total_reads: int
    reads_with_umi: int
    trimmed_bases: int
    input_bases: int
    output_bases: int

    @property
    def umi_rate(self) -> float:
        """挂上 UMI 的 read 占比。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.reads_with_umi / self.total_reads

    def render(self) -> str:
        return "\n".join(
            [
                f"输入 read 数：{self.total_reads}",
                f"挂上 UMI 的 read：{self.reads_with_umi}（{self.umi_rate:.2%}）",
                f"从序列里剪掉的碱基：{self.trimmed_bases}",
                f"碱基数：{self.input_bases} → {self.output_bases}",
            ]
        )


def _accumulate(summary: dict, before, after) -> None:
    """把一对（或一条）read 的前后差异累加进统计。"""
    summary["total_reads"] += 1
    summary["input_bases"] += len(before.sequence)
    summary["output_bases"] += len(after.sequence)
    summary["trimmed_bases"] += len(before.sequence) - len(after.sequence)
    if after.name != before.name:
        summary["reads_with_umi"] += 1


def process_umi_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    config: UmiConfig | None = None,
    compress: bool | None = None,
) -> UmiSummary:
    """提取 UMI 并写出处理后的 FASTQ。

    参数：
        read1_path / output1_path: 正向 reads 的输入与输出。
        read2_path / output2_path: 反向 reads 的输入与输出；**单端数据两者都留空**。
            给了其中一个就必须给另一个。
        config: 参数；``None`` 表示默认。
        compress: 输出是否 gzip。``None``（默认）表示**跟随输入**——
            单端看 R1，双端任一为 gzip 就用 gzip。

    返回：
        :class:`UmiSummary`。

    说明：
        **不丢 read**：所有记录都会写出，UMI 取不到时名字与序列原样保留。
        双端时两份输入的记录数必须一致；不一致或中途失败时会删除已写出的半成品。

    注意：
        选了 ``index2`` / ``read2`` 却没给 R2 时**不会报错**，只是取不到 UMI
        （与上游一致）。这是使用上的坑，产品侧应在表单里提示。
    """
    read1_file = Path(read1_path)
    output1_file = Path(output1_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")

    if (read2_path is None) != (output2_path is None):
        raise ValueError("read2_path 与 output2_path 必须同时给出或同时留空。")

    summary = {
        "total_reads": 0,
        "reads_with_umi": 0,
        "trimmed_bases": 0,
        "input_bases": 0,
        "output_bases": 0,
    }

    if read2_path is None:
        return _process_single(read1_file, output1_file, config, compress, summary)

    read2_file = Path(read2_path)
    if not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")
    return _process_paired(
        read1_file, read2_file, output1_file, Path(output2_path), config, compress,
        summary,
    )


def _process_single(
    read1_file: Path,
    output1_file: Path,
    config: UmiConfig | None,
    compress: bool | None,
    summary: dict,
) -> UmiSummary:
    if compress is None:
        compress = is_gzip(read1_file)

    def transformed():
        for record in read_fastq(read1_file):
            result = process_umi(record, None, config)
            _accumulate(summary, record, result.read1)
            yield result.read1

    try:
        write_fastq(transformed(), output1_file, compress=compress)
    except Exception:
        output1_file.unlink(missing_ok=True)
        raise
    return UmiSummary(**summary)


def _process_paired(
    read1_file: Path,
    read2_file: Path,
    output1_file: Path,
    output2_file: Path,
    config: UmiConfig | None,
    compress: bool | None,
    summary: dict,
) -> UmiSummary:
    if compress is None:
        compress = is_gzip(read1_file) or is_gzip(read2_file)

    sentinel = object()
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

                result = process_umi(record1, record2, config)
                _accumulate(summary, record1, result.read1)
                _accumulate(summary, record2, result.read2)
                writer1.write(result.read1)
                writer2.write(result.read2)
    except Exception:
        output1_file.unlink(missing_ok=True)
        output2_file.unlink(missing_ok=True)
        raise
    return UmiSummary(**summary)
