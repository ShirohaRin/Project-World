"""把突变应用到参考序列（分片 C2，对应上游 ``gdtools APPLY``）。

用途：把某一代样本的突变"贴"回参考基因组，得到那一代的基因组——祖先重建、检验突变效应、
拿它当新参考重新比对，都要这一步。

支持三类（也就是我们自己证据线会产出的三类）：

| 类型 | 语义 | 结果 |
| --- | --- | --- |
| ``SNP`` | 位置 ``p`` 的碱基换成记录里的新碱基 | 长度不变 |
| ``DEL`` | 从 ``p`` 起删掉 ``length`` 个碱基（**含 ``p`` 自己**） | 长度 −``length`` |
| ``INS`` | 在 ``p`` **之后**插入记录里的碱基（即插在 ``p`` 与 ``p+1`` 之间） | 长度 +插入长度 |

``DEL`` 从 ``p`` 开始这一条有旁证：真实产物里 `DEL 2 … 701810 1`（Δ1 bp）对同目录 html 的
那一行，而它的证据 `RA 35 … 701810 0 A .` 的参考碱基正是 ``A``——被删掉的就是 701810 这个碱基。
``INS`` 的"插在 ``p`` 之后"是**我们的读法**，没有逐条核过（见文档的待办）。

两个实现要点：

1. **按坐标从后往前应用**。先改坐标大的、再改坐标小的，前面那些突变的坐标就不会被后面的改动
   影响——否则要一边改一边重算所有坐标，容易错。
2. **"应用后的位置"是按前缀和算出来的**，不是边改边记：每条突变先算出它对长度的增减
   （``SNP`` 0、``DEL`` −length、``INS`` +插入长度），则位置 ``p`` 在新序列里的坐标 =
   ``p + 所有坐标更小的突变的长度增减之和``；``INS`` 再 +1（插进去的那一段从 ``p`` 的下一位开始）。

**不支持跨过参考末端的缺失**（环状参考上跨复制原点的缺失是合法突变）：我们的参考读取分得清
环状，但应用这一版没做环绕，遇到就越界报错并说明原因——**不静默截断**。``MOB`` / ``AMP`` /
``CON`` / ``INV`` 也不支持：它们不能靠"改一段序列"实现（要动整条序列的结构），报错说明。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .model import GenomeDiff, GenomeDiffRecord

__all__ = [
    "SUPPORTED_TYPES",
    "AppliedGenome",
    "AppliedMutation",
    "apply_diff",
    "apply_mutations",
]

#: 能应用的类型。
SUPPORTED_TYPES = ("SNP", "DEL", "INS")

_BASES = frozenset("ACGT")
_INSERTED_BASES = frozenset("ACGTN")


@dataclass(frozen=True, slots=True)
class AppliedMutation:
    """一条突变应用之后的情况。

    ``position`` 是它**在新序列里**的坐标（1-based）：``SNP`` 与 ``DEL`` 是那个位置本身，
    ``INS`` 是插入进去的第一个碱基的位置。``shift`` 是新坐标减原坐标（前面有缺失就会是负的）。
    """

    record: GenomeDiffRecord
    position: int
    shift: int


@dataclass(frozen=True, slots=True)
class AppliedGenome:
    """应用完一份突变之后的基因组。"""

    seq_id: str
    sequence: str
    mutations: tuple[AppliedMutation, ...] = ()

    @property
    def length(self) -> int:
        return len(self.sequence)


def _length_change(record: GenomeDiffRecord) -> int:
    """这条突变让序列长了几（负的就是短了几）。"""
    if record.type == "SNP":
        return 0
    if record.type == "DEL":
        return -_deletion_length(record)
    return len(_inserted_bases(record))


def _deletion_length(record: GenomeDiffRecord) -> int:
    if not record.fields:
        raise ValueError(f"缺失记录 {record.id!r} 没有长度字段，无法应用。")
    try:
        length = int(record.fields[0])
    except ValueError as error:
        raise ValueError(
            f"缺失记录 {record.id!r} 的长度不是整数：{record.fields[0]!r}。"
        ) from error
    if length < 1:
        raise ValueError(f"缺失记录 {record.id!r} 的长度必须 ≥ 1，当前为 {length}。")
    return length


def _inserted_bases(record: GenomeDiffRecord) -> str:
    if not record.fields or not record.fields[0]:
        raise ValueError(f"插入记录 {record.id!r} 没有插入序列，无法应用。")
    inserted = record.fields[0].upper()
    if set(inserted) - _INSERTED_BASES:
        raise ValueError(
            f"插入记录 {record.id!r} 的序列含 ACGTN 之外的字符：{record.fields[0]!r}。"
        )
    return inserted


def _check_supported(record: GenomeDiffRecord) -> None:
    if record.is_evidence:
        raise ValueError(f"记录 {record.id!r} 是证据行（{record.type}），不该拿来应用。")
    if record.type not in SUPPORTED_TYPES:
        raise ValueError(
            f"暂不支持应用 {record.type} 类型的突变（记录 {record.id!r}）；"
            f"目前只支持 {'、'.join(SUPPORTED_TYPES)}。"
        )


def apply_mutations(
    sequence: str,
    mutations: Iterable[GenomeDiffRecord],
    *,
    seq_id: str = "",
) -> AppliedGenome:
    """把一组突变应用到一条序列上，返回新序列与每条突变的新位置。

    同位置的多个突变按传入顺序依次应用；位移只累加坐标**严格更小**的突变。所有突变先整体校验，
    有一条越界或类型不支持就整体报错——不做出"一半应用了"的结果。
    """
    text = sequence.upper()
    ordered = tuple(sorted(mutations, key=lambda record: record.position))

    changes: list[int] = []
    for record in ordered:
        _check_supported(record)
        length = len(text)
        if record.type == "SNP":
            if len(record.fields) < 1 or record.fields[0] not in _BASES:
                raise ValueError(
                    f"替换记录 {record.id!r} 的新碱基不合法：{record.fields[:1]!r}。"
                )
        elif record.type == "DEL":
            deletion = _deletion_length(record)
            if record.position + deletion - 1 > length:
                raise ValueError(
                    f"缺失记录 {record.id!r} 从 {record.position} 删 {deletion} 个碱基会越过"
                    f"参考末端（长度 {length}）；环状参考上跨复制原点的缺失本版本不支持。"
                )
        else:
            _inserted_bases(record)
        if not 1 <= record.position <= length:
            raise ValueError(
                f"记录 {record.id!r} 的位置 {record.position} 超出序列长度 {length}。"
            )
        changes.append(_length_change(record))

    # 前缀和：坐标更小的突变一共让序列长了多少
    shift_before: list[int] = []
    running = 0
    for index, record in enumerate(ordered):
        shift_before.append(running)
        running += changes[index]

    applied = list(text)
    # 从后往前改序列（坐标大的先改，前面那些突变的坐标就不会被影响）；
    # 同位置的多个突变按传入顺序依次应用，所以同一位置内按输入次序排。
    apply_order = sorted(
        range(len(ordered)), key=lambda index: (-ordered[index].position, index)
    )
    for index in apply_order:
        record = ordered[index]
        position = record.position
        if record.type == "SNP":
            applied[position - 1] = record.fields[0]
        elif record.type == "DEL":
            del applied[position - 1 : position - 1 + _deletion_length(record)]
        else:
            applied[position:position] = _inserted_bases(record)

    results = []
    for index, record in enumerate(ordered):
        shift = shift_before[index]
        new_position = record.position + shift
        if record.type == "INS":
            new_position += 1  # 插进去的那一段从原位置的下一位开始
        results.append(AppliedMutation(record=record, position=new_position, shift=shift))

    return AppliedGenome(seq_id=seq_id, sequence="".join(applied), mutations=tuple(results))


def apply_diff(
    references: Mapping[str, str], diff: GenomeDiff
) -> tuple[AppliedGenome, ...]:
    """把一份 `.gd` 应用到若干条参考序列上。

    ``references`` 是"参考名 → 序列"。**集合里每条序列都会返回**（没被突变碰到的照样返回，
    序列不变）——这样调用方直接拿去写新的参考文件即可。突变指向了不在集合里的参考名时**报错**，
    免得那些突变被静默丢掉。
    """
    mutations_by_seq: dict[str, list[GenomeDiffRecord]] = {name: [] for name in references}
    for record in diff.mutations():
        if record.seq_id not in mutations_by_seq:
            raise ValueError(
                f"记录 {record.id!r} 指向参考 {record.seq_id!r}，但它不在给定的参考集合里。"
            )
        mutations_by_seq[record.seq_id].append(record)

    return tuple(
        apply_mutations(sequence, mutations_by_seq[name], seq_id=name)
        for name, sequence in references.items()
    )
