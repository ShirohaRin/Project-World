"""文件级接口：按 index 黑名单过滤一份（或一对）FASTQ。

薄薄一层——流式读入、逐条（或逐对）判定、写出留下的。复用公共层的
:func:`~modules.bio_analysis_function.common.fastq.read_fastq` /
:class:`~modules.bio_analysis_function.common.fastq.FastqWriter`，
以及同一套"失败就删掉半成品"的处理方式。
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from ...common.fastq import FastqWriter, is_gzip, read_fastq
from .algorithm import IndexFilterConfig, is_filtered


@dataclass(frozen=True, slots=True)
class IndexFilterSummary:
    """一次 index 过滤的统计。

    ``total_reads`` 单端时是 **read 条数**，双端时是 **read 对数**
    （一对被一起判定、一起丢弃），与去重同口径。
    ``filtered_reads`` 与它同单位。
    """

    total_reads: int
    filtered_reads: int
    input_bases: int
    output_bases: int
    paired: bool = False
    enabled: bool = False

    @property
    def kept_reads(self) -> int:
        return self.total_reads - self.filtered_reads

    @property
    def filtered_rate(self) -> float:
        """被过滤的比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.filtered_reads / self.total_reads

    def render(self) -> str:
        unit = "read 对" if self.paired else "read"
        lines = [
            f"输入 {unit}：{self.total_reads}",
            f"被 index 过滤：{self.filtered_reads}（{self.filtered_rate:.2%}）",
            f"碱基数：{self.input_bases} → {self.output_bases}",
        ]
        if not self.enabled:
            lines.append("（两份黑名单都是空的，没有过滤任何 read）")
        return "\n".join(lines)


def filter_by_index_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    config: IndexFilterConfig | None = None,
    compress: bool | None = None,
) -> IndexFilterSummary:
    """流式读取（一对）FASTQ，按 index 黑名单过滤后写出。

    参数：
        read1_path / output1_path: 输入与输出的 R1。
        read2_path / output2_path: 双端时给出，单端留空。两者必须同给或同留空。
        config: 黑名单与阈值；``None`` 表示两份黑名单都空——**此时不过滤任何
            read**，只是把数据抄一遍（上游同样如此）。
        compress: 输出是否 gzip。``None``（默认）表示跟随输入。

    返回：
        :class:`IndexFilterSummary`。

    说明：
        **不改碱基、不改名字**，只决定去留。双端时任一端命中就丢整对，
        两份输出始终对齐。中途失败时**所有半成品都会被删除**。

    异常：
        ``ValueError``：文件不存在、两份输出的组合不合法、两份配对 FASTQ
        的记录数不一致。
    """
    read1_file = Path(read1_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")

    paired = read2_path is not None
    if paired:
        if output2_path is None:
            raise ValueError("双端过滤需要同时给出两份输出路径。")
    elif output2_path is not None:
        raise ValueError("没有给出 R2 输入，却给了第二份输出路径。")

    read2_file = Path(read2_path) if paired else None
    if read2_file is not None and not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")

    settings = config or IndexFilterConfig()
    if compress is None:
        compress = is_gzip(read1_file) or (
            read2_file is not None and is_gzip(read2_file)
        )

    output1_file = Path(output1_path)
    output2_file = Path(output2_path) if paired else None

    total_reads = 0
    filtered_reads = 0
    input_bases = 0
    output_bases = 0

    try:
        with ExitStack() as stack:
            writer1 = stack.enter_context(
                FastqWriter(output1_file, compress=compress)
            )
            writer2 = (
                stack.enter_context(FastqWriter(output2_file, compress=compress))
                if output2_file is not None
                else None
            )

            if paired:
                sentinel = object()
                pairs = zip_longest(
                    read_fastq(read1_file),
                    read_fastq(read2_file),
                    fillvalue=sentinel,
                )
                for record1, record2 in pairs:
                    if record1 is sentinel or record2 is sentinel:
                        raise ValueError("两份配对 FASTQ 的记录数不一致。")
                    total_reads += 1
                    input_bases += record1.length + record2.length
                    if is_filtered(record1.name, record2.name, settings):
                        filtered_reads += 1
                        continue
                    output_bases += record1.length + record2.length
                    writer1.write(record1)
                    writer2.write(record2)
            else:
                for record in read_fastq(read1_file):
                    total_reads += 1
                    input_bases += record.length
                    if is_filtered(record.name, None, settings):
                        filtered_reads += 1
                        continue
                    output_bases += record.length
                    writer1.write(record)
    except Exception:
        # writer 已随 ExitStack 退出作用域（文件句柄不再被占用），此处才删得掉。
        output1_file.unlink(missing_ok=True)
        if output2_file is not None:
            output2_file.unlink(missing_ok=True)
        raise

    return IndexFilterSummary(
        total_reads=total_reads,
        filtered_reads=filtered_reads,
        input_bases=input_bases,
        output_bases=output_bases,
        paired=paired,
        enabled=settings.enabled,
    )
