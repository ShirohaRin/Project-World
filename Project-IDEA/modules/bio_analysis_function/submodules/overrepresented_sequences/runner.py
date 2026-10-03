"""文件级接口：把过表达序列分析跑在一整个 FASTQ 文件上。

与其他算法不同，本算法要**读两遍文件**——这是上游的分工决定的：

1. 第一遍（只读开头一段）**发现**候选序列；
2. 第二遍（全文件、按采样）**量化**这些候选。

两遍的覆盖范围不同是刻意的：发现用不着看完整文件，量化则要覆盖全文件才准。

**本算法不产出任何文件**，只有结论。
"""

from __future__ import annotations

from itertools import chain
from pathlib import Path

from ...common.fastq import read_fastq
from .algorithm import (
    OverrepConfig,
    OverrepSummary,
    OverrepresentedSequence,
    candidate_steps,
    collect_candidate_counts,
    filter_candidates,
    passes_report_threshold,
    remove_substrings,
    scan_sampled_read,
)


def find_overrepresented_sequences(
    input_path: str | Path,
    *,
    config: OverrepConfig | None = None,
) -> OverrepSummary:
    """流式读完一份 FASTQ，报出异常高频的片段。

    参数：
        input_path: FASTQ 路径（可 gzip，按魔数识别）。
        config: 参数；``None`` 表示与 fastp 默认一致（采样 1/100）。

    返回：
        :class:`OverrepSummary`，含检出序列、它们的采样计数、推算总量、
        占碱基比例与位置分布。没有检出时 ``sequences`` 为空——**这是常态**，
        干净的数据本来就不该有过表达序列。

    说明：
        第一步只看文件开头（默认 151 万碱基）找候选；文件比这短时就是全文件。
        因此**同样的序列在文件里换个位置出现，可能只有靠前的那些被选中**——
        这是上游的行为，不是本实现的偏差。

    异常：
        ``ValueError``：文件不存在、参数越界，或 FASTQ 格式有问题。
    """
    file_path = Path(input_path)
    if not file_path.exists():
        raise ValueError(f"文件不存在：{file_path}")

    settings = config or OverrepConfig()

    # ---- 第一步：确定"读长"，并顺便找出候选 ----
    #
    # 上游先单独扫一遍前 1000 条拿读长，再从头扫一遍找候选（打开文件两次）。
    # 这里把前 1000 条**留在内存里**（1000 条 × 150bp 才 150KB），
    # 于是只需要打开一次文件，结果完全一样。
    records = read_fastq(file_path)
    prefix: list[bytes] = []
    for record in records:
        prefix.append(record.sequence)
        if len(prefix) >= settings.seq_length_sample:
            break
    seq_length = max((len(item) for item in prefix), default=0)

    candidates = collect_candidate_counts(
        chain(prefix, records), seq_length, settings.base_limit
    )
    hot = remove_substrings(filter_candidates(candidates, seq_length))

    # ---- 第二步：在全文件上按采样量化这些候选 ----
    steps = candidate_steps(seq_length)
    counts: dict[bytes, int] = {sequence: 0 for sequence in hot}
    distributions: dict[bytes, list[int]] = {
        sequence: [0] * seq_length for sequence in hot
    }

    total_reads = 0
    total_bases = 0
    sampled_reads = 0
    if hot:
        for index, record in enumerate(read_fastq(file_path)):
            total_reads += 1
            total_bases += record.length
            if index % settings.sampling != 0:
                continue
            sampled_reads += 1
            scan_sampled_read(
                record.sequence, counts, distributions, seq_length, steps
            )
    else:
        # 没找到候选就不必再读一遍；但总条数与碱基数仍要如实报出。
        for record in read_fastq(file_path):
            total_reads += 1
            total_bases += record.length

    found = []
    for sequence in sorted(hot):
        count = counts[sequence]
        if not passes_report_threshold(
            len(sequence), count, settings.sampling
        ):
            continue
        estimated = count * settings.sampling
        percent = (
            100.0 * count * len(sequence) * settings.sampling / total_bases
            if total_bases
            else 0.0
        )
        found.append(
            OverrepresentedSequence(
                sequence=sequence.decode("ascii", "replace"),
                count=count,
                length=len(sequence),
                estimated_count=estimated,
                base_percent=percent,
                distribution=tuple(distributions[sequence]),
            )
        )

    # 出现得多的排前面；并列时短的在前（短的在报告里更好读）。
    found.sort(key=lambda item: (-item.count, item.length, item.sequence))
    return OverrepSummary(
        total_reads=total_reads,
        total_bases=total_bases,
        sampled_reads=sampled_reads,
        seq_length=seq_length,
        sampling=settings.sampling,
        sequences=tuple(found),
    )
