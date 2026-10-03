"""文件级接口：把规范化跑在一份（或一对）FASTQ 上。

薄薄一层——流式读入、逐条转换、写出。复用公共层的
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
from .algorithm import (
    NormalizeConfig,
    detect_phred_offset,
    normalize_record,
)


@dataclass(frozen=True, slots=True)
class NormalizeSummary:
    """一次规范化的统计。

    恒等式 ``total_reads = renamed_reads + 没改名的``，而 ``requantified_reads``
    在 ``input_phred=64`` 时恒等于 ``total_reads``；单端时两个计数都按 **read 条数**，
    双端时按 **read 条数**（一对算两条，与 UMI 提取同口径）。
    """

    total_reads: int
    renamed_reads: int
    requantified_reads: int
    input_bases: int
    output_bases: int
    paired: bool = False

    def render(self) -> str:
        unit = "read 条" if not self.paired else "read 条（双端按条计）"
        lines = [
            f"输入 {unit}：{self.total_reads}",
            f"改过名字：{self.renamed_reads}",
            f"重编码质量：{self.requantified_reads}",
            f"碱基数：{self.input_bases} → {self.output_bases}（规范化不改长度）",
        ]
        return "\n".join(lines)


def normalize_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    config: NormalizeConfig | None = None,
    compress: bool | None = None,
) -> NormalizeSummary:
    """流式读取（一对）FASTQ，规范化后写出。

    参数：
        read1_path / output1_path: 输入与输出的 R1。
        read2_path / output2_path: 双端时给出，单端留空。两者必须同给或同留空。
        config: 规范化参数；``None`` 表示默认（Phred+33、不修名字）——
            也就是**什么都不改**，只把数据抄一遍。
        compress: 输出是否 gzip。``None``（默认）表示跟随输入。

    返回：
        :class:`NormalizeSummary`。

    说明：
        **不丢弃任何 read、不改碱基**，只动质量字符（可选）与名字（可选）。
        中途失败时**所有半成品都会被删除**。

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
            raise ValueError("双端规范化需要同时给出两份输出路径。")
    elif output2_path is not None:
        raise ValueError("没有给出 R2 输入，却给了第二份输出路径。")

    read2_file = Path(read2_path) if paired else None
    if read2_file is not None and not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")

    settings = config or NormalizeConfig()
    if compress is None:
        compress = is_gzip(read1_file) or (
            read2_file is not None and is_gzip(read2_file)
        )

    output1_file = Path(output1_path)
    output2_file = Path(output2_path) if paired else None

    total_reads = 0
    renamed_reads = 0
    requantified_reads = 0
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
                    outcome1 = normalize_record(record1, settings)
                    outcome2 = normalize_record(record2, settings)
                    total_reads += 2
                    input_bases += record1.length + record2.length
                    output_bases += outcome1.record.length + outcome2.record.length
                    renamed_reads += int(outcome1.renamed) + int(outcome2.renamed)
                    requantified_reads += int(outcome1.requantified) + int(
                        outcome2.requantified
                    )
                    writer1.write(outcome1.record)
                    writer2.write(outcome2.record)
            else:
                for record in read_fastq(read1_file):
                    outcome = normalize_record(record, settings)
                    total_reads += 1
                    input_bases += record.length
                    output_bases += outcome.record.length
                    renamed_reads += int(outcome.renamed)
                    requantified_reads += int(outcome.requantified)
                    writer1.write(outcome.record)
    except Exception:
        # writer 已随 ExitStack 退出作用域（文件句柄不再被占用），此处才删得掉。
        output1_file.unlink(missing_ok=True)
        if output2_file is not None:
            output2_file.unlink(missing_ok=True)
        raise

    return NormalizeSummary(
        total_reads=total_reads,
        renamed_reads=renamed_reads,
        requantified_reads=requantified_reads,
        input_bases=input_bases,
        output_bases=output_bases,
        paired=paired,
    )


def detect_quality_offset(input_path: str | Path, *, limit: int = 1000) -> int | None:
    """看一份 FASTQ 的质量编码是不是**能确定**为 Phred+33。

    只看前 ``limit`` 条（默认 1000）就够——只要出现一个 ASCII < 64 的字符，
    整份数据就只能是 Phred+33。

    返回 ``33``（确定）或 ``None``（看不出来，两种编码都说得通）。
    **不会返回 64**：没有可靠依据能断定"一定是 Phred+64"。
    """
    file_path = Path(input_path)
    if not file_path.exists():
        raise ValueError(f"文件不存在：{file_path}")
    return detect_phred_offset(
        record.quality for record in read_fastq(file_path, limit=limit)
    )
