"""read 端裁剪（分片 D）：把落在重复序列里的 read 末端碱基从证据里摘出去。

上游的做法（[Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）：先在参考里找
**1–18 bp 的完全重复**，然后对参考的每个位置算出"从这里开始的 read 要从左端裁掉几个碱基、
在这里结束的 read 要从右端裁掉几个"，而且**每端至少裁 1 个**——因为一个碱基到底是原来那个
还是新插入的拷贝，永远无法只凭一条 read 判断。

为什么必须裁（上游给的例子）：参考里已有 `AGC` 的两个拷贝，样本里插入了第三个。

- **完整跨过整段重复**的 read 看得见"多了一个 AGC"，它是这次插入的**证据**；
- 而**末端落在重复里**的 read，那几位碱基既支持"没变化"、也支持"多了一个拷贝"。
  不裁掉的话，这些 read 会被当成"这里没变化"的**反证**，把真实存在的插入压下去。

这条规则同样保护替换判定：重复区里的读段位置本身就有多解，让它们进似然会凭空添噪声。

**本实现是对上游定性描述的重建，不承诺与上游逐位一致**（这与本项目"比突变集合、不比逐位
一致"的验收口径一致，见算法清单 5.5.3）。规则写死如下：

1. 对位置 ``p`` 与周期长度 ``L ∈ [1, max_unit]``，取满足 ``seq[i] == seq[i + L]`` 的**最长连续
   区间**（向左向右各自扩到不满足为止），它覆盖的参考区间是 ``[start, end + L)``；只有
   ``end - start >= L``（至少两个完整拷贝）才算数——否则一次偶然相同的碱基就能凭空造出裁剪。
2. 所有合法周期里取**延伸最远**的那个区间（保守：宁可多裁，不可漏裁）。
3. ``left_trim[p] = 区间右端 − p``（read 从 p 开始，这么多碱基在重复里）、
   ``right_trim[p] = p − 区间左端 + 1``（read 到 p 结束，这么多碱基在重复里）；
   不在任何重复里则为 **1**。

**按需计算 + 缓存**：不为整条参考预建表。细菌基因组 × 18 个周期在 Python 里是几千万次比较，
而实际用到的只是"每条 read 首尾落在哪儿"，所以按位置算、算过就缓存（同一批数据里 read 的首尾
位置高度重复，缓存命中率很高）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from ...common.reference_io import ReferenceSet

__all__ = ["DEFAULT_MAX_UNIT", "ReferenceTrimmer", "build_trimming"]

#: 上游规则里的重复长度上限（"perfect sequence repeats with lengths of 1-18 bases"）。
DEFAULT_MAX_UNIT = 18

_NO_EXTENT = object()


class ReferenceTrimmer:
    """一条参考序列的 read 端裁剪表：按位置算、算过就缓存。

    ``sequence`` 只被引用、不被复制；缓存随对象存活（一批数据用完即可丢弃）。
    """

    def __init__(
        self, sequence: str, *, seq_id: str = "", max_unit: int = DEFAULT_MAX_UNIT
    ) -> None:
        if max_unit < 1:
            raise ValueError(f"重复长度上限必须 ≥ 1，当前为 {max_unit}。")
        self.seq_id = seq_id
        self.sequence = sequence
        self.max_unit = max_unit
        self._extents: dict[int, tuple[int, int, int] | None] = {}
        self._left: dict[int, int] = {}
        self._right: dict[int, int] = {}

    @property
    def length(self) -> int:
        """参考序列长度。"""
        return len(self.sequence)

    def repeat_extent(self, position: int) -> tuple[int, int, int] | None:
        """位置 ``position`` 所属的重复区间 ``(start, end, unit)``；不在重复里返回 ``None``。

        ``start``/``end`` 是 0-based、左闭右开（``end`` 是重复区右端之后一位），``unit`` 是
        该区间的周期长度。
        """
        if not 0 <= position < len(self.sequence):
            raise IndexError(f"位置 {position} 越出参考长度 {len(self.sequence)}。")
        cached = self._extents.get(position, _NO_EXTENT)
        if cached is _NO_EXTENT:
            cached = self._compute_extent(position)
            self._extents[position] = cached
        return cached

    def left_trim(self, position: int) -> int:
        """从左端对齐到 ``position`` 的 read，应当裁掉几个碱基（至少 1）。"""
        cached = self._left.get(position)
        if cached is None:
            cached = self._trim(position, side="left")
            self._left[position] = cached
        return cached

    def right_trim(self, position: int) -> int:
        """右端（最后一个比对碱基）落在 ``position`` 的 read，应当裁掉几个碱基（至少 1）。"""
        cached = self._right.get(position)
        if cached is None:
            cached = self._trim(position, side="right")
            self._right[position] = cached
        return cached

    # --- 内部 ---------------------------------------------------------------

    def _compute_extent(self, position: int) -> tuple[int, int, int] | None:
        """找覆盖该位置的、延伸最远的合法重复区间。"""
        sequence = self.sequence
        length = len(sequence)
        best: tuple[int, int, int] | None = None
        for unit in range(1, self.max_unit + 1):
            end = position
            while end + unit < length and sequence[end] == sequence[end + unit]:
                end += 1
            start = position
            # 向左扩：每退一步都要保证两边都还在序列里（位置靠近末端、unit 又大时这只约束很紧）。
            while (
                start - unit >= 0
                and start - 1 + unit < length
                and sequence[start - 1] == sequence[start - 1 + unit]
            ):
                start -= 1
            if end - start < unit:
                continue  # 不足两个完整拷贝：不算重复
            region_end = min(end + unit, length)
            if best is None or region_end - start > best[1] - best[0]:
                best = (start, region_end, unit)
        return best

    def _trim(self, position: int, *, side: str) -> int:
        extent = self.repeat_extent(position)
        if extent is None:
            return 1  # 上游：每端至少裁 1 个碱基
        start, end, _unit = extent
        if side == "left":
            return max(1, end - position)
        return max(1, position - start + 1)


def build_trimming(
    reference: ReferenceSet, *, max_unit: int = DEFAULT_MAX_UNIT
) -> Mapping[str, ReferenceTrimmer]:
    """给参考集合里的每条序列建一个裁剪表（懒计算，不预扫参考）。"""
    return MappingProxyType(
        {
            sequence.seq_id: ReferenceTrimmer(
                sequence.sequence, seq_id=sequence.seq_id, max_unit=max_unit
            )
            for sequence in reference
        }
    )
