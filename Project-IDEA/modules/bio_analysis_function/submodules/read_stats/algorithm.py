"""read_stats：reads 质量统计（fastp 的 ``Stats``）。

**设计目标：与 fastp 1.3.x 的 ``Stats``（``src/stats.cpp``）逐位一致**——
按位置的质量曲线、碱基含量曲线、Q20/Q30/Q40 口径、5-mer 计数，全部照抄。
改这里之前请先读同目录 `read_stats.md` 的设计记录：上游若干处"看起来可以简化"
的写法是刻意的（尤其是那个用 ASCII 低 3 位当桶下标的技巧）。

本文件只做统计累加，不读写文件；文件级接口见 ``runner.py``。

--------------------------------------------------------------------------
它算什么
--------------------------------------------------------------------------

| 维度 | 粒度 | 用途 |
| --- | --- | --- |
| 条数 / 碱基数 / 平均读长 | 全局 | 数据量是否够、剪切是否砍过头 |
| Q20 / Q30 / Q40 碱基比例 | 全局 | 数据整体质量 |
| GC 含量 | 全局 | 物种特征、污染线索 |
| **按位置**的质量均值 | 每个 cycle | 看质量是否随测序推进而衰减 |
| **按位置**的各碱基质量 | 每个 cycle | 看某种碱基是否特别差 |
| **按位置**的各碱基占比 | 每个 cycle | 看组成是否均衡、有无异常富集 |
| 质量值分布 | 全局 | 看质量是"整体偏低"还是"两头分化" |
| 5-mer 计数 | 全局 | 1024 桶频次表，序列内容异常的线索 |
| 读长分布 | 全局 | 平均读长会掩盖"一半很长一半很短" |

"按位置"的那三项是整个报告的核心：**只看全局平均质量，看不出质量是从第几个
循环开始塌的**，而这决定了要不要做质量剪切、从哪一端切。

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------

本算法**只读不写**：不产出任何 FASTQ，只给一份统计。它与
:mod:`quality_trimming` 是"看"与"改"的关系——先看报告再决定怎么切。

它**不重复别人已经算过的东西**：重复率归 :mod:`deduplication`，
接头归 :mod:`common.adapter_detection`，过滤结果归 :mod:`read_filtering`。
本模块只管"碱基与质量本身长什么样"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Iterable

# ---------------------------------------------------------------------------
# 口径常量（数值与上游一致，不要改）
# ---------------------------------------------------------------------------

#: 5-mer。上游 ``#define KMER_LEN 5``。
KMER_LENGTH: Final = 5

#: k-mer 的桶数：4^5 = 1024。（上游按 ``2 << 10`` 分配了 2048 个槽，
#: 但实际用到的下标永远小于 1024——那是它的分配习惯，不是口径。）
KMER_BUCKETS: Final = 1 << (KMER_LENGTH * 2)

#: Q20 与 Q30 的判定阈值，以 **Phred+33 的字符码**表示。
#: 上游在 ``statRead`` 里写死成 ``'5'`` 与 ``'?'``，不给用户改——照抄。
Q20_CHAR_CODE: Final = ord("5")  # 53 → Q20
Q30_CHAR_CODE: Final = ord("?")  # 63 → Q30

#: Q40 的 Phred 值。上游统计 Q40 总计时的循环是 ``for(c=40; c<94; c++)``。
Q40_PHRED: Final = 40

#: Phred 的最大值（Phred+33 的字符上限是 '~'(126)）。
MAX_PHRED: Final = 93

#: 按位置统计时，用"碱基字符的低 3 位"分桶（上游技巧）。
#:
#: ``'A' & 7 = 1``、``'C' & 7 = 3``、``'T' & 7 = 4``、``'N' & 7 = 6``、
#: ``'G' & 7 = 7``。因此 8 个桶里只有 1/3/4/6/7 会被用到，0/2/5 永远是空的。
#: 这么写是为了省一次查表，本实现照抄——因为**分桶结果会进入曲线数组**，
#: 换个分法曲线就对不上了。
BASE_BUCKET_COUNT: Final = 8

#: 曲线与报告里按这个顺序列出碱基（上游 ``alphabets[5]``）。
CURVE_BASES: Final[tuple[str, ...]] = ("A", "T", "C", "G", "N")

#: 单碱基 → 2 bit 编码，与上游 ``BASE2VAL`` 一致：A=0、T=1、C=2、G=3，其余 -1。
#: **只认大写**——上游对 ``acgt`` 也返回 -1，因此小写碱基不计入 k-mer
#: （但会计入按位置的含量曲线，见 :func:`base_bucket`）。这是上游的不一致，照抄。
_BASE_ENCODING: Final[dict[int, int]] = {
    ord("A"): 0,
    ord("T"): 1,
    ord("C"): 2,
    ord("G"): 3,
}

#: 256 项查表值；``255`` 是"不是合法碱基"的哨兵（:func:`base_code` 会转成 -1）。
BASE_CODES: Final[bytes] = bytes(
    _BASE_ENCODING.get(code, 255) for code in range(256)
)

#: 位置数组的初始富余量。上游 ``bufferMargin`` 默认 1024——读长比预估长时
#: 不必立刻扩容。本实现按需扩容，保留这个数只为与上游的扩容行为对齐心智模型。
CYCLE_MARGIN: Final = 1024

_PHRED_OFFSET: Final = 33


def base_bucket(code: int) -> int:
    """碱基字符码 → 分桶下标（= 低 3 位）。"""
    return code & 0x07


def base_code(code: int) -> int:
    """碱基字符码 → 2 bit 编码；不是 ACGT 时返回 -1。"""
    value = BASE_CODES[code]
    return -1 if value == 255 else value


def kmer_name(index: int) -> str:
    """k-mer 下标 → 它的碱基串（用于把 1024 个桶映射成可读的 5-mer）。"""
    if not 0 <= index < KMER_BUCKETS:
        raise ValueError(f"k-mer 下标必须在 0 到 {KMER_BUCKETS - 1} 之间，当前为 {index}。")
    letters = []
    for shift in range(2 * (KMER_LENGTH - 1), -1, -2):
        letters.append("ATCG"[(index >> shift) & 0x03])
    return "".join(letters)


# ---------------------------------------------------------------------------
# 累加器
# ---------------------------------------------------------------------------


class ReadStatsCollector:
    """逐条累加质量与碱基统计。

    用法::

        collector = ReadStatsCollector()
        collector.add(sequence, quality)     # 逐条喂入
        summary = collector.summarize()

    ``add`` 只做累加（O(读长)），``summarize`` 才把原始计数整成曲线与比例。
    分开的理由是：一批数据只需要汇总一次，但 curve 的计算比累加贵得多。
    """

    def __init__(self) -> None:
        self._capacity = 0
        # 8 个桶各持一条与 capacity 等长的数组。桶一开就建好（而不是等第一条
        # read 到了再建），这样"一条数据都没喂过"时汇总也能正常跑。
        self._q20_bases: list[list[int]] = [[] for _ in range(BASE_BUCKET_COUNT)]
        self._q30_bases: list[list[int]] = [[] for _ in range(BASE_BUCKET_COUNT)]
        self._base_contents: list[list[int]] = [[] for _ in range(BASE_BUCKET_COUNT)]
        self._base_quality: list[list[int]] = [[] for _ in range(BASE_BUCKET_COUNT)]
        self._total_base: list[int] = []
        self._total_quality: list[int] = []
        #: 质量**字符码**（0~127）→ 个数。上游就是按字符码下标的。
        self._quality_histogram: list[int] = [0] * 128
        self._kmer_counts: list[int] = [0] * KMER_BUCKETS

        self.total_reads = 0
        self.total_bases = 0
        self.length_sum = 0
        self.length_counts: dict[int, int] = {}

    # ------------------------------------------------------------------
    # 累加
    # ------------------------------------------------------------------

    def _ensure_capacity(self, length: int) -> None:
        """保证位置数组容得下 ``length`` 个 cycle。"""
        if length <= self._capacity:
            return
        # 与上游一致：一次多给一截，避免长读长数据里反复扩容。
        new_capacity = max(length + 100, int(length * 1.5))
        extra = new_capacity - self._capacity
        for array in (
            self._q20_bases,
            self._q30_bases,
            self._base_contents,
            self._base_quality,
        ):
            for bucket in array:
                bucket.extend([0] * extra)
        self._total_base.extend([0] * extra)
        self._total_quality.extend([0] * extra)
        self._capacity = new_capacity

    def add(self, sequence: bytes, quality: bytes) -> None:
        """累加一条 read。要求序列与质量等长（调用方保证）。"""
        length = len(sequence)
        if length != len(quality):
            raise ValueError(
                f"序列长度（{length}）与质量长度（{len(quality)}）不一致。"
            )
        self._ensure_capacity(length)

        q20 = self._q20_bases
        q30 = self._q30_bases
        contents = self._base_contents
        qualities = self._base_quality
        total_base = self._total_base
        total_quality = self._total_quality
        histogram = self._quality_histogram
        kmers = self._kmer_counts

        kmer = 0
        need_full_compute = True
        for index in range(length):
            base = sequence[index]
            quality_code = quality[index]
            bucket = base & 0x07

            histogram[quality_code] += 1

            if quality_code >= Q30_CHAR_CODE:
                q30[bucket][index] += 1
                q20[bucket][index] += 1
            elif quality_code >= Q20_CHAR_CODE:
                q20[bucket][index] += 1

            contents[bucket][index] += 1
            qualities[bucket][index] += quality_code - _PHRED_OFFSET
            total_base[index] += 1
            total_quality[index] += quality_code - _PHRED_OFFSET

            # 下面这段 k-mer 增量更新是整份代码里最绕的一处，逐行照抄上游：
            # 先看是不是 N，再看够不够 5 个碱基，最后决定"接着上一次的结果
            # 平移一格"还是"从头重算 5 个"。
            if base == ord("N"):
                need_full_compute = True
                continue
            if index < KMER_LENGTH - 1:
                continue

            if not need_full_compute:
                value = base_code(base)
                if value < 0:
                    need_full_compute = True
                    continue
                kmer = ((kmer << 2) & 0x3FC) | value
                kmers[kmer] += 1
            else:
                valid = True
                kmer = 0
                for offset in range(KMER_LENGTH):
                    value = base_code(sequence[index - KMER_LENGTH + 1 + offset])
                    if value < 0:
                        valid = False
                        break
                    kmer = ((kmer << 2) & 0x3FC) | value
                if not valid:
                    need_full_compute = True
                    continue
                kmers[kmer] += 1
                need_full_compute = False

        self.total_reads += 1
        self.length_sum += length
        self.length_counts[length] = self.length_counts.get(length, 0) + 1

    def add_all(self, records: Iterable[tuple[bytes, bytes]]) -> None:
        """批量累加 ``(序列, 质量)`` 序列。"""
        for sequence, quality in records:
            self.add(sequence, quality)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def summarize(self) -> "ReadStatsSummary":
        """把原始计数整成比例与曲线。可重复调用（结果只依赖已喂入的数据）。"""
        cycles = self._resolve_cycles()
        bases = sum(self._total_base[:cycles])

        q20_per_base = [0] * BASE_BUCKET_COUNT
        q30_per_base = [0] * BASE_BUCKET_COUNT
        contents_per_base = [0] * BASE_BUCKET_COUNT
        for bucket in range(BASE_BUCKET_COUNT):
            q20_per_base[bucket] = sum(self._q20_bases[bucket][:cycles])
            q30_per_base[bucket] = sum(self._q30_bases[bucket][:cycles])
            contents_per_base[bucket] = sum(self._base_contents[bucket][:cycles])
        q20_total = sum(q20_per_base)
        q30_total = sum(q30_per_base)

        # Q40：上游直接从字符码直方图里把 Q40~Q93 加起来。
        q40_total = sum(
            self._quality_histogram[phred + _PHRED_OFFSET]
            for phred in range(Q40_PHRED, MAX_PHRED + 1)
        )

        mean_curve = [
            (
                self._total_quality[cycle] / self._total_base[cycle]
                if self._total_base[cycle]
                else 0.0
            )
            for cycle in range(cycles)
        ]

        quality_curves: dict[str, tuple[float, ...]] = {"mean": tuple(mean_curve)}
        content_curves: dict[str, tuple[float, ...]] = {}
        for base in CURVE_BASES:
            bucket = ord(base) & 0x07
            quality_curve = []
            content_curve = []
            for cycle in range(cycles):
                count = self._base_contents[bucket][cycle]
                # 该位置没有这个碱基时（如 N 很少见），上游取整体均值兜底，
                # 而不是记 0——否则曲线会在没有数据的位置掉到底。
                quality_curve.append(
                    self._base_quality[bucket][cycle] / count
                    if count
                    else mean_curve[cycle]
                )
                total = self._total_base[cycle]
                content_curve.append(count / total if total else 0.0)
            quality_curves[base] = tuple(quality_curve)
            content_curves[base] = tuple(content_curve)

        g_bucket = ord("G") & 0x07
        c_bucket = ord("C") & 0x07
        content_curves["GC"] = tuple(
            (
                (self._base_contents[g_bucket][cycle] + self._base_contents[c_bucket][cycle])
                / self._total_base[cycle]
                if self._total_base[cycle]
                else 0.0
            )
            for cycle in range(cycles)
        )

        gc_bases = contents_per_base[g_bucket] + contents_per_base[c_bucket]

        return ReadStatsSummary(
            total_reads=self.total_reads,
            total_bases=bases,
            q20_bases=q20_total,
            q30_bases=q30_total,
            q40_bases=q40_total,
            gc_bases=gc_bases,
            mean_length=self.length_sum // self.total_reads
            if self.total_reads
            else 0,
            cycles=cycles,
            quality_curves=quality_curves,
            content_curves=content_curves,
            quality_histogram={
                phred: self._quality_histogram[phred + _PHRED_OFFSET]
                for phred in range(MAX_PHRED + 1)
                if self._quality_histogram[phred + _PHRED_OFFSET]
            },
            kmer_counts=tuple(self._kmer_counts),
            length_counts=dict(sorted(self.length_counts.items())),
        )

    def _resolve_cycles(self) -> int:
        """cycle 数 = 第一个"没有数据"的位置。

        上游是在累加总碱基数时顺带 break 出来的：读长不一致的数据里，
        短 read 结束的位置仍有别的 read 在贡献碱基，所以它找的是
        **所有 read 都结束之后**的第一个位置。
        """
        for cycle in range(self._capacity):
            if self._total_base[cycle] == 0:
                return cycle
        return self._capacity


# ---------------------------------------------------------------------------
# 汇总结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadStatsSummary:
    """一份 FASTQ 的质量统计。

    曲线都是**按 cycle（位置，从 1 起数）**的序列，长度 = ``cycles``。
    比例类字段（``q20_rate`` 等）是属性，不占存储。

    ``quality_histogram`` 只保留非零项，键是 Phred 值而不是字符码——
    上游内部按字符码存，但对外报字符码没有意义。
    """

    total_reads: int
    total_bases: int
    q20_bases: int
    q30_bases: int
    q40_bases: int
    gc_bases: int
    mean_length: int
    cycles: int
    #: 键为 ``"mean"`` 与 ``"A"/"T"/"C"/"G"/"N"``。
    quality_curves: dict[str, tuple[float, ...]] = field(default_factory=dict)
    #: 键为 ``"A"/"T"/"C"/"G"/"N"/"GC"``。
    content_curves: dict[str, tuple[float, ...]] = field(default_factory=dict)
    #: Phred 值 → 该质量的碱基个数（只含非零项）。
    quality_histogram: dict[int, int] = field(default_factory=dict)
    #: 1024 个 5-mer 桶的计数，下标即编码（A=0、T=1、C=2、G=3，高位在前）。
    kmer_counts: tuple[int, ...] = ()
    #: 读长 → 条数。上游不输出这一项，是本实现新增（平均读长会掩盖分布）。
    length_counts: dict[int, int] = field(default_factory=dict)

    @property
    def q20_rate(self) -> float:
        """Q20 及以上碱基的比例。"""
        return self.q20_bases / self.total_bases if self.total_bases else 0.0

    @property
    def q30_rate(self) -> float:
        """Q30 及以上碱基的比例。"""
        return self.q30_bases / self.total_bases if self.total_bases else 0.0

    @property
    def q40_rate(self) -> float:
        """Q40 及以上碱基的比例。"""
        return self.q40_bases / self.total_bases if self.total_bases else 0.0

    @property
    def gc_content(self) -> float:
        """GC 碱基比例。"""
        return self.gc_bases / self.total_bases if self.total_bases else 0.0
