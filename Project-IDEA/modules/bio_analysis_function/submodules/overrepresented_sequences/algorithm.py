"""overrepresented_sequences：过表达序列分析（fastp 的 over-representation analysis）。

**与 fastp 1.3.x 逐位对齐**：候选筛选照 ``Evaluator::computeOverRepSeq``，
采样统计与阈值判定照 ``Stats::statRead`` 与 ``Stats::overRepPassed``。

本文件只做纯计数与筛选，不读写文件；文件级接口见 ``runner.py``。

--------------------------------------------------------------------------
它回答什么问题
--------------------------------------------------------------------------
"数据里有没有异常高频的片段。"

正常的测序数据里，任何一段短序列出现的次数都由它的长度决定：10bp 有
$4^{10}$ 种可能，一条 10bp 序列在整个 run 里出现几百次不奇怪；但一条 40bp
的序列出现上千次就**不正常**了——它往往是接头残留、污染、或者 rRNA 之类的
高丰度序列。

因此本算法分两步：

1. **找候选**：只在**文件开头的一段**数据上扫（默认 151 万碱基），把每种
   10 / 20 / 40 / 100 / 150 bp 片段数一遍，超过按长度定的阈值就留作候选；
   再把"是别人子串、且自己出现得并不比别人多多少"的候选剔掉。
2. **统计已知候选**：在**整个文件**上按采样（默认每 100 条取 1 条）数这些
   候选出现了多少次、分别落在 read 的哪些位置。

两步的数据范围不同是**上游的刻意设计**：第一步是"发现"，用开头一段就够；
第二步是"量化"，要覆盖全文件才准。

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------
接头检测（:mod:`common.adapter_detection`）也看 k-mer 富集，但它的目标是
**拼出一条接头序列**；本算法不限接头，凡是异常高频的片段都报，是更一般的
污染线索。两者互补，不互相替代。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Iterable

#: 候选扫描时考察的片段长度。上游写死前四个，第五个是 ``min(150, 读长 - 2)``。
_STEPS: Final[tuple[int, ...]] = (10, 20, 40, 100)

#: 候选扫描的碱基上限（上游写死 ``151 * 10000``）。
#:
#: 只在文件开头这么多碱基上找候选——"发现"不需要看完整个 run。
CANDIDATE_BASE_LIMIT: Final = 151 * 10000

#: 确定"读长"时看前多少条。上游 ``Evaluator::getSeqLen`` 同样取 1000。
SEQ_LENGTH_SAMPLE: Final = 1000

#: 采样率的合法范围（上游校验 1~10000）。
MIN_SAMPLING: Final = 1
MAX_SAMPLING: Final = 10000

#: 候选的入选阈值（按长度分档）。上游 ``computeOverRepSeq`` 里写死。
_CANDIDATE_THRESHOLDS: Final[tuple[tuple[int, int], ...]] = (
    (100, 5),
    (40, 20),
    (20, 100),
    (10, 500),
)

#: 报告时的阈值（按长度精确匹配）。上游 ``Stats::overRepPassed`` 里写死。
_REPORT_THRESHOLDS: Final[dict[int, int]] = {
    10: 500,
    20: 200,
    40: 100,
    100: 50,
}

#: 报告的默认阈值（长度不在上表里时用它）。上游同样如此。
_REPORT_DEFAULT_THRESHOLD: Final = 20


@dataclass(frozen=True, slots=True)
class OverrepConfig:
    """参数。

    | 字段 | 上游 | 说明 |
    | --- | --- | --- |
    | `sampling` | `--overrepresentation_sampling`（默认 **20**） | 第二遍的采样率：每多少条取一条 |
    | `base_limit` | 写死 1510000 | 第一遍（找候选）扫多少碱基 |
    | `seq_length_sample` | 写死 1000 | 看前多少条来确定"读长" |
    """

    sampling: int = 20
    base_limit: int = CANDIDATE_BASE_LIMIT
    seq_length_sample: int = SEQ_LENGTH_SAMPLE

    def __post_init__(self) -> None:
        if not MIN_SAMPLING <= self.sampling <= MAX_SAMPLING:
            raise ValueError(
                f"sampling 必须在 {MIN_SAMPLING} 到 {MAX_SAMPLING} 之间，"
                f"当前为 {self.sampling}。"
            )
        if self.base_limit < 1:
            raise ValueError(f"base_limit 必须为正，当前为 {self.base_limit}。")
        if self.seq_length_sample < 1:
            raise ValueError(
                f"seq_length_sample 必须为正，当前为 {self.seq_length_sample}。"
            )


@dataclass(frozen=True, slots=True)
class OverrepresentedSequence:
    """一条过表达序列。

    ``count`` 是**采样计数**（只数了 1/sampling 的 read），``estimated_count``
    是乘回去的估计总量。看"这条序列有多普遍"用后者，看"数据够不够"用前者。
    """

    sequence: str
    count: int
    length: int
    estimated_count: int
    base_percent: float
    #: 每个位置出现的次数，长度 = 报告里的 ``seq_length``。
    distribution: tuple[int, ...] = ()

    def render(self) -> str:
        preview = self.sequence if len(self.sequence) <= 60 else self.sequence[:60] + "…"
        return (
            f"{preview}（{self.length}bp）：采样计数 {self.count}，"
            f"推算总量 {self.estimated_count}，占碱基 {self.base_percent:.4f}%"
        )


@dataclass(frozen=True, slots=True)
class OverrepSummary:
    """一次过表达序列分析的结果。"""

    total_reads: int
    total_bases: int
    sampled_reads: int
    seq_length: int
    sampling: int
    sequences: tuple[OverrepresentedSequence, ...] = field(default_factory=tuple)

    def render(self) -> str:
        lines = [
            f"read 条数：{self.total_reads}",
            f"碱基总数：{self.total_bases}",
            f"采样：{self.sampled_reads} 条（每 {self.sampling} 条取 1 条）",
            f"检出过表达序列：{len(self.sequences)} 条",
        ]
        for item in self.sequences:
            lines.append("  · " + item.render())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def candidate_steps(seq_length: int) -> tuple[int, ...]:
    """候选扫描要考察的片段长度。

    第五个是 ``min(150, 读长 - 2)``。**它可能与前四个重复**（读长短时），
    上游不去重——同一个长度会被算两遍、计数翻倍。照抄。
    """
    return _STEPS + (min(150, seq_length - 2),)


def collect_candidate_counts(
    sequences: Iterable[bytes], seq_length: int, base_limit: int = CANDIDATE_BASE_LIMIT
) -> dict[bytes, int]:
    """在第一段数据上把各种长度的片段数一遍。

    上游的循环是"读一条、累加碱基、处理这一条"，直到碱基数**达到**上限
    才停——所以让总量越过上限的那一条**也是被处理的**。这里同样。
    """
    counts: dict[bytes, int] = {}
    steps = candidate_steps(seq_length)
    bases = 0
    for sequence in sequences:
        bases += len(sequence)
        length = len(sequence)
        for step in steps:
            if step <= 0 or step > length:
                continue
            for start in range(length - step):
                piece = sequence[start : start + step]
                counts[piece] = counts.get(piece, 0) + 1
        if bases >= base_limit:
            break
    return counts


def filter_candidates(
    counts: dict[bytes, int], seq_length: int
) -> dict[bytes, int]:
    """按长度分档、按阈值筛选候选。

    分档是**从上往下**判的：先看"长度 ≥ 读长 - 1"，再看 ≥ 100、≥ 40、≥ 20、≥ 10。
    所以一条 150bp 的片段走的是第一档（阈值 3），不是 100 那一档。
    """
    hot: dict[bytes, int] = {}
    for sequence, count in counts.items():
        length = len(sequence)
        if length >= seq_length - 1:
            threshold = 3
        else:
            threshold = 0
            for minimum, candidate_threshold in _CANDIDATE_THRESHOLDS:
                if length >= minimum:
                    threshold = candidate_threshold
                    break
            if threshold == 0:
                continue
        if count >= threshold:
            hot[sequence] = count
    return hot


def remove_substrings(hot: dict[bytes, int]) -> dict[bytes, int]:
    """剔掉"是别人子串、而且自己并不比别人多十倍"的候选。

    这条规则的作用是去冗余：如果 ``ACGTACGT`` 出现 1000 次、而它的延长
    ``ACGTACGTACGT`` 也出现 900 次，那么短的这条只是长的那条的一部分，
    单独报出来是噪声。

    **判断用的是整数除法**（``count / other < 10``），与上游一致——
    换成浮点比较，边界附近的取舍会变。
    """
    keys = list(hot)
    dropped: set[bytes] = set()
    for sequence in keys:
        count = hot[sequence]
        for other in keys:
            if other == sequence or sequence not in other:
                continue
            if count // hot[other] < 10:
                dropped.add(sequence)
                break
    return {sequence: count for sequence, count in hot.items() if sequence not in dropped}


def passes_report_threshold(length: int, count: int, sampling: int) -> bool:
    """报告阈值：``sampling × 采样计数`` 要**严格大于**该长度的档位阈值。

    上游 ``Stats::overRepPassed``。注意它与"候选筛选"的阈值**不是同一套**
    （那套在第一步用，判据是 ≥），两套都要照抄。
    """
    scaled = sampling * count
    return scaled > _REPORT_THRESHOLDS.get(length, _REPORT_DEFAULT_THRESHOLD)


def scan_sampled_read(
    sequence: bytes,
    counts: dict[bytes, int],
    distributions: dict[bytes, list[int]],
    evaluated_length: int,
    steps: tuple[int, ...],
) -> None:
    """在一条 read 上统计已知序列的出现次数与位置分布。

    ``counts`` 的键就是"已知序列"的集合（它同时被就地累加）。

    **命中之后要多跳一个片段长度**（上游的 ``i += step``）——否则同一段
    DNA 会被它在不同起点的重叠窗口重复计数。这是"整段出现"而不是
    "位置被覆盖"的口径。
    """
    length = len(sequence)
    for step in steps:
        if step <= 0 or step > length:
            continue
        index = 0
        limit = length - step
        while index < limit:
            piece = sequence[index : index + step]
            if piece in counts:
                counts[piece] += 1
                distribution = distributions[piece]
                for position in range(index, min(index + step, evaluated_length)):
                    distribution[position] += 1
                index += step
            index += 1
