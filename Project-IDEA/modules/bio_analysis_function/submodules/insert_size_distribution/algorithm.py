"""insert_size_distribution：双端插入片段长度分布（fastp 的 insert size histogram）。

**与 fastp 1.3.x 的 ``PairEndProcessor::statInsertSize`` 逐位对齐**：同一个
"片段长度"的定义、同一个上限、同一个 unknown 桶。

本文件只做累加，不读写文件；文件级接口见 ``runner.py``。

--------------------------------------------------------------------------
它回答什么问题
--------------------------------------------------------------------------
"这个文库的插入片段有多大、整齐不整齐。"

- **平均片段长度**只能告诉你中心在哪，看不出分布的胖瘦；
- **峰值**（出现最多的那个长度）是建库是否正常的最直接指标；
- 分布**太宽**说明片段筛选不干净；**多个峰**说明混了两次建库。

片段长度不是测出来的，是**推算**出来的：靠两条 read 的重叠关系倒推。
推导规则完全照抄上游（见 :mod:`common.paired_overlap`）：

| 情况 | 片段长度 |
| --- | --- |
| 两条 read 只在中间重叠（``offset > 0``） | ``len(R1) + len(R2) - 重叠长度`` |
| 两端读穿、读进了接头（``offset <= 0``） | 重叠长度本身就是片段长度 |
| 找不到重叠 | **判不出来**，进 unknown 桶 |

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------
重叠判定本身归 :mod:`common.paired_overlap`（工具层），它已经给出每对的
``insert_size``；本模块只负责**把它们汇成直方图并找出峰值**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...common.paired_overlap import OverlapResult

#: 直方图的上限。上游 ``Options::insertSizeMax`` 默认 512。
#:
#: 下标 0~``max_size - 1`` 是真实的片段长度，下标 ``max_size`` 是**溢出桶**：
#: 既装"片段超过上限"的，也装"压根判不出来"（找不到重叠）的。上游就是这么
#: 用一个桶装两种情况的，照抄——峰值只在上限之内找，所以混在一起不影响峰值。
DEFAULT_MAX_SIZE: Final = 512


@dataclass(frozen=True, slots=True)
class InsertSizeConfig:
    """直方图参数。

    | 字段 | 上游 | 说明 |
    | --- | --- | --- |
    | `max_size` | `insertSizeMax`（默认 512） | 直方图上限；越界与判不出的都并入溢出桶 |
    """

    max_size: int = DEFAULT_MAX_SIZE

    def __post_init__(self) -> None:
        # 只用一处校验：这是用户会直接构造的对象。
        if self.max_size < 1:
            raise ValueError(f"max_size 必须为正，当前为 {self.max_size}。")


@dataclass(frozen=True, slots=True)
class InsertSizeSummary:
    """一次插入片段统计的结果。

    ``histogram`` 的长度是 ``max_size + 1``，最后一项是**溢出桶**
    （判不出 + 超上限）。恒等式：

        total_pairs = sum(histogram[:max_size]) + histogram[max_size]
    """

    total_pairs: int
    overlapped_pairs: int
    max_size: int
    histogram: tuple[int, ...] = ()
    peak_size: int = 0

    @property
    def unknown_pairs(self) -> int:
        """溢出桶里的条数（判不出片段长度，或超过上限）。"""
        return self.histogram[self.max_size] if self.histogram else 0

    @property
    def overlap_rate(self) -> float:
        """能判出片段长度的比例。输入为空时返回 0.0。"""
        if self.total_pairs == 0:
            return 0.0
        return self.overlapped_pairs / self.total_pairs

    def render(self) -> str:
        lines = [
            f"输入 read 对：{self.total_pairs}",
            f"能判出片段长度：{self.overlapped_pairs}（{self.overlap_rate:.1%}）",
            f"峰值片段长度：{self.peak_size}（{self.histogram[self.peak_size]} 对）"
            if self.histogram
            else "峰值片段长度：—",
            f"判不出/超上限：{self.unknown_pairs}",
        ]
        return "\n".join(lines)


class InsertSizeHistogram:
    """逐对累加片段长度直方图。

    用法::

        histogram = InsertSizeHistogram()
        for pair in pairs:
            histogram.add(analyze_overlap(record1.sequence, record2.sequence))
        summary = histogram.summarize()
    """

    def __init__(self, config: InsertSizeConfig | None = None) -> None:
        self.config = config or InsertSizeConfig()
        self._counts: list[int] = [0] * (self.config.max_size + 1)
        self.total_pairs = 0
        self.overlapped_pairs = 0

    def add(self, result: OverlapResult) -> None:
        """累加一对 read 的重叠分析结论。"""
        self.total_pairs += 1
        if result.overlapped:
            self.overlapped_pairs += 1
        self._counts[self._bucket_of(result)] += 1

    def _bucket_of(self, result: OverlapResult) -> int:
        """这一对进哪个桶。

        注意上游的两个边界写法都是**照抄**的：判不出时直接给上限桶，
        算出来的长度**恰好等于上限**时也留在上限桶里（不是并入 unknown 之外），
        判断用的是严格大于。
        """
        if not result.overlapped:
            return self.config.max_size
        size = result.insert_size
        if size > self.config.max_size:
            return self.config.max_size
        return size

    def summarize(self) -> InsertSizeSummary:
        return InsertSizeSummary(
            total_pairs=self.total_pairs,
            overlapped_pairs=self.overlapped_pairs,
            max_size=self.config.max_size,
            histogram=tuple(self._counts),
            peak_size=self._find_peak(),
        )

    def _find_peak(self) -> int:
        """出现次数最多的片段长度。

        只在上限之内找（与上游 ``getPeakInsertSize`` 一致）——溢出桶里混着
        "判不出"的，把它算成峰值没有意义。

        并列时取**较小**的长度：上游用严格大于比较，先遇到的胜出。
        """
        best_index = 0
        best_count = 0
        for index in range(self.config.max_size):
            if self._counts[index] > best_count:
                best_count = self._counts[index]
                best_index = index
        return best_index
