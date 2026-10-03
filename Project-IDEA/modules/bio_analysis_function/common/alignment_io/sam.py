"""SAM 文本比对记录的读写与 CIGAR 解析。

这是参考比对这条线的**对出契约**：我们的比对器写出 SAM，IGV 与其它工具能直接看；
反过来，别人（含上游 breseq 用 bowtie2 跑出来）的 SAM 也能读进来，
用同一套下游做**阶段级对拍**——比对器没法承诺与 bowtie2 逐位一致，
但"拿同一份比对结果往下走，变异调用是否一致"是可以验的。

本层只管格式，不做任何比对判定：不筛映射质量、不看编辑距离、不挑最佳命中。

**CIGAR 的消费关系**是这里最容易写错的地方，单独列清楚：

| 操作 | 含义 | 消费 query | 消费 reference |
| --- | --- | --- | --- |
| `M` | 比对（可能含错配） | 是 | 是 |
| `I` | 相对参考的插入 | 是 | 否 |
| `D` | 相对参考的缺失 | 否 | 是 |
| `N` | 跳过的参考区（如内含子） | 否 | 是 |
| `S` | 软剪裁（序列仍在） | 是 | 否 |
| `H` | 硬剪裁（序列已不在） | 否 | 否 |
| `P` | 填充 | 否 | 否 |
| `=` / `X` | 完全匹配 / 错配 | 是 | 是 |

由此，``SEQ`` 的长度必须等于**消费 query 的操作之和**（`H`/`P`/`D`/`N` 不算），
这条在构造记录时就校验——写错的 CIGAR 会让下游所有坐标错位，而且不报错。

**BAM 不在本层**。BAM 是同一套记录的二进制（BGZF + 紧凑编码），要单独做；
本层只处理文本 SAM（可 gzip，按魔数识别）。
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ..fastq import is_gzip

__all__ = [
    "AlignmentFormatError",
    "Cigar",
    "CigarOp",
    "SamHeader",
    "SamReader",
    "SamRecord",
    "SamSequence",
    "SamWriter",
    "open_sam",
    "parse_cigar",
    "parse_sam_record",
    "read_sam",
    "write_sam",
]

#: 各 CIGAR 操作是否消费 query / reference。
_CONSUMES_QUERY = frozenset("MIS=X")
_CONSUMES_REFERENCE = frozenset("MDN=X")
_CIGAR_OPERATIONS = frozenset("MIDNSHP=X")

#: SAM 的 FLAG 位（按规范命名）。
_FLAG_PAIRED = 0x1
_FLAG_PROPER_PAIR = 0x2
_FLAG_UNMAPPED = 0x4
_FLAG_MATE_UNMAPPED = 0x8
_FLAG_REVERSE = 0x10
_FLAG_MATE_REVERSE = 0x20
_FLAG_FIRST = 0x40
_FLAG_LAST = 0x80
_FLAG_SECONDARY = 0x100
_FLAG_QUALITY_FAIL = 0x200
_FLAG_DUPLICATE = 0x400
_FLAG_SUPPLEMENTARY = 0x800

_MANDATORY_FIELDS = 11


class AlignmentFormatError(ValueError):
    """比对记录无法解析：字段数不对、CIGAR 非法、长度与序列不符等。"""


@dataclass(frozen=True, slots=True)
class CigarOp:
    """一个 CIGAR 操作（长度 + 操作符）。长度必须 ≥ 1：SAM 里没有 ``0M`` 这种写法。"""

    length: int
    op: str

    def __post_init__(self) -> None:
        if self.op not in _CIGAR_OPERATIONS:
            raise AlignmentFormatError(f"未知的 CIGAR 操作：{self.op!r}。")
        if self.length < 1:
            raise AlignmentFormatError(f"CIGAR 操作长度必须 ≥ 1，当前为 {self.op}{self.length}。")

    def __str__(self) -> str:
        return f"{self.length}{self.op}"


@dataclass(frozen=True, slots=True)
class Cigar:
    """一条记录的 CIGAR。未比对的记录用空 CIGAR 表示（SAM 里写作 ``*``）。"""

    ops: tuple[CigarOp, ...] = ()
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        """是否没有 CIGAR（对应 SAM 的 ``*``）。"""
        return not self.ops

    @property
    def query_length(self) -> int:
        """消费 query 的碱基数——有 ``SEQ`` 时它必须等于 ``SEQ`` 的长度。"""
        return sum(op.length for op in self.ops if op.op in _CONSUMES_QUERY)

    @property
    def reference_length(self) -> int:
        """消费 reference 的碱基数（决定记录覆盖的参考区间）。"""
        return sum(op.length for op in self.ops if op.op in _CONSUMES_REFERENCE)

    @property
    def has_soft_clip(self) -> bool:
        """是否有软剪裁（序列还在，只是没比上）。"""
        return any(op.op == "S" for op in self.ops)

    @property
    def has_hard_clip(self) -> bool:
        """是否有硬剪裁（序列已经不在记录里）。"""
        return any(op.op == "H" for op in self.ops)

    @property
    def has_indel(self) -> bool:
        """是否有插入或缺失。"""
        return any(op.op in "ID" for op in self.ops)

    @property
    def has_skipped(self) -> bool:
        """是否有 ``N``（跳过的参考区）。"""
        return any(op.op == "N" for op in self.ops)

    @property
    def is_simple(self) -> bool:
        """是否只由 ``M`` / ``I`` / ``D`` 组成——没有剪裁、没有 ``N``、没有 ``=`` / ``X``。

        变异检测的常见前置：CIGAR 不简单时"这一位对应的参考坐标"要额外算，
        很多判定会先要求简单 CIGAR。
        """
        return all(op.op in "MID" for op in self.ops)

    def __str__(self) -> str:
        return "".join(str(op) for op in self.ops) if self.ops else "*"


def parse_cigar(text: str) -> Cigar:
    """解析 CIGAR 文本（``*`` 或空串表示没有 CIGAR）。"""
    token = text.strip()
    if token in ("", "*"):
        return Cigar(ops=(), raw=token)
    ops: list[CigarOp] = []
    digits: list[str] = []
    for character in token:
        if character.isdigit():
            digits.append(character)
            continue
        if not digits:
            raise AlignmentFormatError(f"CIGAR {token!r} 里的操作 {character!r} 缺少长度。")
        ops.append(CigarOp(length=int("".join(digits)), op=character))
        digits = []
    if digits:
        raise AlignmentFormatError(f"CIGAR {token!r} 末尾多出一段没有操作符的数字。")
    return Cigar(ops=tuple(ops), raw=token)


@dataclass(frozen=True, slots=True)
class SamSequence:
    """``@SQ`` 行：参考序列的名字与长度。"""

    name: str
    length: int

    def __post_init__(self) -> None:
        if not self.name:
            raise AlignmentFormatError("@SQ 行缺少 SN（参考序列名）。")
        if self.length < 1:
            raise AlignmentFormatError(f"@SQ 行 SN:{self.name} 的长度必须 ≥ 1。")


@dataclass(frozen=True, slots=True)
class SamHeader:
    """SAM 头部：原始 ``@`` 行 + 解析出来的参考序列表。

    保留原始行是为了**逐字节往返**：读进来再写出去，头部不会被重新排序或改写。
    """

    lines: tuple[str, ...] = ()
    sequences: tuple[SamSequence, ...] = ()

    @classmethod
    def parse(cls, lines: Iterable[str]) -> SamHeader:
        """从若干 ``@`` 行解析头部。"""
        kept: list[str] = []
        sequences: list[SamSequence] = []
        for line in lines:
            stripped = line.rstrip("\r\n")
            if not stripped.startswith("@"):
                raise AlignmentFormatError(f"头部行必须以 @ 开头：{stripped!r}")
            kept.append(stripped)
            if not stripped.startswith("@SQ"):
                continue
            fields = dict(
                token.split(":", 1)
                for token in stripped.split("\t")[1:]
                if ":" in token
            )
            name = fields.get("SN", "")
            length_text = fields.get("LN", "")
            if not length_text.isdigit():
                raise AlignmentFormatError(f"@SQ 行缺少合法的 LN：{stripped!r}")
            sequences.append(SamSequence(name=name, length=int(length_text)))
        return cls(lines=tuple(kept), sequences=tuple(sequences))

    @classmethod
    def from_sequences(
        cls, sequences: Iterable[tuple[str, int]], *, sort_order: str = "unknown"
    ) -> SamHeader:
        """按"名字 + 长度"造一个头部（比对器写出自己的结果时用）。

        只依赖 ``(name, length)`` 二元组，因此本模块不需要认识参考资料的数据结构。
        """
        pairs = tuple(sequences)
        sq_lines = tuple(
            f"@SQ\tSN:{name}\tLN:{length}" for name, length in pairs
        )
        return cls(
            lines=(f"@HD\tVN:1.6\tSO:{sort_order}",) + sq_lines,
            sequences=tuple(SamSequence(name=name, length=length) for name, length in pairs),
        )

    def reference_length(self, name: str) -> int | None:
        """某条参考序列的长度；不在头部里就返回 ``None``。"""
        for sequence in self.sequences:
            if sequence.name == name:
                return sequence.length
        return None

    def to_lines(self) -> tuple[str, ...]:
        """写回去用的行（原样）。"""
        return self.lines


@dataclass(frozen=True, slots=True)
class SamRecord:
    """一条比对记录（SAM 的 11 个必填字段 + 可选标签）。

    坐标一律沿用 SAM 的口径：``position`` 是 **1-based**，``0`` 表示未比对；
    ``mapping_quality`` 取 0~255，``255`` 表示"不可用"。
    """

    query_name: str
    flag: int
    reference_name: str
    position: int
    mapping_quality: int
    cigar: Cigar
    next_reference_name: str
    next_position: int
    template_length: int
    sequence: str
    qualities: str
    tags: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.flag <= 0xFFFF:
            raise AlignmentFormatError(f"FLAG 越界：{self.flag}。")
        if self.position < 0 or self.next_position < 0:
            raise AlignmentFormatError("POS / PNEXT 不能为负。")
        if not 0 <= self.mapping_quality <= 255:
            raise AlignmentFormatError(f"MAPQ 越界（0~255）：{self.mapping_quality}。")
        if self.is_unmapped:
            if self.reference_name != "*" and self.position < 1:
                raise AlignmentFormatError(
                    f"{self.query_name} 未比对却给了参考名与位置 {self.position}。"
                )
        elif self.reference_name == "*":
            raise AlignmentFormatError(f"{self.query_name} 已比对却没有参考名（RNAME 为 *）。")
        elif self.position < 1:
            raise AlignmentFormatError(
                f"{self.query_name} 已比对，POS 必须是 1-based（≥ 1），当前为 {self.position}。"
            )
        if self.sequence != "*":
            if any(character.isspace() for character in self.sequence):
                raise AlignmentFormatError(f"SEQ 里含空白：{self.query_name}。")
            if self.sequence != self.sequence.upper():
                raise AlignmentFormatError(f"SEQ 必须是大写：{self.query_name}。")
            if not self.cigar.is_empty and self.cigar.query_length != len(self.sequence):
                raise AlignmentFormatError(
                    f"{self.query_name} 的 SEQ 长度 {len(self.sequence)} 与 CIGAR "
                    f"{self.cigar} 消费的 query 长度 {self.cigar.query_length} 不符。"
                )
            if self.qualities != "*" and len(self.qualities) != len(self.sequence):
                raise AlignmentFormatError(
                    f"{self.query_name} 的 QUAL 长度 {len(self.qualities)} 与 SEQ "
                    f"长度 {len(self.sequence)} 不符。"
                )
        elif self.qualities != "*":
            raise AlignmentFormatError(f"{self.query_name} 的 SEQ 为 * 时 QUAL 也必须是 *。")

    # --- FLAG ---------------------------------------------------------------

    @property
    def is_paired(self) -> bool:
        """是否双端记录。"""
        return bool(self.flag & _FLAG_PAIRED)

    @property
    def is_proper_pair(self) -> bool:
        """是否"正确配对"。"""
        return bool(self.flag & _FLAG_PROPER_PAIR)

    @property
    def is_unmapped(self) -> bool:
        """本条是否未比对。"""
        return bool(self.flag & _FLAG_UNMAPPED)

    @property
    def is_mate_unmapped(self) -> bool:
        """对端是否未比对。"""
        return bool(self.flag & _FLAG_MATE_UNMAPPED)

    @property
    def is_reverse(self) -> bool:
        """本条是否比对到负链。"""
        return bool(self.flag & _FLAG_REVERSE)

    @property
    def is_read1(self) -> bool:
        """是否 read1。"""
        return bool(self.flag & _FLAG_FIRST)

    @property
    def is_read2(self) -> bool:
        """是否 read2。"""
        return bool(self.flag & _FLAG_LAST)

    @property
    def is_secondary(self) -> bool:
        """是否次级比对（同一 read 的非最佳命中）。"""
        return bool(self.flag & _FLAG_SECONDARY)

    @property
    def is_supplementary(self) -> bool:
        """是否补充比对（嵌合比对里被拆开的那一段）。"""
        return bool(self.flag & _FLAG_SUPPLEMENTARY)

    @property
    def is_duplicate(self) -> bool:
        """是否标记为重复。"""
        return bool(self.flag & _FLAG_DUPLICATE)

    @property
    def reference_end(self) -> int:
        """本条覆盖的参考区间右端（1-based 闭区间）；未比对时为 0。"""
        if self.is_unmapped or self.cigar.is_empty:
            return self.position if self.position else 0
        return self.position + self.cigar.reference_length - 1

    # --- 可选标签 -----------------------------------------------------------

    def tag(self, name: str) -> str | None:
        """取某个可选标签的值文本（不带类型前缀）；没有就返回 ``None``。"""
        for tag_name, _type, value in self.tags:
            if tag_name == name:
                return value
        return None

    def tag_int(self, name: str) -> int | None:
        """取某个可为整数的标签（``NM``、``AS`` 等）；取不到或不是整数返回 ``None``。"""
        value = self.tag(name)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def to_line(self) -> str:
        """写成 SAM 的一行（制表符分隔，含可选标签）。"""
        fields = [
            self.query_name,
            str(self.flag),
            self.reference_name,
            str(self.position),
            str(self.mapping_quality),
            str(self.cigar),
            self.next_reference_name,
            str(self.next_position),
            str(self.template_length),
            self.sequence,
            self.qualities,
        ]
        fields.extend(f"{name}:{type_}:{value}" for name, type_, value in self.tags)
        return "\t".join(fields)


def parse_sam_record(line: str) -> SamRecord:
    """解析一条 SAM 记录行。"""
    fields = line.rstrip("\r\n").split("\t")
    if len(fields) < _MANDATORY_FIELDS:
        raise AlignmentFormatError(
            f"SAM 记录至少要有 {_MANDATORY_FIELDS} 个字段，当前只有 {len(fields)} 个：{line!r}"
        )
    for name, text in (
        ("FLAG", fields[1]),
        ("POS", fields[3]),
        ("MAPQ", fields[4]),
        ("PNEXT", fields[7]),
        ("TLEN", fields[8]),
    ):
        if not text.lstrip("-").isdigit():
            raise AlignmentFormatError(f"SAM 记录的 {name} 不是整数：{text!r}")
    tags: list[tuple[str, str, str]] = []
    for raw in fields[_MANDATORY_FIELDS:]:
        name, separator, rest = raw.partition(":")
        type_, second, value = rest.partition(":")
        if not separator or not second:
            raise AlignmentFormatError(f"可选标签格式应为 name:type:value：{raw!r}")
        tags.append((name, type_, value))
    return SamRecord(
        query_name=fields[0],
        flag=int(fields[1]),
        reference_name=fields[2],
        position=int(fields[3]),
        mapping_quality=int(fields[4]),
        cigar=parse_cigar(fields[5]),
        next_reference_name=fields[6],
        next_position=int(fields[7]),
        template_length=int(fields[8]),
        sequence=fields[9],
        qualities=fields[10],
        tags=tuple(tags),
    )


def _open_text(path: Path) -> IO[str]:
    """按**魔数**判断是否 gzip（与公共层其它读写工具同一口径），不看扩展名。"""
    if is_gzip(path):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


class SamReader:
    """SAM 读取器：先解析头部，其余记录按需迭代（大文件不必整个读进内存）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise AlignmentFormatError(f"SAM 文件不存在：{self.path}")
        self._handle = _open_text(self.path)
        header_lines: list[str] = []
        try:
            for line in self._handle:
                if line.startswith("@"):
                    header_lines.append(line)
                    continue
                if not line.strip():
                    continue
                self._pending = line
                break
            else:
                self._pending = ""
            self.header = SamHeader.parse(header_lines)
        except Exception:
            self._handle.close()
            raise

    def __iter__(self) -> Iterator[SamRecord]:
        pending, self._pending = self._pending, ""
        if pending:
            yield parse_sam_record(pending)
        for line in self._handle:
            if not line.strip():
                continue
            yield parse_sam_record(line)

    def close(self) -> None:
        """关闭底层文件。"""
        self._handle.close()

    def __enter__(self) -> SamReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_sam(path: str | Path) -> SamReader:
    """打开 SAM 文件（明文或 gzip）：头部立即可用，记录流式读出。"""
    return SamReader(path)


