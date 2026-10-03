"""文件级接口：把质量统计跑在一整个 FASTQ 文件上。

本文件很薄——统计本身是无状态的逐条累加（见 ``algorithm.py``），
文件级这一层只负责"流式读进来、喂给累加器、给出汇总"，外加一个把汇总
序列化成 JSON 的辅助函数。读写复用模块公共层的
:func:`~modules.bio_analysis_function.common.fastq.read_fastq`。

**本算法不产出 FASTQ**，因此没有"失败删半成品"那套处理——它要么给出统计，
要么抛出异常。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ...common.fastq import read_fastq
from .algorithm import (
    CURVE_BASES,
    KMER_BUCKETS,
    ReadStatsCollector,
    ReadStatsSummary,
    kmer_name,
)


def stat_fastq(input_path: str | Path) -> ReadStatsSummary:
    """流式读完一份 FASTQ，返回它的质量统计。

    参数：
        input_path: FASTQ 路径（可 gzip，按魔数识别）。

    返回：
        :class:`ReadStatsSummary`，含全局指标、按位置的质量与含量曲线、
        质量值分布、5-mer 计数与读长分布。

    说明：
        **只读不写**。双端数据请对 R1 与 R2 各调一次——上游也是分开统计
        （两份 read 的读长与质量特征本来就该分开看），需要合并时由调用方
        把两份的全局量相加。

    异常：
        ``ValueError``：文件不存在，或 FASTQ 格式有问题。
    """
    file_path = Path(input_path)
    if not file_path.exists():
        raise ValueError(f"文件不存在：{file_path}")

    collector = ReadStatsCollector()
    for record in read_fastq(file_path):
        collector.add(record.sequence, record.quality)
    return collector.summarize()


def report_to_json(summary: ReadStatsSummary, *, indent: int | None = None) -> str:
    """把统计汇总序列化成 JSON 文本（不写文件）。

    **为什么不自带文件输出**：这份 JSON 的消费者是前端（要画曲线）与用户
    （要存档），两者对"写到哪里、叫什么名字"各有主张，塞进算法里只会打架。
    本函数给出**可复现的文本**，落盘交给调用方。

    两个可选的口径在这里定下来：

    - ``kmer_counts`` 是 1024 个数，直接给出长度不便于人读，因此除了数组
      还给一份 ``kmer_top``（计数最高的若干个 5-mer 及其碱基串）；
    - `quality_histogram` 的键是 Phred 值（JSON 里会成为字符串键），
      只含非零项。

    ``indent`` 传给 :func:`json.dumps`：``None`` 压缩成一行，``2`` 适合人看。
    """
    payload = asdict(summary)
    payload["kmer_top"] = _top_kmers(summary)
    payload["curve_bases"] = list(CURVE_BASES)
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def _top_kmers(summary: ReadStatsSummary, limit: int = 20) -> list[dict[str, object]]:
    """计数最高的若干个 5-mer（按计数降序、同计数按编码升序）。"""
    counts = summary.kmer_counts
    if not counts:
        return []
    ranked = sorted(
        range(min(len(counts), KMER_BUCKETS)),
        key=lambda index: (-counts[index], index),
    )
    return [
        {"kmer": kmer_name(index), "count": counts[index]}
        for index in ranked[:limit]
        if counts[index] > 0
    ]


def render_report(summary: ReadStatsSummary) -> str:
    """把统计汇总排成人类可读的多行文本。

    只列**全局量**与"最差的位置"这类结论性信息；曲线本身有几百上千个点，
    排成文本没有意义，交给前端画图。
    """
    lines = [
        f"read 条数：{summary.total_reads}",
        f"碱基总数：{summary.total_bases}",
        f"平均读长：{summary.mean_length}；cycle 数：{summary.cycles}",
        f"Q20 及以上：{summary.q20_bases}（{summary.q20_rate:.2%}）",
        f"Q30 及以上：{summary.q30_bases}（{summary.q30_rate:.2%}）",
        f"Q40 及以上：{summary.q40_bases}（{summary.q40_rate:.2%}）",
        f"GC 含量：{summary.gc_content:.2%}",
    ]

    lengths = summary.length_counts
    if lengths:
        shortest = min(lengths)
        longest = max(lengths)
        if shortest == longest:
            lines.append(f"读长：全部为 {shortest}")
        else:
            lines.append(f"读长范围：{shortest} ~ {longest}（共 {len(lengths)} 种）")

    mean_curve = summary.quality_curves.get("mean", ())
    if mean_curve:
        worst_cycle = min(range(len(mean_curve)), key=lambda index: mean_curve[index])
        lines.append(
            f"质量最低的位置：第 {worst_cycle + 1} 个 cycle（Q{mean_curve[worst_cycle]:.1f}）"
        )
    return "\n".join(lines)
