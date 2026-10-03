"""文件级接口：从一份 FASTQ 里采样，检测接头序列。

与其它预处理算法不同，本算法的产物**不是**一个新的 FASTQ 文件，而是一条结论
（检测到的接头序列 + 依据），供下游的接头裁剪步骤使用。因此这里没有输出文件，
只有"读文件、采样、调算法"三件事。

采样策略与上游一致：**从文件开头顺序读**，最多 ``max_reads`` 条或 ``max_bases``
个碱基（谁先到算谁），把采样到的 read 留在内存里——接头检测要多次遍历同一批样本
（先数 k-mer，再围绕种子建两棵树），因此必须能随机访问。内存占用与样本量成正比，
与文件总大小无关。
"""

from __future__ import annotations

from pathlib import Path

from ..fastq import read_fastq
from .algorithm import AdapterDetection, AdapterDetectionConfig, detect_adapter


def sample_sequences(
    fastq_path: str | Path,
    config: AdapterDetectionConfig | None = None,
) -> list[bytes]:
    """从 FASTQ 里顺序采样出 read 序列（只要序列，不要质量与名字）。

    数据够大时，返回的条数与碱基数由 ``config.max_reads`` / ``config.max_bases``
    截断；文件本身更小就全部返回。接头检测只看碱基，不看质量，因此质量串在采样
    阶段就被丢掉了（这一点与上游不同：上游把整条 read 留在内存里，多占一份质量串
    的内存，但不影响任何判定）。
    """
    path = Path(fastq_path)
    if not path.exists():
        raise ValueError(f"文件不存在：{path}")
    config = config or AdapterDetectionConfig()

    sequences: list[bytes] = []
    bases = 0
    for record in read_fastq(path):
        sequences.append(record.sequence)
        bases += record.length
        # 上游在"读下一条之前"检查上限，因此样本可能刚好超过碱基上限一条 read；
        # 这里的判断放在 append 之后，行为一致。
        if len(sequences) >= config.max_reads or bases >= config.max_bases:
            break
    return sequences


def detect_adapter_from_file(
    fastq_path: str | Path,
    *,
    config: AdapterDetectionConfig | None = None,
) -> AdapterDetection:
    """从一份 FASTQ 里检测接头序列。

    参数：
        fastq_path: 输入 FASTQ 路径，gzip 按魔数自动识别（只读，不会写任何文件）。
        config: 检测参数；``None`` 表示默认（与 fastp 一致）。

    返回：
        :class:`AdapterDetection`；``adapter`` 为 ``None`` 表示没检测到。

    异常：
        ``ValueError``：文件不存在或其 FASTQ 格式不合法。

    说明：
        采样是流式的（读够样本就停），但采样到的 read 会留在内存里，
        上限由 ``config.max_reads`` / ``max_bases`` 决定（默认约 26 万条、4000 万碱基）。
    """
    config = config or AdapterDetectionConfig()
    sequences = sample_sequences(fastq_path, config)
    return detect_adapter(sequences, config)