def read_sam(path: str | Path) -> tuple[SamHeader, tuple[SamRecord, ...]]:
    """一次读完整个 SAM（测试与小文件用；大文件请用 :func:`open_sam`）。"""
    with SamReader(path) as reader:
        return reader.header, tuple(reader)


class SamWriter:
    """SAM 写出器：头部 + 逐条记录，可选 gzip。"""

    def __init__(self, path: str | Path, header: SamHeader, *, compress: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if compress:
            self._handle: IO[str] = gzip.open(self.path, "wt", encoding="utf-8", newline="")
        else:
            self._handle = self.path.open("w", encoding="utf-8", newline="")
        for line in header.to_lines():
            self._handle.write(line + "\n")

    def write(self, record: SamRecord) -> None:
        """写一条记录。"""
        self._handle.write(record.to_line() + "\n")

    def close(self) -> None:
        """关闭底层文件。"""
        self._handle.close()

    def __enter__(self) -> SamWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def write_sam(
    path: str | Path,
    header: SamHeader,
    records: Iterable[SamRecord],
    *,
    compress: bool = False,
) -> int:
    """把头部与记录写到文件，返回写出的记录条数。"""
    count = 0
    with SamWriter(path, header, compress=compress) as writer:
        for record in records:
            writer.write(record)
            count += 1
    return count
