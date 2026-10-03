"""参考资料的数据模型：参考序列、特征（feature）与位置（location）。

这一层只回答"参考长什么样"——序列、坐标、特征及其注释。它**不做任何算法判断**：
不判定突变、不筛选特征类型、不解释基因影响；那些属于上层的比对与注释模块。

坐标约定
--------

**1-based、闭区间**，与 GenBank / IGV / samtools 一致（不是 Python 的 0-based 半开区间）。
横跨复制原点的特征按 GenBank 自己的写法用 ``join`` 表达，本层**不额外发明回绕语义**——
``subsequence`` 越界一律报错，而不是偷偷绕回。

位置写法
--------

覆盖 GenBank 的常见子集：``123``、``123..456``、``<123..456``、``123..>456``、
``complement(...)``、``join(...)``、``order(...)``，后三者可嵌套。

``complement(join(a,b))`` 按 NCBI 规范与 Biopython 的口径解释为**先拼接、再整体反向互补**：
实现上是"把各段的链翻转、并把段序颠倒"，于是按段取出再拼接的结果等于
``反向互补(a + b)``。这一点有专门的测试钉住（见 ``tests/test_model.py``），
因为它极易写成"逐段反向互补但顺序不动"——那是错的。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from ..sequences import reverse_complement

__all__ = [
    "Feature",
    "Location",
    "Part",
    "ReferenceFormatError",
    "ReferenceSequence",
    "ReferenceSet",
]


class ReferenceFormatError(ValueError):
    """参考资料无法解析：格式不合法、位置写法不支持、坐标越界或与声明不符。"""


@dataclass(frozen=True, slots=True)
class Part:
    """位置里的一个连续区间（1-based 闭区间）。

    ``partial`` 记录该段的端点是否带 ``<`` / ``>`` 标记。GenBank 用它们表示
    "序列只测到这里"；对注释而言这两者不影响坐标计算，所以本层只记下"端点是部分的"，
    不区分是哪一端。
    """

    start: int
    end: int
    strand: int = 1
    partial: bool = False

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ReferenceFormatError(f"区间起点必须 ≥ 1，当前为 {self.start}。")
        if self.end < self.start:
            raise ReferenceFormatError(f"区间终点 {self.end} 小于起点 {self.start}。")
        if self.strand not in (1, -1):
            raise ReferenceFormatError(f"链方向只能是 +1 或 -1，当前为 {self.strand}。")

    @property
    def length(self) -> int:
        """区间长度（碱基数）。"""
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class Location:
    """一个特征在参考上的位置：一段或多段，按**生物学顺序**排列。

    ``operator`` 取 ``"single"``（单段）、``"join"``（按顺序拼接）或 ``"order"``
    （顺序不确定的拼接，GenBank 用它表达"元素顺序未定"）。``raw`` 保留原始写法，
    报错与排障时回显用。
    """

    parts: tuple[Part, ...]
    operator: str = "join"
    raw: str = ""

    def __post_init__(self) -> None:
        if not self.parts:
            raise ReferenceFormatError(f"位置 {self.raw or '<空>'} 不含任何区间。")
        if self.operator not in ("single", "join", "order"):
            raise ReferenceFormatError(f"不支持的位置运算符：{self.operator!r}。")

    @property
    def start(self) -> int:
        """最左坐标（各段起点的最小值）。"""
        return min(part.start for part in self.parts)

    @property
    def end(self) -> int:
        """最右坐标（各段终点的最大值）。"""
        return max(part.end for part in self.parts)

    @property
    def length(self) -> int:
        """各段长度之和（拼接后的序列长度）。"""
        return sum(part.length for part in self.parts)

    @property
    def strand(self) -> int:
        """整体的链方向。

        各段链不一致时**报错**而不是猜一个——混合链的 ``join`` 是合法的 GenBank 写法，
        但"这个特征的链是什么"这个问题本身没有答案，调用方应当按段处理。
        """
        strands = {part.strand for part in self.parts}
        if len(strands) != 1:
            raise ReferenceFormatError(
                f"位置 {self.raw or self.operator} 的各段链方向不一致，请按段取用。"
            )
        return strands.pop()

    @property
    def mixed_strand(self) -> bool:
        """各段链方向是否不一致。"""
        return len({part.strand for part in self.parts}) > 1

    @property
    def complete(self) -> bool:
        """所有端点都不是部分序列（没有 ``<`` / ``>`` 标记）。"""
        return all(not part.partial for part in self.parts)

    def spans(self) -> tuple[tuple[int, int], ...]:
        """各段的 ``(起点, 终点)``，按生物学顺序。"""
        return tuple((part.start, part.end) for part in self.parts)

    def overlaps(self, start: int, end: int) -> bool:
        """是否与 ``[start, end]``（1-based 闭区间）有任何重叠。"""
        return any(part.start <= end and start <= part.end for part in self.parts)

    def extract(self, sequence: str) -> str:
        """按位置从 ``sequence`` 里取出序列。

        每段按自己的链方向取（负链取反向互补），再按顺序拼接。
        """
        if not sequence:
            raise ReferenceFormatError("参考资料序列为空，无法按位置取序列。")
        pieces: list[str] = []
        for part in self.parts:
            if part.end > len(sequence):
                raise ReferenceFormatError(
                    f"位置 {self.raw or self.operator} 的区间 {part.start}..{part.end} "
                    f"超出参考长度 {len(sequence)}。"
                )
            piece = sequence[part.start - 1 : part.end]
            pieces.append(piece if part.strand == 1 else reverse_complement(piece))
        return "".join(pieces)


@dataclass(frozen=True, slots=True)
class Feature:
    """参考上的一个特征（基因、rRNA、重复区、移动元件……）。

    ``qualifiers`` 的值是**元组**（同一个限定符可以出现多次，例如多个 ``/note``），
    顺序与文件里一致。``index`` 是该特征在文件里的出现序号，用于稳定排序与排障。
    """

    kind: str
    location: Location
    qualifiers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    index: int = 0

    def values(self, key: str) -> tuple[str, ...]:
        """某个限定符的全部取值。"""
        return tuple(self.qualifiers.get(key, ()))

    def value(self, key: str, default: str = "") -> str:
        """某个限定符的第一个取值（没有就返回 ``default``）。"""
        values = self.qualifiers.get(key)
        return values[0] if values else default

    @property
    def gene(self) -> str:
        """基因名：优先 ``/gene``，退到 ``/locus_tag``，都没有则为空串。"""
        return self.value("gene") or self.value("locus_tag")

    @property
    def product(self) -> str:
        """``/product``（产物描述），没有则为空串。"""
        return self.value("product")

    @property
    def pseudo(self) -> bool:
        """是否伪基因（``/pseudo`` 存在即真，空值也算）。"""
        return "pseudo" in self.qualifiers

    def __str__(self) -> str:  # pragma: no cover - 仅用于排障与报错信息
        return f"{self.kind}#{self.index} {self.location.raw or self.location.operator}"


@dataclass(frozen=True, slots=True)
class ReferenceSequence:
    """一条参考序列：序列本身 + 拓扑 + 特征表。

    不变式（构造时校验，解析器负责把数据整理成这样）：

    - ``sequence`` **已统一大写**且**不含空白**；
    - ``seq_id`` 非空；
    - 所有特征都落在序列范围内（越界说明文件被截断或坐标写错，宁可早报错）。
    """

    seq_id: str
    sequence: str
    features: tuple[Feature, ...] = ()
    circular: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.seq_id:
            raise ReferenceFormatError("参考序列缺少 SEQ_ID。")
        if not self.sequence:
            raise ReferenceFormatError(f"参考序列 {self.seq_id} 的序列为空。")
        if any(character.isspace() for character in self.sequence):
            raise ReferenceFormatError(f"参考序列 {self.seq_id} 的序列里含空白字符。")
        if self.sequence != self.sequence.upper():
            raise ReferenceFormatError(f"参考序列 {self.seq_id} 的序列必须是大写。")
        for feature in self.features:
            if feature.location.end > len(self.sequence):
                raise ReferenceFormatError(
                    f"参考序列 {self.seq_id} 的特征 {feature} 超出序列长度 "
                    f"{len(self.sequence)}。"
                )

    @property
    def length(self) -> int:
        """序列长度（碱基数）。"""
        return len(self.sequence)

    def subsequence(self, start: int, end: int) -> str:
        """取 ``[start, end]``（1-based 闭区间）。

        **不处理跨原点回绕**：越界直接报错。GenBank 对跨原点特征用的是
        ``join(...,1..n)`` 写法，本层沿用同一约定，避免"看起来能跑、其实序列是错的"。
        """
        if start < 1 or end < start or end > len(self.sequence):
            raise ReferenceFormatError(
                f"参考序列 {self.seq_id} 上取子序列 {start}..{end} 越界"
                f"（长度 {len(self.sequence)}）。"
            )
        return self.sequence[start - 1 : end]

    def extract(self, location: Location) -> str:
        """按位置取序列（负链自动反向互补）。"""
        return location.extract(self.sequence)

    def features_of(self, *kinds: str) -> tuple[Feature, ...]:
        """按特征类型筛选（不传即返回全部）。类型按原样比较，不做大小写折叠。"""
        if not kinds:
            return self.features
        wanted = set(kinds)
        return tuple(feature for feature in self.features if feature.kind in wanted)


@dataclass(frozen=True, slots=True)
class ReferenceSet:
    """一次分析用到的全部参考序列（染色体 + 质粒，或只有一条）。

    breseq 的 E-value 用的是**参考总长度**，所以这里提供 :attr:`total_length`——
    它是"所有参考序列的碱基数之和"，不是某一条的长度。
    """

    sequences: tuple[ReferenceSequence, ...]

    def __post_init__(self) -> None:
        if not self.sequences:
            raise ReferenceFormatError("参考资料里没有任何序列。")
        seen: set[str] = set()
        for sequence in self.sequences:
            if sequence.seq_id in seen:
                raise ReferenceFormatError(f"参考资料里的 SEQ_ID 重复：{sequence.seq_id}。")
            seen.add(sequence.seq_id)

    def __len__(self) -> int:
        return len(self.sequences)

    def __iter__(self) -> Iterator[ReferenceSequence]:
        return iter(self.sequences)

    def __contains__(self, seq_id: object) -> bool:
        return any(sequence.seq_id == seq_id for sequence in self.sequences)

    @property
    def ids(self) -> tuple[str, ...]:
        """各条序列的 SEQ_ID，按文件顺序。"""
        return tuple(sequence.seq_id for sequence in self.sequences)

    @property
    def total_length(self) -> int:
        """参考总长度（碱基数之和）。"""
        return sum(sequence.length for sequence in self.sequences)

    @property
    def feature_count(self) -> int:
        """全部特征数。"""
        return sum(len(sequence.features) for sequence in self.sequences)

    def get(self, seq_id: str) -> ReferenceSequence:
        """按 SEQ_ID 取一条参考序列，取不到就报错。"""
        for sequence in self.sequences:
            if sequence.seq_id == seq_id:
                return sequence
        raise ReferenceFormatError(
            f"参考资料里没有 SEQ_ID 为 {seq_id!r} 的序列，现有：{'、'.join(self.ids)}。"
        )

    def features_of(self, *kinds: str) -> tuple[Feature, ...]:
        """跨全部序列按类型筛选特征。"""
        collected: list[Feature] = []
        for sequence in self.sequences:
            collected.extend(sequence.features_of(*kinds))
        return tuple(collected)

    @classmethod
    def of(cls, sequences: Iterable[ReferenceSequence]) -> ReferenceSet:
        """由若干条参考序列构造（便于测试与拼接多来源数据）。"""
        return cls(tuple(sequences))
