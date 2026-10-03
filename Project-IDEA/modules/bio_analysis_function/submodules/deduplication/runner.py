"""文件级接口：把重复检测跑在一整个（或一对）FASTQ 文件上。

**为什么没有直接用公共层的 ``process_fastq``**：那条公共管道是"逐条独立变换"
的模型，而重复检测的判定**依赖此前处理过的所有 read**（布隆过滤器的比特是
跨 read 累积的）。把这种"有状态、顺序相关"的判定塞进并行流水线，要么结果
随线程调度漂移，要么得把判定挪到唯一线程里——后者正是本实现的做法
（见同目录 `deduplication.md` 的"顺序依赖性"）。

读与写仍复用公共层的 :func:`~modules.bio_analysis_function.common.fastq.read_fastq` /
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
    DEFAULT_ACCURACY_ANALYZE,
    DEFAULT_ACCURACY_DEDUP,
    DuplicateDetector,
)


@dataclass(frozen=True, slots=True)
class DuplicationSummary:
    """一次重复检测的统计结果。

    计数口径与模块其他算法一致：

        total_reads = kept_reads + dropped_reads

    单端时 ``total_reads`` 是 **read 条数**；双端时是 **read 对数**
    （上游同样按"对"计数，因为一对是被一起判重、一起丢弃的），
    此时 ``paired`` 为 ``True``，``render()`` 也会改用"对"的说法。

    两个容易看错的字段：

    - ``dropped_reads`` **只在真正去重时非零**（``dedup`` 为 ``True``）。
      只做评估时它恒为 0——没丢任何东西，``kept_reads`` 就等于总数。
    - ``bases_after`` 是"**保留下来的**碱基数"（输入碱基数减去被判为重复的
      那些 read 的碱基数），与是否真的写出文件无关。去重时它恰好等于写出
      文件的碱基数；只评估时它是一个假想值，用来回答"去重会削掉多少数据"。
      双端时它含 R1 与 R2 两侧。
    """

    total_reads: int
    duplicate_reads: int
    kept_reads: int
    dropped_reads: int
    bases_before: int
    bases_after: int
    paired: bool = False
    dedup: bool = False
    accuracy_level: int = DEFAULT_ACCURACY_ANALYZE

    @property
    def duplicate_rate(self) -> float:
        """重复率。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.duplicate_reads / self.total_reads

    @property
    def kept_rate(self) -> float:
        """保留比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.kept_reads / self.total_reads

    def render(self) -> str:
        unit = "read 对" if self.paired else "read"
        lines = [
            f"输入 {unit}：{self.total_reads}",
            f"重复 {unit}：{self.duplicate_reads}（{self.duplicate_rate:.2%}）",
        ]
        if self.dedup:
            lines.append(f"丢弃 {unit}：{self.dropped_reads}；保留 {self.kept_reads}")
        else:
            lines.append("未去重（只评估重复率）")
        lines.append(f"碱基数：{self.bases_before} → {self.bases_after}")
        lines.append(f"内存档位：{self.accuracy_level}")
        return "\n".join(lines)


def deduplicate_fastq(
    read1_path: str | Path,
    output1_path: str | Path | None = None,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    accuracy_level: int | None = None,
    buffer_bytes: int | None = None,
    compress: bool | None = None,
) -> DuplicationSummary:
    """流式读取（一对）FASTQ，检测重复，可选地丢掉重复的 read。

    参数：
        read1_path: 正向 reads（可 gzip，按魔数识别）。
        output1_path: 去重后的输出路径；**留空表示只评估重复率、不产出文件**
            （对应 fastp 的默认行为），给出则表示真的去重（对应 ``--dedup``）。
        read2_path: 反向 reads。给了就是双端——一对 read 的 R1 与 R2
            **首尾相接**后一起判重，重复时成对丢弃。
        output2_path: 双端时 R2 的输出路径，与 ``output1_path`` 同时给或同时不给。
        accuracy_level: 内存档位 1~6（1 GiB ~ 32 GiB）。``None``（默认）
            按上游取值：只评估取 1，去重取 3——去重判错了要丢数据，
            所以默认多花内存降低假阳性。
        buffer_bytes: 直接指定每个缓冲区的字节数，**覆盖** ``accuracy_level``
            给出的默认值（缓冲区个数仍由档位决定）。默认 ``None`` 表示按档位取值。
            它的用途有两个：小内存机器上手动压低头寸；以及**两侧实现对拍时
            指定同一份配置**——位图大小决定位置向量怎么取模、进而决定假阳性率，
            改小了结论就变，所以对拍必须同值。
        compress: 输出是否 gzip。``None``（默认）表示**跟随输入**；
            只评估时该参数无意义。

    返回：
        :class:`DuplicationSummary`。字段含义（尤其 ``bases_after`` 与
        ``dropped_reads`` 在两种模式下的差别）见该类型的说明。

    说明：
        **判定是顺序相关的**：一条 read 是否被判为重复，取决于它前面出现过
        什么。同一份数据换个顺序跑，重复条数会不同——这是布隆过滤器式判重的
        定义，不是实现的不确定性。本实现把它做成**严格按文件顺序**判定，
        因此同一份输入无论线程怎么调度，结果完全一致。
        中途失败时**所有半成品输出都会被删除**。

    异常：
        ``ValueError``：文件不存在、两份输出的组合不合法、两份配对 FASTQ 的
        记录数不一致。
    """
    read1_file = Path(read1_path)
    if not read1_file.exists():
        raise ValueError(f"文件不存在：{read1_file}")

    paired = read2_path is not None
    if paired:
        if (output1_path is None) != (output2_path is None):
            raise ValueError(
                "双端去重要么同时给出两份输出路径，要么都不给（只做评估）。"
            )
    elif output2_path is not None:
        raise ValueError("没有给出 R2 输入，却给了第二份输出路径。")

    read2_file = Path(read2_path) if paired else None
    if read2_file is not None and not read2_file.exists():
        raise ValueError(f"文件不存在：{read2_file}")

    dedup = output1_path is not None
    if accuracy_level is None:
        accuracy_level = DEFAULT_ACCURACY_DEDUP if dedup else DEFAULT_ACCURACY_ANALYZE

    if compress is None:
        compress = is_gzip(read1_file) or (
            read2_file is not None and is_gzip(read2_file)
        )

    detector = DuplicateDetector(accuracy_level, buffer_bytes=buffer_bytes)
    bases_before = 0
    bases_after = 0
    output1_file = Path(output1_path) if dedup else None
    output2_file = Path(output2_path) if (dedup and paired) else None

    try:
        with ExitStack() as stack:
            writer1 = (
                stack.enter_context(FastqWriter(output1_file, compress=compress))
                if output1_file is not None
                else None
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
                    bases_before += record1.length + record2.length
                    if detector.check_pair(record1.sequence, record2.sequence):
                        continue
                    bases_after += record1.length + record2.length
                    if writer1 is not None and writer2 is not None:
                        writer1.write(record1)
                        writer2.write(record2)
            else:
                for record in read_fastq(read1_file):
                    bases_before += record.length
                    if detector.check_read(record.sequence):
                        continue
                    bases_after += record.length
                    if writer1 is not None:
                        writer1.write(record)
    except Exception:
        # writer 已随 ExitStack 退出作用域（文件句柄不再被占用），此处才删得掉。
        if output1_file is not None:
            output1_file.unlink(missing_ok=True)
        if output2_file is not None:
            output2_file.unlink(missing_ok=True)
        raise

    dropped = detector.duplicate_reads if dedup else 0
    return DuplicationSummary(
        total_reads=detector.total_reads,
        duplicate_reads=detector.duplicate_reads,
        kept_reads=detector.total_reads - dropped,
        dropped_reads=dropped,
        bases_before=bases_before,
        bases_after=bases_after,
        paired=paired,
        dedup=dedup,
        accuracy_level=accuracy_level,
    )
