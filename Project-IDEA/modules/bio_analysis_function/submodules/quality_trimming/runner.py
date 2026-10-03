"""文件级接口：把滑窗质量剪切跑在一整个 FASTQ 文件上。

算法层（``algorithm.py``）处理单条 read，公共层
(:mod:`modules.bio_analysis_function.common.fastq`) 负责 FASTQ 流式读写
与"读 → 逐条变换 → 写"的管道。本文件只做两者的粘合：
把配置包装成一个逐条变换函数交给公共管道。

调用方不必关心分块、压缩、统计或失败清理，这些都在公共层完成。
"""

from __future__ import annotations

from pathlib import Path

from ...common.fastq import FastqRecord, FastqStreamSummary, process_fastq
from .algorithm import QualityCutConfig, trim_and_cut


def trim_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    front: int = 0,
    tail: int = 0,
    config: QualityCutConfig | None = None,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """对一整个 FASTQ 文件做滑窗质量剪切，并写出结果文件。

    处理是**流式**的：逐条读入、剪切、写出，任何时候内存里只有当前记录，
    因此可以直接跑几十 GB 的原始测序文件。

    参数：
        input_path: 输入 FASTQ 路径，gzip 按魔数自动识别。
        output_path: 输出 FASTQ 路径；父目录不存在会自动创建。
        front: 从头部固定切掉的碱基数。
        tail: 从尾部固定切掉的碱基数。
        config: 质量剪切参数；为 ``None`` 时不做质量剪切，只做固定修剪。
        compress: 输出是否 gzip 压缩。``None``（默认）表示**跟随输入**——
            输入是 gzip 就压缩输出，否则写纯文本；不看输出文件名的扩展名。

    返回：
        :class:`~modules.bio_analysis_function.common.fastq.FastqStreamSummary`，
        包含 read 数、碱基数与剪切比例。

    异常：
        ``ValueError``：输入文件不存在或其 FASTQ 格式不合法。
    """

    def transform(record: FastqRecord) -> FastqRecord | None:
        result = trim_and_cut(
            record.sequence,
            record.quality,
            front=front,
            tail=tail,
            config=config,
        )
        if result is None:
            # 剪切后长度不合法，这条 read 不再写出。
            return None
        return FastqRecord(
            name=record.name,
            sequence=result.sequence,
            quality=result.quality,
        )

    return process_fastq(input_path, output_path, transform, compress=compress)
