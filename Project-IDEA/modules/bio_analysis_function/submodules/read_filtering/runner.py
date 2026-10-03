"""文件级接口：把 reads 过滤跑在一整个 FASTQ 文件上。

**为什么没有直接用公共层的 ``process_fastq``**：那条公共管道只统计
"保留 / 丢弃"两个总数，而过滤算法真正要看的是**按原因分类的失败统计**
（多少条低质量、多少条 N 过多……），这正是 fastp 报告里"过滤结果"那一节。
它也只支持**一个输出**，而本算法可以额外把被丢弃的 read 收集到第二个文件。
本文件因此自己驱动一遍流式读写，但读与写仍复用公共层的
:func:`~modules.bio_analysis_function.common.fastq.read_fastq` /
:class:`~modules.bio_analysis_function.common.fastq.FastqWriter`，
以及同一套"失败就删掉半成品"的处理方式。
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from ...common.fastq import FastqRecord, FastqWriter, is_gzip, read_fastq
from .algorithm import (
    FAILURE_LABELS,
    FAILURE_ORDER,
    PASS_FILTER,
    ReadFilterConfig,
    filter_verdict,
    verdict_label,
)


@dataclass(frozen=True, slots=True)
class FilterSummary:
    """一次过滤的统计结果。

    计数口径与模块里其他预处理算法一致：

        total_reads = kept_reads + dropped_reads

    ``failures`` 是"失败原因 → 条数"的明细，只统计非通过的 read，
    因此其值之和等于 ``dropped_reads``。序列本身从不被改写，
    所以这里没有 ``changed_reads``。
    """

    total_reads: int
    kept_reads: int
    dropped_reads: int
    bases_before: int
    bases_after: int
    failures: dict[int, int] = field(default_factory=dict)

    @property
    def kept_rate(self) -> float:
        """通过比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.kept_reads / self.total_reads

    @property
    def bases_removed(self) -> int:
        """未进入输出文件的碱基总数（被丢弃 read 的全部碱基）。"""
        return self.bases_before - self.bases_after

    def reason_counts(self) -> list[tuple[str, int]]:
        """按固定顺序返回 ``[(标签, 条数)]``，只列出现过的原因。"""
        return [
            (FAILURE_LABELS[code], self.failures[code])
            for code in FAILURE_ORDER
            if self.failures.get(code)
        ]

    def render(self) -> str:
        """渲染成便于阅读与日志记录的多行文本。"""
        lines = [
            f"输入 read 数：{self.total_reads}",
            f"通过 read 数：{self.kept_reads}（通过率 {self.kept_rate:.2%}）",
            f"丢弃 read 数：{self.dropped_reads}",
            f"碱基数：{self.bases_before} → {self.bases_after}（去掉 {self.bases_removed}）",
        ]
        for label, count in self.reason_counts():
            lines.append(f"  · {label}：{count}")
        return "\n".join(lines)


def filter_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    failed_output_path: str | Path | None = None,
    config: ReadFilterConfig | None = None,
    compress: bool | None = None,
) -> FilterSummary:
    """对一整个 FASTQ 文件做过滤，只把通过的 read 写到输出文件。

    处理是**流式**的：逐条读入、判定、写出，任何时候内存里只有当前记录，
    因此可以直接跑几十 GB 的原始测序文件。**序列与质量不被改写**——
    本算法只决定每条 read 的去留。

    参数：
        input_path: 输入 FASTQ 路径，gzip 按魔数自动识别。
        output_path: 输出 FASTQ 路径（只含通过的 read）；父目录会自动创建。
        failed_output_path: 可选。给出时，**被丢弃的 read 会原样写进这个文件**，
            并在名字后面追加一个失败原因标签（如 ``failed_too_short``，与上游
            ``FAILED_TYPES`` 同口径）——用于回溯"丢掉了什么、为什么丢"。
            留空则只写通过的那些。
        config: 过滤参数；``None`` 表示使用默认参数（与 fastp 命令行默认一致）。
        compress: 输出是否 gzip 压缩。``None``（默认）表示**跟随输入**——
            输入是 gzip 就压缩输出，否则写纯文本；不看输出文件名的扩展名。
            两个输出用同一个判断结果。

    返回：
        :class:`FilterSummary`，含总数、通过数、按原因分类的失败明细与碱基数。
        ``dropped_reads`` 是**丢弃的条数**；给了 ``failed_output_path`` 时，
        这些 read 会全部出现在那个文件里（条数与它相等）。

    异常：
        ``ValueError``：输入文件不存在或其 FASTQ 格式不合法。
    """
    config = config or ReadFilterConfig()
    input_file = Path(input_path)
    if not input_file.exists():
        raise ValueError(f"文件不存在：{input_file}")
    if compress is None:
        compress = is_gzip(input_file)

    output_file = Path(output_path)
    failed_file = Path(failed_output_path) if failed_output_path is not None else None
    failures: dict[int, int] = {}
    counters = {"total": 0, "kept": 0, "dropped": 0, "bases_before": 0, "bases_after": 0}

    try:
        # 两个输出同时开着：每条 read 只去其中一边，不会两处都写。
        with ExitStack() as stack:
            writer = stack.enter_context(FastqWriter(output_file, compress=compress))
            failed_writer = (
                stack.enter_context(FastqWriter(failed_file, compress=compress))
                if failed_file is not None
                else None
            )

            for record in read_fastq(input_file):
                counters["total"] += 1
                counters["bases_before"] += record.length

                verdict = filter_verdict(record.sequence, record.quality, config)
                if verdict != PASS_FILTER:
                    failures[verdict] = failures.get(verdict, 0) + 1
                    counters["dropped"] += 1
                    if failed_writer is not None:
                        # 序列原样保留，只在名字后追加失败原因（上游就是这么做的）。
                        failed_writer.write(
                            FastqRecord(
                                f"{record.name} {verdict_label(verdict)}",
                                record.sequence,
                                record.quality,
                            )
                        )
                    continue

                counters["kept"] += 1
                counters["bases_after"] += record.length
                writer.write(record)

            written = writer.written
            failed_written = failed_writer.written if failed_writer is not None else 0
    except Exception:
        # 中途失败：两个半成品都删掉（此时 writer 已随 with 退出而关闭，
        # 句柄不再占用——否则 Windows 上删除会静默失败）。
        output_file.unlink(missing_ok=True)
        if failed_file is not None:
            failed_file.unlink(missing_ok=True)
        raise

    # 写出条数与统计必须一致；不一致说明统计与写出脱节，属于内部错误。
    if written != counters["kept"]:
        raise RuntimeError(
            f"内部统计不一致：写出 {written} 条，统计通过 {counters['kept']} 条。"
        )
    if failed_file is not None and failed_written != counters["dropped"]:
        raise RuntimeError(
            f"内部统计不一致：失败输出写出 {failed_written} 条，"
            f"统计丢弃 {counters['dropped']} 条。"
        )

    return FilterSummary(
        total_reads=counters["total"],
        kept_reads=counters["kept"],
        dropped_reads=counters["dropped"],
        bases_before=counters["bases_before"],
        bases_after=counters["bases_after"],
        failures=failures,
    )
