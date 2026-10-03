"""文件级接口：把 polyG / polyX 修剪跑在一整个 FASTQ 文件上。

算法层（``algorithm.py``）处理单条 read，公共层
(:mod:`modules.bio_analysis_function.common.fastq`) 负责 FASTQ 流式读写
与"读 → 逐条变换 → 写"的管道。本文件只做两者的粘合。

注意 poly 修剪**只改序列、不改质量**，因此裁剪后质量串要跟着序列同步截短。
"""

from __future__ import annotations

from pathlib import Path

from ...common.fastq import FastqRecord, FastqStreamSummary, process_fastq
from .algorithm import PolyTrimConfig, trim_poly_tails


def trim_poly_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    config: PolyTrimConfig | None = None,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """对一整个 FASTQ 文件做 polyG / polyX 尾部修剪，并写出结果文件。

    处理是**流式**的：逐条读入、修剪、写出，任何时候内存里只有当前记录。

    参数：
        input_path: 输入 FASTQ 路径，gzip 按魔数自动识别。
        output_path: 输出 FASTQ 路径；父目录不存在会自动创建。
        config: 开关与阈值；``None`` 表示两者都不做（输出与输入内容一致）。
        compress: 输出是否 gzip 压缩。``None``（默认）表示**跟随输入**——
            不看输出文件名的扩展名。

    返回：
        :class:`~modules.bio_analysis_function.common.fastq.FastqStreamSummary`。

    说明：
        没有检测到 poly 尾巴的 read 会**原样写出**，不会计入 ``changed_reads``。
        本算法不会丢弃 read，因此 ``dropped_reads`` 恒为 0。

    异常：
        ``ValueError``：输入文件不存在或其 FASTQ 格式不合法。
    """
    settings = config if config is not None else PolyTrimConfig()

    def transform(record: FastqRecord) -> FastqRecord | None:
        result = trim_poly_tails(record.sequence, config=settings)
        if not result.changed:
            return record
        return FastqRecord(
            name=record.name,
            sequence=result.sequence,
            quality=record.quality[: len(result.sequence)],
        )

    return process_fastq(input_path, output_path, transform, compress=compress)
