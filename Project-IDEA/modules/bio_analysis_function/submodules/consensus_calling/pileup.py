"""堆叠（pileup）：把比对结果摊成"每个参考位置上有什么碱基"的证据。

共识碱基调用的第一步，也是上游 breseq 的口径：先把 reads 比到参考（或用外部比对结果），
再逐位点看堆叠，然后才谈得上"这个位置在样本里到底是什么碱基"。

**输入是 SAM，不是比对器的内部对象**，这条取舍是有意的：

- 公共层 `common/alignment_io` 已经能读写 SAM，它同时是**外部比对结果**的入口
  （见算法清单 5.5.3：接受外部 SAM/BAM 是与上游做阶段级对拍的唯一手段）；
- 我们自己的比对器也用 `write_mapped_sam` 落盘，所以"比对器 → SAM → 堆叠"比
  "比对器 → 堆叠"更宽：换 bowtie2 跑出来的 SAM 照样能进这条链；
- 于是本模块只依赖 `common/`，不依赖 `submodules/read_mapping/`——符合
  "子模块不依赖其他子模块"的约定（开发规则 3.1 第 4 条）。

坐标与朝向的口径（与 SAM 规范、也与本项目比对器的内部口径一致）：

1. ``SEQ`` 存的是**与参考同向**的序列（负链记录已经是反向互补后的），所以堆叠**不需要**
   再翻转；链方向只影响"这条证据来自哪条链"这个统计量。
2. 位置是 **0-based**（SAM 的 ``POS`` 是 1-based，读进来时换算）。
3. ``read_offset`` 是该碱基在 ``SEQ`` 里的下标、``read_length`` 是 ``SEQ`` 的长度——
   read 端裁剪（分片 D）靠这两个字段判断"这个碱基离 read 末端还有多远"。

CIGAR 的消费关系照 `common/alignment_io` 的表：

======  ==========================================================
``M``/``=``/``X``  消费两侧 → 产生一个**碱基观测**
``I``              只消费 read → 记成**插入事件**，挂在它前面那个参考位置上
``D``              只消费参考 → 记成**缺失事件**，落在它覆盖的**每一个**参考位置上
``S``              只消费 read → 软剪裁，不产生任何证据（但仍推进 ``read_offset``）
``N``              只消费参考 → 跳过的区域不产证据，也不做插入挂靠
``H``/``P``        两侧都不消费 → 忽略
======  ==========================================================

两条约定值得单独说清：

- **插入的挂靠位置**：插入发生在两个参考位置**之间**，堆叠是按参考位置组织的，所以统一
  记在它前面那个参考位置上（与 breseq 的坐标口径一致）。若比对以插入开头（前面没有参考
  位置可挂），这条插入**不记录**——它没有可表达的坐标。
- **缺失的质量**：缺失本身没有质量。上游 breseq 用"read 中下一个被比对的碱基的质量"
  来代表单碱基缺失；本实现沿用这条约定（``D`` 之后若还有 read 碱基，就取它的质量；
  否则退到前一个碱基的质量）。所以 :class:`DeletionObservation` 也带 ``quality``。

**规模说明（重要）**：本实现把每个参考位置的观测**收集在内存里**再按位置排序输出，
因此只适合小规模验证与对拍。细菌基因组在 100× 覆盖下，Python 里这条路径是几百 MB 到 GB
量级——真实数据必须换成流式（按参考位置顺序、边读边聚）或原生实现，记在文档的已知限制里。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ...common.alignment_io import SamRecord, open_sam
from ...common.reference_io import ReferenceSet
from .trimming import ReferenceTrimmer

__all__ = [
    "BaseObservation",
    "DeletionObservation",
    "InsertionObservation",
    "PileupColumn",
    "iter_pileup",
]

#: SAM 质量串的偏移（Phred+33）：``!`` 是 Q0。
_QUALITY_OFFSET = 33


def _decode_qualities(text: str, length: int, default: int) -> tuple[int, ...]:
    """把 SAM 的 ``QUAL`` 文本解成整数质量；``*``（无质量）时全部用 ``default``。"""
    if text == "*":
        return (default,) * length
    return tuple(max(0, ord(character) - _QUALITY_OFFSET) for character in text)


@dataclass(frozen=True, slots=True)
class BaseObservation:
    """一条 read 在某个参考位置上留下的**一个碱基**观测（CIGAR ``M``/``=``/``X``）。

    ``trimmed`` 表示这个碱基落在 read 末端被**裁剪**的那一段里（见 `trimming.py`）：
    它仍然被记下来（便于解释"这里覆盖度为什么低"），但共识判定与错误率表都会跳过它。
    """

    query_name: str
    base: str
    quality: int
    mapq: int
    is_reverse: bool
    read_offset: int
    read_length: int
    trimmed: bool = False

    @property
    def is_ambiguous(self) -> bool:
        """是否不是一个能判读的碱基（``N`` 等）——共识调用会把这些观测排除在外。"""
        return self.base not in "ACGT"


@dataclass(frozen=True, slots=True)
class DeletionObservation:
    """一条 read 在某个参考位置上**没有碱基**（CIGAR ``D``）——缺失证据。"""

    query_name: str
    length: int
    quality: int
    mapq: int
    is_reverse: bool
    read_offset: int
    read_length: int
    #: 缺失所在的 read 位置是否落在被裁剪的末端里（判定与重校准都会跳过它）。
    trimmed: bool = False


@dataclass(frozen=True, slots=True)
class InsertionObservation:
    """一条 read 在某个参考位置**之后**多出的碱基（CIGAR ``I``）——插入证据。

    插入没有 ``trimmed`` 标记：当前没有任何判定消费插入证据（见 `consensus.py` 只判碱基替换），
    等做小 indel 判定时再补，免得留一个没人看的字段。
    """

    query_name: str
    bases: str
    qualities: tuple[int, ...]
    mapq: int
    is_reverse: bool
    read_offset: int
    read_length: int


@dataclass(frozen=True, slots=True)
class PileupColumn:
    """某个参考位置上的全部证据（0-based 坐标）。"""

    seq_id: str
    position: int
    reference_base: str
    bases: tuple[BaseObservation, ...] = ()
    deletions: tuple[DeletionObservation, ...] = ()
    insertions: tuple[InsertionObservation, ...] = ()

    @property
    def coverage(self) -> int:
        """覆盖该位置的碱基观测数（不含缺失证据）。"""
        return len(self.bases)

    @property
    def forward_coverage(self) -> int:
        """来自正链的碱基观测数。"""
        return sum(1 for observation in self.bases if not observation.is_reverse)

    @property
    def reverse_coverage(self) -> int:
        """来自负链的碱基观测数。"""
        return sum(1 for observation in self.bases if observation.is_reverse)

    def counts(self) -> dict[str, int]:
        """各碱基的观测数（含 ``N`` 这类非 ACGT 的读段碱基）。"""
        counts: dict[str, int] = {}
        for observation in self.bases:
            counts[observation.base] = counts.get(observation.base, 0) + 1
        return counts


@dataclass(slots=True)
class _ColumnBuilder:
    """收集过程中的可变容器（对外只暴露不可变的 :class:`PileupColumn`）。"""

    seq_id: str
    position: int
    reference_base: str
    bases: list[BaseObservation] = field(default_factory=list)
    deletions: list[DeletionObservation] = field(default_factory=list)
    insertions: list[InsertionObservation] = field(default_factory=list)

    def build(self) -> PileupColumn:
        return PileupColumn(
            seq_id=self.seq_id,
            position=self.position,
            reference_base=self.reference_base,
            bases=tuple(self.bases),
            deletions=tuple(self.deletions),
            insertions=tuple(self.insertions),
        )


def _iter_records(source: str | Path | Iterable[SamRecord]) -> Iterator[SamRecord]:
    """统一两种输入：SAM 文件路径（明文或 gzip）或已经读好的记录序列。"""
    if isinstance(source, (str, Path)):
        with open_sam(source) as reader:
            yield from reader
        return
    yield from source


def _aligned_extent(
    cigar, position: int
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """从 CIGAR 找出第一个与最后一个**参与比对的碱基**在参考与 read 上的坐标。

    返回 ``((参考坐标, read 下标), (参考坐标, read 下标))``；没有任何 ``M`` 时两者都是 ``None``。
    ``D``/``N`` 只推进参考、不改变"最后一个比对碱基"，所以它们落在末尾时不会把右端坐标带偏。
    """
    reference = position  # 0-based
    query = 0
    first: tuple[int, int] | None = None
    last: tuple[int, int] | None = None
    for op in cigar.ops:
        kind, span = op.op, op.length
        if kind in ("M", "=", "X"):
            if first is None:
                first = (reference, query)
            last = (reference + span - 1, query + span - 1)
            reference += span
            query += span
        elif kind == "I":
            query += span
        elif kind in ("D", "N"):
            reference += span
        elif kind == "S":
            query += span
        # H / P 两侧都不消费
    return first, last


def iter_pileup(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    default_quality: int = 0,
    trimming: Mapping[str, ReferenceTrimmer] | None = None,
) -> Iterator[PileupColumn]:
    """把比对结果摊成逐参考位置的堆叠，按（参考名、位置）升序输出。

    只输出**有过证据**的位置（被碱基覆盖、或有缺失/插入），没有证据的位置不产出空列——
    这样调用方不必遍历整条参考。

    参数：
        reference: 参考集合（用来取参考碱基；参考名不在其中的记录会直接报错）。
        source: SAM 文件路径，或一堆 :class:`SamRecord`。
        default_quality: ``QUAL`` 为 ``*``（没有质量）时使用的质量。默认 **0**，
            也就是"当最差的碱基"——没有质量信息时这是最保守的假设，而不是假装它很好。
        trimming: 由 :func:`build_trimming` 建出的裁剪表。给了它就按它给每条 read 的两端打
            ``trimmed`` 标记（落在被裁末端里的碱基仍然记录，但下游会跳过）；``None`` 表示不裁剪。
    """
    if default_quality < 0:
        raise ValueError(f"默认质量不能为负，当前为 {default_quality}。")
    columns: dict[tuple[str, int], _ColumnBuilder] = {}

    def column(seq_id: str, position: int, reference_base: str) -> _ColumnBuilder:
        key = (seq_id, position)
        existing = columns.get(key)
        if existing is None:
            existing = _ColumnBuilder(
                seq_id=seq_id, position=position, reference_base=reference_base
            )
            columns[key] = existing
        return existing

    for record in _iter_records(source):
        if record.is_unmapped or record.sequence == "*":
            continue  # 没比上的、或没有序列的记录给不出任何位置证据
        seq_id = record.reference_name
        reference_sequence = reference.get(seq_id).sequence
        sequence = record.sequence
        length = len(sequence)
        if record.cigar.is_empty:
            continue  # 已比对却没有 CIGAR：无从判断位置对应关系，跳过而不是瞎猜
        qualities = _decode_qualities(record.qualities, length, default_quality)
        read_offset = 0  # 已比对碱基在 SEQ 里的下标（SEQ 与参考同向）
        position = record.position - 1  # 0-based 参考坐标
        # read 端裁剪：先看这条 read 的比对两端落在参考的哪个位置，据此得到"被裁掉的 read 区间"
        # [0, trimmed_until) ∪ (trimmed_from, length)。默认两个边界都不裁。
        trimmed_until, trimmed_from = 0, length - 1
        if trimming is not None:
            trimmer = trimming.get(seq_id)
            if trimmer is not None:
                first, last = _aligned_extent(record.cigar, position)
                if first is not None and last is not None:
                    trimmed_until = first[1] + trimmer.left_trim(first[0])
                    trimmed_from = last[1] - trimmer.right_trim(last[0])
        previous_position: int | None = None
        for op in record.cigar.ops:
            kind, span = op.op, op.length
            if kind in ("M", "=", "X"):
                for step in range(span):
                    index = read_offset + step
                    column(seq_id, position + step, reference_sequence[position + step]).bases.append(
                        BaseObservation(
                            query_name=record.query_name,
                            base=sequence[index],
                            quality=qualities[index],
                            mapq=record.mapping_quality,
                            is_reverse=record.is_reverse,
                            read_offset=index,
                            read_length=length,
                            trimmed=index < trimmed_until or index > trimmed_from,
                        )
                    )
                previous_position = position + span - 1
                position += span
                read_offset += span
            elif kind == "I":
                if previous_position is not None:
                    column(seq_id, previous_position, reference_sequence[previous_position]).insertions.append(
                        InsertionObservation(
                            query_name=record.query_name,
                            bases=sequence[read_offset : read_offset + span],
                            qualities=qualities[read_offset : read_offset + span],
                            mapq=record.mapping_quality,
                            is_reverse=record.is_reverse,
                            read_offset=read_offset,
                            read_length=length,
                        )
                    )
                read_offset += span
            elif kind == "D":
                # 缺失本身没有质量，用 read 里紧随其后的那个碱基的质量代表它（上游的约定）。
                if read_offset < length:
                    deletion_quality = qualities[read_offset]
                elif read_offset:
                    deletion_quality = qualities[read_offset - 1]
                else:  # pragma: no cover - 空 read 已被前面拦掉
                    deletion_quality = default_quality
                for step in range(span):
                    column(seq_id, position + step, reference_sequence[position + step]).deletions.append(
                        DeletionObservation(
                            query_name=record.query_name,
                            length=span,
                            quality=deletion_quality,
                            mapq=record.mapping_quality,
                            is_reverse=record.is_reverse,
                            read_offset=read_offset,
                            read_length=length,
                            trimmed=read_offset < trimmed_until or read_offset > trimmed_from,
                        )
                    )
                previous_position = position + span - 1
                position += span
            elif kind == "S":
                read_offset += span  # 软剪裁的碱基还在 SEQ 里，只是没比上——不产生证据
            elif kind == "N":
                previous_position = None  # 跳过的参考区不承载证据，也不做插入挂靠
                position += span
            elif kind in ("H", "P"):
                continue
            else:  # pragma: no cover - alignment_io 只允许八种操作，这里兜底
                raise ValueError(f"堆叠遇到不支持的 CIGAR 操作：{kind!r}。")

    for key in sorted(columns):
        yield columns[key].build()
