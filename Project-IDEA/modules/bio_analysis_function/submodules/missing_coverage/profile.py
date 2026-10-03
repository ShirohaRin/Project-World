"""覆盖度剖面（分片 A）：逐参考位置的读段深度。

这是 MC（missing coverage）证据线的地基。与共识调用那条线不同，这里**不需要**逐个碱基的证据，
只问"每个参考位置被多少条 read 盖住"——所以不复用 `consensus_calling` 的堆叠（子模块之间也
不该互相依赖），而是直接从 SAM 的 CIGAR 摊**差分数组**：一条 read 的每个 ``M``/``=``/``X`` 区段
给 ``[起点, 终点)`` 加一，最后前缀和还原逐位深度。代价与参考长度、read 数线性相关，
不随覆盖度膨胀（堆叠那条路要为每个观测建一个对象）。

三条口径：

1. **位置是 0-based**（与堆叠、共识调用一致；``.gd`` 是 1-based，写出去时再换算）。
2. **只数"唯一"比对**：``mapq >= min_mapping_quality``（默认 1）。上游把比对分成 unique / repeat
   两类，覆盖度分析只看 unique-only 位置；我们比对器的 MAPQ 三档（60 / 20 / 0）里 0 就是多命中。
3. **只有 ``M``/``=``/``X`` 贡献覆盖**：``D``/``N`` 上 read 没有碱基，不算覆盖；``S`` 没比上，
   也不算。读段末端被裁剪（`trimming`）的碱基**仍然算覆盖**——裁剪只是让它对突变判定失去信息，
   不会让这条 read 消失；覆盖度问的是"这里有没有 read 来过"。

**规模说明**：每条参考序列一份 ``len(sequence)`` 的整数元组。细菌基因组 4.6 Mb 在 Python 里是
几十 MB，比堆叠那条路省得多，但真实数据仍应换原生实现（与 `read_mapping.md`、`consensus.py`
同一条限制）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from math import fsum
from pathlib import Path

from ...common.alignment_io import SamRecord, open_sam
from ...common.reference_io import ReferenceSet

__all__ = [
    "CoverageProfile",
    "build_coverage_profile",
    "iter_coverage_profiles",
]

#: 贡献覆盖的 CIGAR 操作（消费 read 与参考两侧）。
_MATCH_OPS = ("M", "=", "X")
#: 只消费参考的操作：read 在这些位置上没有碱基，不算覆盖。
_REFERENCE_ONLY_OPS = ("D", "N")


@dataclass(frozen=True, slots=True)
class CoverageProfile:
    """一条参考序列上的逐位深度（``depths[i]`` 是 0-based 位置 ``i`` 的深度）。"""

    seq_id: str
    depths: tuple[int, ...]

    @property
    def length(self) -> int:
        """序列长度（= 数组长度）。"""
        return len(self.depths)

    def depth(self, position: int) -> int:
        """某个 0-based 位置的深度。"""
        return self.depths[position]

    @property
    def covered(self) -> int:
        """至少被一条 read 盖住的位置数。"""
        return sum(1 for value in self.depths if value)

    @property
    def mean(self) -> float:
        """平均深度（对整条序列，含零覆盖的位置）。"""
        if not self.depths:
            return 0.0
        return fsum(self.depths) / len(self.depths)

    @property
    def variance(self) -> float:
        """深度的总体方差（``ddof=0``，与上游按矩估计拟合分布的用法一致）。"""
        if not self.depths:
            return 0.0
        mean = self.mean
        return fsum((value - mean) ** 2 for value in self.depths) / len(self.depths)

    def histogram(self) -> dict[int, int]:
        """深度 → 该深度的位置数。"""
        counts: dict[int, int] = {}
        for value in self.depths:
            counts[value] = counts.get(value, 0) + 1
        return counts


def _iter_records(source: str | Path | Iterable[SamRecord]) -> Iterator[SamRecord]:
    """统一两种输入：SAM 文件路径（明文或 gzip）或已经读好的记录序列。"""
    if isinstance(source, (str, Path)):
        with open_sam(source) as reader:
            yield from reader
        return
    yield from source


def _diff_arrays(
    reference: ReferenceSet, source: str | Path | Iterable[SamRecord], min_mapping_quality: int
) -> dict[str, list[int]]:
    """一条 read 摊一次差分：``[起点, 终点)`` 加一、``终点`` 减一。"""
    if min_mapping_quality < 0:
        raise ValueError(f"最低比对质量不能为负，当前为 {min_mapping_quality}。")
    lengths = {sequence.seq_id: sequence.length for sequence in reference}
    differences = {seq_id: [0] * (length + 1) for seq_id, length in lengths.items()}

    for record in _iter_records(source):
        if record.is_unmapped or record.cigar.is_empty:
            continue  # 没比上的、或没有 CIGAR 的记录给不出覆盖
        if record.mapping_quality < min_mapping_quality:
            continue  # 多命中（MAPQ 0）不算 unique-only 覆盖
        seq_id = record.reference_name
        if seq_id not in differences:
            raise ValueError(
                f"比对结果里的参考名 {seq_id!r} 不在参考集合里（{sorted(lengths)}）。"
            )
        length = lengths[seq_id]
        difference = differences[seq_id]
        position = record.position - 1  # 0-based
        for op in record.cigar.ops:
            kind, span = op.op, op.length
            if kind in _MATCH_OPS:
                start, end = position, position + span
                if start < 0 or end > length:
                    raise ValueError(
                        f"记录 {record.query_name!r} 在 {seq_id} 上的比对区间 "
                        f"[{start}, {end}) 越出参考范围 [0, {length})；"
                        "跨复制原点的比对本版本不支持。"
                    )
                difference[start] += 1
                difference[end] -= 1
                position += span
            elif kind in _REFERENCE_ONLY_OPS:
                position += span
            elif kind in ("I", "S", "H", "P"):
                continue  # 不消费参考，位置不动
            else:  # pragma: no cover - alignment_io 只允许八种操作，这里兜底
                raise ValueError(f"覆盖度统计遇到不支持的 CIGAR 操作：{kind!r}。")
    return differences


def iter_coverage_profiles(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    min_mapping_quality: int = 1,
) -> Iterator[CoverageProfile]:
    """把比对结果摊成逐位深度，按参考文件的顺序**每条序列各出一份**。

    没有 read 的序列也会产出（全是零）——"这条序列完全没覆盖"本身是需要被看见的事实，
    分析方法（分片 B）遇到全零序列会明确地放弃拟合，而不是偷偷给一个阈值。
    """
    differences = _diff_arrays(reference, source, min_mapping_quality)
    for sequence in reference:
        difference = differences[sequence.seq_id]
        running = 0
        depths: list[int] = []
        for index in range(sequence.length):
            running += difference[index]
            depths.append(running)
        yield CoverageProfile(seq_id=sequence.seq_id, depths=tuple(depths))


def build_coverage_profile(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    seq_id: str,
    *,
    min_mapping_quality: int = 1,
) -> CoverageProfile:
    """只要某一条参考序列的剖面（测试与单序列分析用）。"""
    if seq_id not in reference.ids:
        raise ValueError(f"参考集合里没有 {seq_id!r}（有：{list(reference.ids)}）。")
    for profile in iter_coverage_profiles(
        reference, source, min_mapping_quality=min_mapping_quality
    ):
        if profile.seq_id == seq_id:
            return profile
    raise ValueError(f"参考集合里没有 {seq_id!r}。")  # pragma: no cover - 上面已拦
