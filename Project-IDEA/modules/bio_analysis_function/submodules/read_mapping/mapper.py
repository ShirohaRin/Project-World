"""比对器：种子投票 → 候选对角线 → 带内比对 → 挑最好的一条。

- 分片 A（`index.py`）：参考的 k-mer 索引用来快速定位候选位置；
- 分片 B：种子投票选出候选对角线；
- 分片 C（本文件 + `dp.py`）：在候选对角线上做**带内半全局比对**，允许短 indel，
  产出 CIGAR 与错配/插入/缺失的明细。

三条口径先写清楚，后面每一片都要用到：

1. **对角线**（diagonal）定义为"read 第 0 位落在参考的哪个坐标"，**0-based**。
   正向命中记 ``d = p - j``（``j`` 是 read 偏移、``p`` 是参考位置）；
   负向命中先把 read 反向互补再按同样的方式记，于是两个方向的对角线可以直接比较。
2. **负链存的是"反向互补后的序列"**：SAM 也是这个口径（FLAG 0x10 时 SEQ 存的是
   正向参考方向）。因此比对时统一拿"与参考同向"的那条序列逐位比对，不需要分情况。
3. **read 必须被完整消费**：本片不做软剪裁，参考两端自由（允许 read 只覆盖参考的一部分，
   也允许末尾/开头出现插入）。软件剪裁与末端处理是后续分片的事。

挑最好的一条：先比**编辑距离**（错配 + 插入碱基 + 缺失碱基，少者优），再比种子票数
（多者优），最后按（起点、参考名、链方向）定序——保证同一份输入每次跑结果一样。

**加速的一个前提条件**：如果某条候选在**无缺口**下完全匹配，它的编辑距离就是 0，
不可能更好，因此直接采用、不再做动态规划。真实数据里与参考差异 <1‰，多数 read
命中这条快路（见 `read_mapping.md` 的实测）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from ...common.alignment_io import Cigar, CigarOp
from ...common.reference_io import ReferenceSet
from ...common.sequences import reverse_complement
from .dp import BandedAlignment, align_banded
from .index import (
    DEFAULT_MAX_HITS,
    DEFAULT_SEED_LENGTH,
    DEFAULT_STEP,
    ReferenceIndex,
    encode_kmer,
    reverse_complement_kmer,
)

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_CLIP",
    "DEFAULT_MAX_INDEL",
    "DEFAULT_MAX_MISMATCHES",
    "Alignment",
    "Mapper",
    "MappingParams",
    "Read",
]

#: 默认允许的错配数上限。见 `read_mapping.md` 对"为什么不能太紧"的说明。
DEFAULT_MAX_MISMATCHES = 4
#: 默认允许的净漂移（带宽）：插入碱基 + 缺失碱基之和的上限也是它。
DEFAULT_MAX_INDEL = 3
#: 默认每端最多软剪裁多少碱基。接头读通、质量掉尾这类末端靠它处理。
DEFAULT_MAX_CLIP = 10
#: 默认最多扩展几条候选对角线（按票数排序后的前若干条）。
DEFAULT_MAX_CANDIDATES = 8


@dataclass(frozen=True, slots=True)
class Read:
    """比对器消费的最小读段：名字、序列、质量、是否 read2。

    序列必须**大写**（与参考同一口径）；允许含 ``N``，含 ``N`` 的窗口不能做种子，
    且比对时 ``N`` 按错配计（不当作"匹配任意碱基"）。
    """

    name: str
    sequence: str
    qualities: str = ""
    is_read2: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("读段缺少名字。")
        if not self.sequence:
            raise ValueError(f"读段 {self.name} 的序列为空。")
        if any(character.isspace() for character in self.sequence):
            raise ValueError(f"读段 {self.name} 的序列里含空白。")
        if self.sequence != self.sequence.upper():
            raise ValueError(f"读段 {self.name} 的序列必须是大写。")
        if self.qualities and len(self.qualities) != len(self.sequence):
            raise ValueError(
                f"读段 {self.name} 的质量长度 {len(self.qualities)} 与序列长度 "
                f"{len(self.sequence)} 不符。"
            )

    @property
    def length(self) -> int:
        """读长（碱基数）。"""
        return len(self.sequence)


@dataclass(frozen=True, slots=True)
class Alignment:
    """一条比对结果。

    ``reference_start`` 是 **0-based** 的左端坐标；``reference_length`` 是对齐覆盖的
    参考长度——有 indel 时它**不等于**读长（``query_length``）。CIGAR 含 ``M`` / ``I`` /
    ``D``，末端处理打开时还会含 ``S``（软剪裁）；``M`` 里既含匹配也含错配，错配数单独给出。
    """

    query_name: str
    seq_id: str
    strand: int
    reference_start: int
    reference_length: int
    query_length: int
    mismatches: int
    insertions: int
    deletions: int
    cigar: Cigar
    seed_hits: int
    candidates: int
    is_read2: bool = False
    soft_clipped: int = 0
    #: 映射质量：60 表示这次命中是**唯一**的，0 表示有同样好的另一处、20 表示另有一处
    #: 但明显更差。这是**粗粒度的唯一性信号**，不是 bowtie2 的 MAPQ 标度（见文档）。
    mapq: int = 0

    @property
    def is_unique(self) -> bool:
        """这次命中是否唯一（没有同样好的另一处）。"""
        return self.mapq == 60

    @property
    def reference_end(self) -> int:
        """覆盖区间的右端（0-based、闭区间）。"""
        return self.reference_start + self.reference_length - 1

    @property
    def aligned_length(self) -> int:
        """真正参与比对的读段长度（剪掉软剪裁部分）。"""
        return self.query_length - self.soft_clipped

    @property
    def edit_distance(self) -> int:
        """错配 + 插入碱基 + 缺失碱基（软剪裁不算错误）。"""
        return self.mismatches + self.insertions + self.deletions

    @property
    def identity(self) -> float:
        """按编辑距离算的一致度：``1 - 编辑距离 / 参与比对的长度``。

        分母是**参与比对**的碱基数（不是整条读长）——剪掉的部分没有参与比对，
        把它算进分母会把一致度无故拉低。
        """
        if not self.aligned_length:
            return 0.0
        return 1.0 - self.edit_distance / self.aligned_length

    @property
    def is_gapped(self) -> bool:
        """是否含插入或缺失。"""
        return bool(self.insertions or self.deletions)


@dataclass(frozen=True, slots=True)
class MappingParams:
    """比对参数。默认值面向细菌重测序（与参考差异 <1‰、读长 100~150 bp）。"""

    seed_length: int = DEFAULT_SEED_LENGTH
    step: int = DEFAULT_STEP
    max_hits: int = DEFAULT_MAX_HITS
    max_mismatches: int = DEFAULT_MAX_MISMATCHES
    max_indel: int = DEFAULT_MAX_INDEL
    #: 每端最多软剪裁多少碱基（接头读通、质量掉尾这类"解释不了的末端"）。
    #: 0 表示关掉末端处理——那时末端只能被硬塞成错配或插入。
    max_clip: int = DEFAULT_MAX_CLIP
    max_candidates: int = DEFAULT_MAX_CANDIDATES

    def __post_init__(self) -> None:
        if self.seed_length < 1:
            raise ValueError(f"种子长度必须 ≥ 1，当前为 {self.seed_length}。")
        if self.step < 1:
            raise ValueError(f"步长必须 ≥ 1，当前为 {self.step}。")
        if self.max_mismatches < 0:
            raise ValueError(f"错配上限不能为负，当前为 {self.max_mismatches}。")
        if self.max_indel < 0:
            raise ValueError(f"indel 上限不能为负，当前为 {self.max_indel}。")
        if self.max_clip < 0:
            raise ValueError(f"软剪裁上限不能为负，当前为 {self.max_clip}。")
        if self.max_candidates < 1:
            raise ValueError(f"候选对角线数必须 ≥ 1，当前为 {self.max_candidates}。")


@dataclass(frozen=True, slots=True)
class Mapper:
    """把 reads 比到一组参考序列上。

    参考与索引分开传：索引是按参考建的重结构，调用方往往要复用同一份索引比对多批 reads。
    两者的参数必须自洽（索引的 k / step 与 :class:`MappingParams` 一致），本类会检查这一点，
    避免"用 k=16 的索引去比对 k=12 的种子"这种静默错误。
    """

    reference: ReferenceSet
    index: ReferenceIndex
    params: MappingParams = MappingParams()

    def __post_init__(self) -> None:
        for index in self.index.indexes.values():
            if index.length != self.reference.get(index.seq_id).length:
                raise ValueError(
                    f"索引 {index.seq_id} 的长度 {index.length} 与参考 "
                    f"{self.reference.get(index.seq_id).length} 不一致。"
                )
            if (index.k, index.step) != (self.params.seed_length, self.params.step):
                raise ValueError(
                    f"索引 {index.seq_id} 的 (k, step)=({index.k}, {index.step}) 与参数 "
                    f"({self.params.seed_length}, {self.params.step}) 不一致。"
                )

    def map_read(self, read: Read) -> Alignment | None:
        """比对一条 read；比不上返回 ``None``。"""
        if read.length < self.params.seed_length:
            return None
        votes = self._vote(read.sequence)
        if not votes:
            return None
        # 候选按票数排序（并列时按起点、参考名、链方向定序，保证结果稳定）。
        ordered = sorted(
            votes.items(),
            key=lambda item: (-item[1], item[0][2], item[0][1], item[0][0]),
        )
        shortlist = ordered[: self.params.max_candidates]

        # 把短名单里的每条候选都算出来——**不能只算最好的一条**：
        # 映射质量要看"有没有同样好的另一处"，那必须知道第二名是谁。
        # 完全匹配的候选走无缺口快路（不必做动态规划，结果等价）。
        scored: list[tuple[tuple[int, int, int, str, int], int, BandedAlignment]] = []
        for (seq_id, strand, start), hits in shortlist:
            if self._count_mismatches(read.sequence, seq_id, start, strand) == 0:
                result: BandedAlignment | None = _ungapped_result(read.length, start)
            else:
                result = self._align_candidate(read.sequence, seq_id, start, strand)
                if result is None:
                    continue
                if result.mismatches > self.params.max_mismatches:
                    continue
                if result.insertions + result.deletions > self.params.max_indel:
                    continue
            scored.append(
                ((result.edit_distance, -hits, start, seq_id, strand), hits, result)
            )
        if not scored:
            return None
        scored.sort(key=lambda item: item[0])
        key, hits, result = scored[0]
        runner_up = scored[1][0][0] if len(scored) > 1 else None
        if runner_up is None:
            mapq = 60  # 唯一
        elif runner_up == key[0]:
            mapq = 0  # 有同样好的另一处：无法区分
        else:
            mapq = 20  # 另有更差的可行位置
        return self._to_alignment(
            read, key[3], key[4], hits, len(votes), result, mapq=mapq
        )

    def map_reads(self, reads: Iterable[Read]) -> Iterator[Alignment | None]:
        """按输入顺序逐条比对，输出与输入**一一对应**（比不上的位置是 ``None``）。"""
        for read in reads:
            yield self.map_read(read)

    # --- 内部 ---------------------------------------------------------------

    def _to_alignment(
        self,
        read: Read,
        seq_id: str,
        strand: int,
        hits: int,
        candidates: int,
        result: BandedAlignment,
        *,
        mapq: int = 0,
    ) -> Alignment:
        """把带内比对结果包装成对外的 :class:`Alignment`。"""
        return Alignment(
            query_name=read.name,
            seq_id=seq_id,
            strand=strand,
            reference_start=result.reference_start,
            reference_length=result.reference_length,
            query_length=read.length,
            mismatches=result.mismatches,
            insertions=result.insertions,
            deletions=result.deletions,
            cigar=result.cigar,
            seed_hits=hits,
            candidates=candidates,
            is_read2=read.is_read2,
            soft_clipped=result.soft_clipped,
            mapq=mapq,
        )

    def _vote(self, sequence: str) -> dict[tuple[str, int, int], int]:
        """给每条候选对角线收集种子票数。

        键是 ``(参考名, 链方向, read 第 0 位落在参考的坐标)``；正负两个方向在同一个
        循环里收集，因为"挑最好的一条"必须把两个方向放在一起比。
        """
        k = self.params.seed_length
        length = len(sequence)
        votes: dict[tuple[str, int, int], int] = {}
        for offset in range(0, length - k + 1):
            code = encode_kmer(sequence[offset : offset + k])
            if code is None:
                continue
            reverse_code = reverse_complement_kmer(code, k)
            for seq_id, index in self.index.indexes.items():
                for position in index.lookup(code):
                    key = (seq_id, 1, position - offset)
                    votes[key] = votes.get(key, 0) + 1
                # 负链：把 read 反向互补后再对齐，因此偏移要换算成互补串上的位置。
                for position in index.lookup(reverse_code):
                    key = (seq_id, -1, position - (length - k - offset))
                    votes[key] = votes.get(key, 0) + 1
        return votes

    def _aligned_query(self, sequence: str, strand: int) -> str:
        """与参考同向的那条序列（负链取反向互补）。"""
        return sequence if strand == 1 else reverse_complement(sequence)

    def _count_mismatches(
        self, sequence: str, seq_id: str, start: int, strand: int
    ) -> int | None:
        """无缺口扩展：逐位比对并数错配；放不下（越界）时返回 ``None``。"""
        reference_sequence = self.reference.get(seq_id).sequence
        length = len(sequence)
        if start < 0 or start + length > len(reference_sequence):
            return None
        target = reference_sequence[start : start + length]
        query = self._aligned_query(sequence, strand)
        return sum(1 for query_base, target_base in zip(query, target) if query_base != target_base)

    def _align_candidate(
        self, sequence: str, seq_id: str, start: int, strand: int
    ) -> BandedAlignment | None:
        """在候选对角线附近做带内比对（允许短 indel），并做末端软剪裁。"""
        reference_sequence = self.reference.get(seq_id).sequence
        query = self._aligned_query(sequence, strand)
        margin = self.params.max_indel
        window_start = max(0, start - margin)
        window_end = min(len(reference_sequence), start + len(query) + margin)
        if window_end <= window_start:
            return None
        return align_banded(
            query,
            reference_sequence[window_start:window_end],
            window_start=window_start,
            center=start - window_start,
            max_indel=margin,
            max_clip=self.params.max_clip,
            # 剪完至少要留下一个种子长度的碱基参与比对：再短就算不上"比上了"。
            min_aligned_length=self.params.seed_length,
        )


def _ungapped_result(length: int, start: int) -> BandedAlignment:
    """构造"无缺口、全匹配"的比对结果（快路用）。"""
    return BandedAlignment(
        reference_start=start,
        reference_length=length,
        query_length=length,
        mismatches=0,
        insertions=0,
        deletions=0,
        cigar=Cigar(ops=(CigarOp(length=length, op="M"),)),
    )
