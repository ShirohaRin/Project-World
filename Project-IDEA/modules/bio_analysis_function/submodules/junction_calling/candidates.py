"""嵌合比对的对（分片 A 之二）：哪些两段合起来像是"跨了个新连接"。

上游判据（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)，
"Identifying candidate junctions"）原文是五条，本实现逐条照搬：

> 对每一条有多个比对的 read，测试所有比对两两组合，要求：
> 1. 有一个比对从 read 的第一个碱基开始；
> 2. 两个比对合起来覆盖的 read 碱基数，比任何**单**个比对覆盖的多出 2 个以上；
> 3. 两个比对各自至少有 5 个 read 碱基不与对方重叠；
> 4. 其中一个比对至少有 10 个 read 碱基不与对方重叠；
> 5. 两个比对之间最多有 20 bp 的 read 碱基是"只属于这条 read"的。

第 2 条是这条判据的灵魂：**两段拼起来明显比任何一段单独解释得好**，才值得考虑"这里有个新连接"。
第 5 条限制了"新连接中间夹的那一截"的长度——夹太多就不是同一个连接了。

判完之后把候选连接序列拼出来：**左段贴到断点的参考碱基 + 中间那截 read 碱基 + 右段从断点起的
参考碱基**；两端各留多少个参考碱基，上游取"整个数据集里最长 read 的长度"，见
`build_junction_sequence` 的 `flank` 参数。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .segments import AlignmentSegment, segments_of

__all__ = [
    "CandidateSettings",
    "ChimericPair",
    "build_junction_sequence",
    "group_segments_by_read",
    "iter_chimeric_pairs",
    "longest_read_length",
]


@dataclass(frozen=True, slots=True)
class CandidateSettings:
    """五条判据里的四个数字（上游写明的就是这四组）。"""

    #: 判据 3：两段各自至少要有这么多 read 碱基不与对方重叠。
    min_unique_each: int = 5
    #: 判据 4：其中一段至少要有这么多（比上一条更严）。
    min_unique_one: int = 10
    #: 判据 5：两段之间最多夹这么多 read 碱基。
    max_intervening: int = 20
    #: 判据 2：两段合起来要比最好的一段单独解释**多出这么多**才算。
    coverage_margin: int = 2


@dataclass(frozen=True, slots=True)
class ChimericPair:
    """一对像"跨了新连接"的比对段。``first`` 是按 read 方向靠前的那一段。"""

    first: AlignmentSegment
    second: AlignmentSegment

    @property
    def overlap(self) -> int:
        """两段在 read 上的重叠碱基数。"""
        return self.first.query_overlap(self.second)

    @property
    def unique_first(self) -> int:
        """第一段里不与第二段重叠的 read 碱基数。"""
        return self.first.query_length - self.overlap

    @property
    def unique_second(self) -> int:
        """第二段里不与第一段重叠的 read 碱基数。"""
        return self.second.query_length - self.overlap

    @property
    def union_length(self) -> int:
        """两段合起来覆盖的 read 碱基数。"""
        return self.first.query_length + self.second.query_length - self.overlap

    @property
    def intervening(self) -> int:
        """两段之间夹着的、（只属于这条 read 的）碱基数；重叠或相邻时为 0。"""
        if self.second.query_start <= self.first.query_end + 1:
            return 0
        return self.second.query_start - self.first.query_end - 1


def group_segments_by_read(
    records: Iterable[SamRecord],
) -> dict[str, tuple[AlignmentSegment, ...]]:
    """按 read 名把**所有记录的段**收在一起（一条 read 可能有多条比对记录）。

    返回的字典保持首次出现的顺序（Python 的 dict 有序），便于输出确定。
    """
    grouped: dict[str, list[AlignmentSegment]] = {}
    for record in records:
        segments = segments_of(record)
        if not segments:
            continue
        grouped.setdefault(record.query_name, []).extend(segments)
    return {name: tuple(items) for name, items in grouped.items()}


def find_pairs(
    segments: tuple[AlignmentSegment, ...],
    *,
    settings: CandidateSettings = CandidateSettings(),
) -> tuple[ChimericPair, ...]:
    """在一条 read 的所有段里找出所有满足五条判据的对。"""
    if len(segments) < 2:
        return ()
    ordered = sorted(segments, key=lambda segment: segment.query_start)
    best_single = max(segment.query_length for segment in segments)
    pairs: list[ChimericPair] = []
    for index, first in enumerate(ordered):
        if first.query_start != 0:
            continue  # 判据 1：靠前的那段必须从 read 的第一个碱基开始
        for second in ordered[index + 1 :]:
            pair = ChimericPair(first=first, second=second)
            if pair.union_length <= best_single + settings.coverage_margin:
                continue  # 判据 2
            if min(pair.unique_first, pair.unique_second) < settings.min_unique_each:
                continue  # 判据 3
            if max(pair.unique_first, pair.unique_second) < settings.min_unique_one:
                continue  # 判据 4
            if pair.intervening > settings.max_intervening:
                continue  # 判据 5
            pairs.append(pair)
    return tuple(pairs)


def iter_chimeric_pairs(
    records: Iterable[SamRecord],
    *,
    settings: CandidateSettings = CandidateSettings(),
) -> Iterator[tuple[SamRecord, ChimericPair]]:
    """逐条 read 找嵌合对，产出 ``(该 read 的一条记录, 对)``。

    记录用来取 read 的序列（拼中间那截碱基要用）。同一条 read 的多条记录里，序列是一样的，
    这里取**第一条带序列的记录**。
    """
    representatives: dict[str, SamRecord] = {}
    for record in records:
        if record.sequence == "*":
            continue
        representatives.setdefault(record.query_name, record)
    for query_name, segments in group_segments_by_read(records).items():
        record = representatives.get(query_name)
        if record is None:
            continue
        for pair in find_pairs(segments, settings=settings):
            yield record, pair


def longest_read_length(records: Iterable[SamRecord]) -> int:
    """整个数据集里最长 read 的长度（拼连接序列时两端各留这么多参考碱基）。"""
    longest = 0
    for record in records:
        if record.sequence == "*":
            continue
        longest = max(longest, len(record.sequence))
    return longest


def build_junction_sequence(
    pair: ChimericPair,
    read_sequence: str,
    reference: ReferenceSet,
    *,
    flank: int,
) -> str:
    """拼出候选连接的序列。

    左段取到它**最后一个**参考位置为止的 ``flank`` 个参考碱基、接着是两段之间那截 read 碱基、
    再接着右段从它**第一个**参考位置起的 ``flank`` 个参考碱基。序列两端不够长时**截断**
    （环状参考上跨原点的连接本版本不做，见文档的已知限制）。

    两段必须同链：``SEQ`` 在本项目里按"与参考同向"存（见 `pileup.py`），一旦两段一正一反，
    "中间那截 read 碱基朝哪边读"就没有统一答案了——那种反转类连接本版本直接报错，不猜。
    """
    first, second = pair.first, pair.second
    if first.is_reverse != second.is_reverse:
        raise ValueError("两段比对不在同一条链上：反转类连接本版本不支持。")
    if flank < 1:
        raise ValueError(f"两端保留的参考碱基数必须为正，当前为 {flank!r}。")

    left_sequence = reference.get(first.seq_id).sequence
    right_sequence = reference.get(second.seq_id).sequence
    left_start = max(0, first.reference_end - flank + 1)
    right_end = min(len(right_sequence), second.reference_start + flank)
    intervening = read_sequence[first.query_end + 1 : second.query_start]
    return (
        left_sequence[left_start : first.reference_end + 1]
        + intervening
        + right_sequence[second.reference_start : right_end]
    )
