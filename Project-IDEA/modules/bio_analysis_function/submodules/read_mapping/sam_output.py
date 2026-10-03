"""把比对结果写成 SAM（分片 E）。

这一层只做**转换与落盘**：`Alignment` → `SamRecord` → 文件。格式的读写与校验都在公共层
`common/alignment_io`，这里不重复实现，也不做任何比对判定。

三条必须照 SAM 规矩来的地方：

1. **负链记录里的序列要反向互补**（SAM 规范：``SEQ`` 存的是与参考同向的那条），
   质量串随之**反向**。我们的 :class:`Alignment` 也是这个口径，所以两边一致。
2. **坐标要换成 1-based**：内部一律 0-based，写 SAM 时 ``POS = reference_start + 1``。
3. **只写比对上的记录**（与 bowtie2 的默认行为一致）：未比对的 read 不进 SAM，
   上游 breseq 也是另存 ``*.unmatched.fastq``。要统计未比对条数，看返回的计数。

随记录写两个标签：

- ``NM:i:`` 编辑距离（错配 + 插入碱基 + 缺失碱基）——下游过滤与对拍都靠它；
- ``MD:Z:`` 错配位置描述串（匹配数、参考碱基、``^`` 接被删的参考碱基），
  IGV 等工具用它来显示差异，也便于人工核对。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from ...common.alignment_io import SamHeader, SamRecord, SamWriter
from ...common.reference_io import ReferenceSet
from ...common.sequences import reverse_complement
from .mapper import Alignment, Read

__all__ = ["build_md_tag", "to_sam_record", "write_mapped_sam"]

#: FLAG 位（与 SAM 规范一致）。
_FLAG_PAIRED = 0x1
_FLAG_UNMAPPED = 0x4
_FLAG_REVERSE = 0x10
_FLAG_FIRST = 0x40
_FLAG_LAST = 0x80


def build_md_tag(alignment: Alignment, query: str, reference: str) -> str:
    """按 CIGAR 生成 ``MD`` 串。

    规则（SAM 规范）：匹配的碱基数直接写数字；遇到错配先写已匹配的个数、再写**参考碱基**；
    缺失写成 ``^`` 加被删掉的参考碱基；插入与剪裁**不进** ``MD``（它们不占参考位置）。

    参数：
        alignment: 一条比对结果（用它的 CIGAR 与 0-based 起点）。
        query: **与参考同向**的读段序列（负链要先反向互补）。
        reference: 参考序列全文（用它切片取碱基）。
    """
    pieces: list[str] = []
    matches = 0
    query_index = 0
    reference_index = alignment.reference_start
    for op in alignment.cigar.ops:
        if op.op == "M":
            for _ in range(op.length):
                if query[query_index] == reference[reference_index]:
                    matches += 1
                else:
                    pieces.append(str(matches))
                    pieces.append(reference[reference_index])
                    matches = 0
                query_index += 1
                reference_index += 1
        elif op.op == "I" or op.op == "S":
            query_index += op.length
        elif op.op == "D":
            pieces.append(str(matches))
            pieces.append("^" + reference[reference_index : reference_index + op.length])
            matches = 0
            reference_index += op.length
        else:  # pragma: no cover - 比对结果里只会出现 M/I/D/S
            raise ValueError(f"比对结果里出现了不支持的 CIGAR 操作：{op.op!r}。")
    pieces.append(str(matches))
    return "".join(pieces)


def to_sam_record(
    read: Read,
    alignment: Alignment,
    reference: ReferenceSet,
    *,
    paired: bool = False,
) -> SamRecord:
    """把一条比对结果转成 SAM 记录。

    参数：
        read: 原始读段（提供名字、序列、质量）。
        alignment: 比对结果。
        reference: 参考集合（用于取 ``MD`` 需要的参考碱基）。
        paired: 是否按双端记录写（写 ``0x1`` 与 ``0x40/0x80``）。
            我们的比对本身是单端的（照上游口径，不用配对信息），这里只是如实标注来源。
    """
    reference_sequence = reference.get(alignment.seq_id).sequence
    if alignment.strand == -1:
        sequence = reverse_complement(read.sequence)
        qualities = read.qualities[::-1] if read.qualities else ""
    else:
        sequence = read.sequence
        qualities = read.qualities
    flag = 0
    if paired:
        flag |= _FLAG_PAIRED | (_FLAG_LAST if read.is_read2 else _FLAG_FIRST)
    if alignment.strand == -1:
        flag |= _FLAG_REVERSE
    return SamRecord(
        query_name=read.name,
        flag=flag,
        reference_name=alignment.seq_id,
        position=alignment.reference_start + 1,  # SAM 是 1-based
        mapping_quality=alignment.mapq,
        cigar=alignment.cigar,
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence=sequence,
        qualities=qualities or "*",
        tags=(
            ("NM", "i", str(alignment.edit_distance)),
            ("MD", "Z", build_md_tag(alignment, sequence, reference_sequence)),
        ),
    )


def iter_sam_records(
    reads: Iterable[Read],
    alignments: Iterable[Alignment | None],
    reference: ReferenceSet,
    *,
    paired: bool = False,
) -> Iterator[SamRecord]:
    """把一对"读段 / 比对结果"（顺序一一对应）逐个转成 SAM 记录，跳过未比对的。"""
    for read, alignment in zip(reads, alignments):
        if alignment is None:
            continue
        yield to_sam_record(read, alignment, reference, paired=paired)


def write_mapped_sam(
    path: str | Path,
    reference: ReferenceSet,
    reads: Iterable[Read],
    alignments: Iterable[Alignment | None],
    *,
    paired: bool = False,
    compress: bool = False,
) -> int:
    """写出 SAM（只含比对上的记录），返回写出的记录条数。

    头部按参考集合生成（``@HD`` + 每条参考一行 ``@SQ``），可直接用 IGV 打开。
    """
    header = SamHeader.from_sequences(
        ((sequence.seq_id, sequence.length) for sequence in reference),
        sort_order="unknown",
    )
    written = 0
    with SamWriter(path, header, compress=compress) as writer:
        for record in iter_sam_records(reads, alignments, reference, paired=paired):
            writer.write(record)
            written += 1
    return written
