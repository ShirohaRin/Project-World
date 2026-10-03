"""paired_end_base_correction：用重叠区里高质量的碱基改正对侧的低质量碱基。

**设计目标：与 fastp 1.3.x 的 ``BaseCorrector::correctByOverlapAnalysis``
（``src/basecorrector.cpp``）逐位一致**。改这里之前请先读同目录的
`paired_end_base_correction.md`。

它要解决的问题是**测序错误的纠正**：两条 read 覆盖同一段片段时，同一位点被测了两遍。
如果一边是高质量碱基、另一边是低质量碱基，而两者又不一致，那多半是低质量那边读错了——
把低质量碱基改成对侧的互补，并把对侧的质量值一并赋过去（上游的注释：让它们共享同一个
质量）。

三个刻意的边界（都来自上游，不要"顺手优化"）：

1. **只处理重叠区，且只在真有错配时才动手**：``overlap.diff == 0`` 或重叠都没检出，
   直接原样返回，一个字节都不改。
2. **两个质量门槛写死**：可信 ≥ Q30、不可信 ≤ Q14（上游是常量，不是命令行参数）。
   两边都可信、或两边都不可信时**不修正**——那种情况下谁也说不清哪边对。
3. **带缺口的重叠直接跳过**：上游在调用点明确写了 "no gap allowed for overlap
   correction"——带缺口的重叠里"哪一位对应哪一位"本身就不可靠。默认参数下 overlap
   走的是无缺口路径，因此这一条不会被触发；只有显式打开 ``allow_gap`` 才可能遇到。

本文件只做一对 read 的校正，不读写文件；文件级接口见 ``runner.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...common.fastq import FastqRecord
from ...common.paired_overlap import OverlapConfig, analyze_overlap
from ...common.sequences import complement

#: Phred 质量与字符之间的固定偏移。上游用 ``num2qual(n) = n + 33``。
_PHRED_OFFSET: Final = 33

#: 上游写死的两个质量门槛（``BaseCorrector`` 里的局部常量）。
#: 达到 ``GOOD`` 才算"可信"，低到 ``BAD`` 才算"不可信"；中间那一段不参与修正。
_GOOD_QUALITY_BYTE: Final = 30 + _PHRED_OFFSET
_BAD_QUALITY_BYTE: Final = 14 + _PHRED_OFFSET


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    """一对 read 的校正结果。

    ``read1`` / ``read2`` 是校正之后的记录（没改动时就是原来的对象）。
    ``corrected`` 是被改正的碱基数；``corrected_read1`` / ``corrected_read2``
    说明哪一条被动过——统计里"校正过的 read 条数"按这两个标志算：
    两条都动过记 2，只动过一条记 1（上游就是这么计的）。
    """

    read1: FastqRecord
    read2: FastqRecord
    corrected: int = 0
    corrected_read1: bool = False
    corrected_read2: bool = False

    @property
    def corrected_reads(self) -> int:
        """被改动过的 read 条数（0、1 或 2）。"""
        return int(self.corrected_read1) + int(self.corrected_read2)


def correct_pair_by_overlap(
    read1: FastqRecord,
    read2: FastqRecord,
    config: OverlapConfig | None = None,
) -> CorrectionResult:
    """用重叠区的高质量碱基修正对侧的低质量碱基。

    参数：
        read1 / read2: 一对 read（原始方向；函数内部自己取 R2 的反向互补）。
        config: overlap 参数；``None`` 表示默认（与 fastp 一致）。
            质量门槛不在这里——它们是上游写死的常量。

    返回：
        :class:`CorrectionResult`。**没有可修正的位点时也照常返回**
        （``corrected`` 为 0），不是错误。

    说明：
        重叠区第 ``i`` 位的两个对应位置是::

            p1 = max(0, offset) + i
            p2 = len(R2) - max(0, -offset) - 1 - i

        ``p2`` 是**递减**的——R2 与 R1 反向配对，这是它读同一段片段的另一条链。
        ``offset < 0``（两端读穿接头）时两个起点都往中间收，因此这个循环对
        三种几何（``offset`` 为正 / 为零 / 为负）都能给出正确的位置对应。
    """
    if len(read1.sequence) != len(read1.quality):
        raise ValueError("read1 的序列长度与质量长度不一致。")
    if len(read2.sequence) != len(read2.quality):
        raise ValueError("read2 的序列长度与质量长度不一致。")

    overlap = analyze_overlap(read1.sequence, read2.sequence, config)
    # 上游的短路：没有错配、或压根没重叠，就一个字节都不改。
    if not overlap.overlapped or overlap.diff == 0:
        return CorrectionResult(read1=read1, read2=read2)
    # 带缺口的重叠里"哪一位对应哪一位"本身就不可靠，上游在调用点明确跳过
    # （"no gap allowed for overlap correction"）。默认参数下 has_gap 恒为假。
    if overlap.has_gap:
        return CorrectionResult(read1=read1, read2=read2)

    sequence1 = bytearray(read1.sequence)
    quality1 = bytearray(read1.quality)
    sequence2 = bytearray(read2.sequence)
    quality2 = bytearray(read2.quality)

    start1 = max(0, overlap.offset)
    start2 = len(read2.sequence) - max(0, -overlap.offset) - 1

    corrected = 0
    corrected_read1 = False
    corrected_read2 = False
    for index in range(overlap.overlap_len):
        position1 = start1 + index
        position2 = start2 - index

        if sequence1[position1] == complement(sequence2[position2]):
            continue  # 这一位两边一致，不用管

        quality_at_1 = quality1[position1]
        quality_at_2 = quality2[position2]
        if quality_at_1 >= _GOOD_QUALITY_BYTE and quality_at_2 <= _BAD_QUALITY_BYTE:
            # R1 可信、R2 不可信 → 用 R1 改 R2，并把 R1 的质量值赋给 R2。
            sequence2[position2] = complement(sequence1[position1])
            quality2[position2] = quality_at_1
            corrected += 1
            corrected_read2 = True
        elif quality_at_2 >= _GOOD_QUALITY_BYTE and quality_at_1 <= _BAD_QUALITY_BYTE:
            # 反过来：R2 可信、R1 不可信 → 用 R2 改 R1。
            sequence1[position1] = complement(sequence2[position2])
            quality1[position1] = quality_at_2
            corrected += 1
            corrected_read1 = True
        # 其余情况（两边都可信 / 两边都不可信）不修正：说不清哪边对。

    if corrected == 0:
        return CorrectionResult(read1=read1, read2=read2)

    return CorrectionResult(
        read1=FastqRecord(read1.name, bytes(sequence1), bytes(quality1)),
        read2=FastqRecord(read2.name, bytes(sequence2), bytes(quality2)),
        corrected=corrected,
        corrected_read1=corrected_read1,
        corrected_read2=corrected_read2,
    )


#: 上游 ``BaseCorrector::test()`` 的自带向量：输入、参数与期望输出都是写死的。
#: 这是本算法最强的验证证据——不是"看起来对"，而是可以逐位比对。
#:
#: 两条 read 各带一个低质量碱基（``/`` = Q14）：
#: ``quality1`` 的低质量位在索引 50、``quality2`` 的在索引 42，两处都正对着一个错配，
#: 因此两个方向各被修正一次（``corrected == 2``），修完之后质量全变成 ``E``（Q36）。
UPSTREAM_TEST_VECTOR: Final = {
    "read1": b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCACGGGG",
    "quality1": b"E" * 50 + b"/" + b"E" * 5,
    "read2": b"AAAAAAAAAACCCCGGGGAAAATTTTAAAATTGGGGGGGGGGTGGGGGGGGGGGGG",
    "quality2": b"E" * 42 + b"/" + b"E" * 13,
    "diff_limit": 5,
    "require": 30,
    "diff_percent_limit": 0.2,
    "expect_read1": b"TTTTAACCCCCCCCCCCCCCCCCCCCCCCCCCCCAATTTTAAAATTTTCCCCGGGG",
    "expect_quality1": b"E" * 56,
    "expect_read2": b"AAAAAAAAAACCCCGGGGAAAATTTTAAAATTGGGGGGGGGGGGGGGGGGGGGGGG",
    "expect_quality2": b"E" * 56,
}
