"""候选连接的打分与排序（分片 B）：位置哈希分、最小重叠分、截断。

上游（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)，
"Identifying candidate junctions" 末段）把同一条连接的多个候选合并之后，用两个分数排序：

- **位置哈希分（position-hash score）**：支持这条连接的 read 里，**不同的 read 起点位置**有几个。
  病理候选（比如纯属比对上来的噪声）往往只由"刚好压到连接点、而且起点几乎一样"的 read 撑起来，
  这个分数会很低；真实连接会被从不同位置跨过来的一批 read 支撑。原文还强调它同时偏好
  "两条链上分布均匀"的 read。
- **最小重叠分（minimum-overlap score）**：对每条支持 read，取它在连接**两侧**各自独有的
  read 碱基数（不算重叠区）的**较小值**，再对所有 read 求和。它是位置哈希分的**并列时的**
  第二排序键。

然后**截断**：按这两个分数从高到低保留候选，直到"累计连接长度超过全参考总长的 0.1 倍"或者
"候选数超过 5000"。这两个数都是上游写明的。

本实现的两点明确差异（不承诺与上游一致）：
- 上游的最终接受阈值用的是 ``neg_log10_pos_hash_p_value``（"skew"），本模块**只算位置哈希分
  本身**——那个 p 值的零假设没公开，我们没有复刻（接受判据在 `acceptance` 那一片，见文档待办）。
- "合并同一条连接的候选"在这里按**连接的身份**（两段的参考位置 + 中间那截 read 碱基）合并，
  而不是按上游内部的序列指纹；同一条连接会被合成一个候选，这是它该有的语义。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .candidates import (
    CandidateSettings,
    ChimericPair,
    build_junction_sequence,
    iter_chimeric_pairs,
    longest_read_length,
)

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_CUMULATIVE_FRACTION",
    "JunctionCandidate",
    "JunctionKey",
    "SupportingRead",
    "call_junctions",
    "group_candidates",
    "rank_candidates",
]

#: 上游的截断：候选数上限。
DEFAULT_MAX_CANDIDATES = 5000
#: 上游的截断：累计连接长度相对全参考总长的上限。
DEFAULT_MAX_CUMULATIVE_FRACTION = 0.1


@dataclass(frozen=True, slots=True)
class JunctionKey:
    """一条连接的**身份**：左右两段的参考位置，加上中间那一截 read 碱基。

    中间那截进身份是有理由的：**同一条连接序列**才该合并成一个候选；连接点相同但中间夹的
    碱基不同，那是两回事（比如同一个位点上插了不同的序列）。
    """

    left_seq_id: str
    #: 左段最后一个参考位置（0-based）——连接点在它之后。
    left_breakpoint: int
    #: 两段之间那截只属于 read 的碱基（没有就空串）。
    intervening: str
    right_seq_id: str
    #: 右段第一个参考位置（0-based）——连接点在它之前。
    right_breakpoint: int

    @property
    def label(self) -> str:
        """人读用的短标签（写报告、报错信息用）。"""
        return (
            f"{self.left_seq_id}:{self.left_breakpoint}|"
            f"{self.intervening or '.'}|"
            f"{self.right_seq_id}:{self.right_breakpoint}"
        )


@dataclass(frozen=True, slots=True)
class SupportingRead:
    """一条支撑某连接候选的 read（去重后按 read 名保留一条）。"""

    query_name: str
    #: 这条 read 自己在参考上的起点（见 `AlignmentSegment.start_anchor`）。
    anchor: int
    is_reverse: bool
    unique_first: int
    unique_second: int

    @property
    def min_unique(self) -> int:
        """两侧独有碱基数的较小值（最小重叠分的加数）。"""
        return min(self.unique_first, self.unique_second)


@dataclass(frozen=True, slots=True)
class JunctionCandidate:
    """一个候选连接：身份、连接序列、支撑 read。"""

    key: JunctionKey
    sequence: str
    support: tuple[SupportingRead, ...]

    @property
    def read_count(self) -> int:
        """支撑 read 条数。"""
        return len(self.support)

    @property
    def pos_hash_score(self) -> int:
        """位置哈希分：支撑 read 里**不同的起点位置**个数。"""
        return len({read.anchor for read in self.support})

    @property
    def min_overlap_score(self) -> int:
        """最小重叠分：每条 read 两侧独有碱基数较小值之和。"""
        return sum(read.min_unique for read in self.support)

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """降序用的排序键（位置哈希分 → 最小重叠分 → 身份标签，保证输出确定）。"""
        return (-self.pos_hash_score, -self.min_overlap_score, self.key.label)


@dataclass(slots=True)
class _CandidateBuilder:
    """合并过程中的可变容器。"""

    key: JunctionKey
    sequence: str
    support: dict[str, SupportingRead] = field(default_factory=dict)

    def build(self) -> JunctionCandidate:
        # 按 read 名排序，输出确定；同一条 read 的多条候选记录只留一条
        reads = tuple(self.support[name] for name in sorted(self.support))
        return JunctionCandidate(key=self.key, sequence=self.sequence, support=reads)


def group_candidates(
    entries: Iterable[tuple[ChimericPair, str]],
    reference: ReferenceSet,
    *,
    flank: int,
) -> tuple[JunctionCandidate, ...]:
    """把 ``(嵌合对, read 序列)`` 合并成候选连接（顺序按首次出现）。"""
    builders: dict[JunctionKey, _CandidateBuilder] = {}
    for pair, read_sequence in entries:
        sequence = build_junction_sequence(pair, read_sequence, reference, flank=flank)
        key = JunctionKey(
            left_seq_id=pair.first.seq_id,
            left_breakpoint=pair.first.reference_end,
            intervening=read_sequence[pair.first.query_end + 1 : pair.second.query_start],
            right_seq_id=pair.second.seq_id,
            right_breakpoint=pair.second.reference_start,
        )
        builder = builders.get(key)
        if builder is None:
            builder = _CandidateBuilder(key=key, sequence=sequence)
            builders[key] = builder
        builder.support.setdefault(
            pair.first.query_name,
            SupportingRead(
                query_name=pair.first.query_name,
                anchor=pair.first.start_anchor,
                is_reverse=pair.first.is_reverse,
                unique_first=pair.unique_first,
                unique_second=pair.unique_second,
            ),
        )
    return tuple(builder.build() for builder in builders.values())


def rank_candidates(
    candidates: Iterable[JunctionCandidate],
    *,
    reference_length: int,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_cumulative_fraction: float = DEFAULT_MAX_CUMULATIVE_FRACTION,
) -> tuple[JunctionCandidate, ...]:
    """按两个分数降序排、按上游的两条上限截断。"""
    if reference_length <= 0:
        raise ValueError(f"参考总长必须为正，当前为 {reference_length!r}。")
    if max_candidates < 1:
        raise ValueError(f"候选数上限必须为正，当前为 {max_candidates!r}。")
    if not 0.0 < max_cumulative_fraction <= 1.0:
        raise ValueError(f"累计长度占比必须落在 (0, 1]，当前为 {max_cumulative_fraction!r}。")

    ordered = sorted(candidates, key=lambda candidate: candidate.sort_key)
    budget = max_cumulative_fraction * reference_length
    kept: list[JunctionCandidate] = []
    cumulative = 0
    for candidate in ordered:
        if len(kept) >= max_candidates or cumulative + len(candidate.sequence) > budget:
            break  # 上游是"加到会超就停"，不是跳过它继续试下一个
        kept.append(candidate)
        cumulative += len(candidate.sequence)
    return tuple(kept)


def call_junctions(
    records: Iterable[SamRecord],
    reference: ReferenceSet,
    *,
    candidate_settings: CandidateSettings = CandidateSettings(),
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_cumulative_fraction: float = DEFAULT_MAX_CUMULATIVE_FRACTION,
) -> tuple[JunctionCandidate, ...]:
    """从比对结果到排好序的候选连接（分片 A + B 的入口）。

    两遍扫描：先定"整个数据集里最长 read 的长度"（拼连接序列时两端各留这么多参考碱基），
    再找嵌合对、合并成候选、排序截断。
    """
    materialized = tuple(records)
    flank = longest_read_length(materialized)
    if flank == 0:
        return ()
    entries = (
        (pair, record.sequence)
        for record, pair in iter_chimeric_pairs(materialized, settings=candidate_settings)
    )
    candidates = group_candidates(entries, reference, flank=flank)
    return rank_candidates(
        candidates,
        reference_length=reference.total_length,
        max_candidates=max_candidates,
        max_cumulative_fraction=max_cumulative_fraction,
    )
