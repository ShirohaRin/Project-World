"""GenBank 平面文件读取：序列 + 拓扑 + FEATURES 注释。

只要参考基因组需要的那部分——``LOCUS``（名字、长度、拓扑、分子类型）、``DEFINITION``、
``FEATURES`` 表、``ORIGIN`` 序列。其余顶层字段（REFERENCE / COMMENT / 各种编号）
本层**原样跳过**，不进模型：它们对变异判定与注释没有作用，收进来只会多一份要维护的字段表。

解析分两步：先按"顶格即新关键字"把记录切成块，再逐块处理。GenBank 的顶层关键字一律从
第 1 列开始，续行必有缩进，所以这条规则足以稳定切块，比按字段名一个个找更抗格式差异。

接受的序列字母：IUPAC 核酸代码 ``ACGTRYSWKMBDHVN``（大小写均可，输出统一大写）。
出现别的字母说明这不是核酸序列（或文件被写坏了），一律报错而不是静默丢弃——
静默丢碱基会让下游所有坐标错位，且不会有人发现。
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from ..fastq import is_gzip
from .model import (
    Feature,
    Location,
    Part,
    ReferenceFormatError,
    ReferenceSequence,
    ReferenceSet,
)

__all__ = ["parse_genbank", "parse_location", "read_genbank"]

#: GenBank FEATURES 表的列位置：特征名在第 6~21 列，位置与限定符自第 22 列起。
_KEY_COLUMN = 5
_LOCATION_COLUMN = 21

#: 允许出现在参考序列里的字母（IUPAC 核酸代码）。
_SEQUENCE_LETTERS = frozenset("ACGTRYSWKMBDHVN")

_LOCATION_FUNCTIONS = ("complement", "join", "order")

_RECORD_TERMINATOR = "//"


def _open_text(path: Path) -> IO[str]:
    """按**魔数**判断是否 gzip（与公共层 FASTQ 读写同一口径），不看扩展名。"""
    if is_gzip(path):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


# ---------------------------------------------------------------------------
# 位置解析
# ---------------------------------------------------------------------------


def _split_top_level(text: str) -> list[str]:
    """按顶层逗号切分（括号内的逗号不算）。"""
    segments: list[str] = []
    depth = 0
    current: list[str] = []
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ReferenceFormatError(f"位置写法 {text!r} 的括号不配对。")
        if character == "," and depth == 0:
            segments.append("".join(current))
            current = []
            continue
        current.append(character)
    if depth != 0:
        raise ReferenceFormatError(f"位置写法 {text!r} 的括号不配对。")
    if current:
        segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _parse_atom(text: str) -> Part:
    """解析最底层的一段的写法：``123``、``123..456``、``<123..456``、``123..>456``。"""
    token = text.strip()
    if not token:
        raise ReferenceFormatError("位置里出现空的区间写法。")
    if "^" in token:
        raise ReferenceFormatError(
            f"位置写法 {token!r} 用了插入位点记法（123^124），本层不支持。"
        )
    partial = False
    if ".." in token:
        left, _, right = token.partition("..")
        left, right = left.strip(), right.strip()
        if left[:1] in ("<", ">"):
            partial = True
            left = left[1:]
        if right[:1] in ("<", ">"):
            partial = True
            right = right[1:]
        if not left.isdigit() or not right.isdigit():
            raise ReferenceFormatError(f"位置写法 {token!r} 不是合法的区间。")
        return Part(start=int(left), end=int(right), strand=1, partial=partial)
    if token[:1] in ("<", ">"):
        partial = True
        token = token[1:]
    if not token.isdigit():
        raise ReferenceFormatError(f"位置写法 {token!r} 不是合法的坐标。")
    position = int(token)
    return Part(start=position, end=position, strand=1, partial=partial)


def _parse_expression(text: str) -> tuple[list[Part], str]:
    """递归解析一个位置表达式，返回 ``(各段, 运算符)``。"""
    token = text.strip()
    for function in _LOCATION_FUNCTIONS:
        if not token.startswith(f"{function}("):
            continue
        if not token.endswith(")"):
            raise ReferenceFormatError(f"位置写法 {token!r} 的括号不配对。")
        inner = token[len(function) + 1 : -1]
        parts: list[Part] = []
        operator = "order" if function == "order" else "join"
        for segment in _split_top_level(inner):
            sub_parts, sub_operator = _parse_expression(segment)
            if sub_operator == "order":
                operator = "order"
            parts.extend(sub_parts)
        if function == "complement":
            # 整体反向互补 = 各段的链翻转、且段序颠倒（顺序不动是错的，见 model.py 说明）。
            parts = [Part(p.start, p.end, -p.strand, p.partial) for p in reversed(parts)]
            operator = "join"
        return parts, operator
    return [_parse_atom(token)], "single"


def parse_location(text: str) -> Location:
    """解析 GenBank 位置写法（支持 ``complement`` / ``join`` / ``order`` 嵌套）。"""
    raw = text.strip()
    if not raw:
        raise ReferenceFormatError("位置写法为空。")
    parts, operator = _parse_expression(raw)
    if len(parts) == 1 and operator != "order":
        operator = "single"
    return Location(parts=tuple(parts), operator=operator, raw=raw)


# ---------------------------------------------------------------------------
# 记录切块
# ---------------------------------------------------------------------------


def _split_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """按"顶格即新关键字"把一条记录切成 ``[(关键字行, 续行…)]``。"""
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t"):
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            raise ReferenceFormatError(f"记录开头出现无法归属的续行：{line!r}")
    return blocks


def _keyword(line: str) -> str:
    """取顶层关键字（第一个词，全大写）。"""
    return line.split()[0].upper() if line.split() else ""


# ---------------------------------------------------------------------------
# 各块的解析
# ---------------------------------------------------------------------------


def _parse_locus(line: str) -> tuple[str, int, bool]:
    """解析 LOCUS 行，返回 ``(SEQ_ID, 声明的长度, 是否环状)``。"""
    tokens = line.split()
    if len(tokens) < 2:
        raise ReferenceFormatError(f"LOCUS 行不完整：{line.strip()!r}")
    seq_id = tokens[1]
    unit_index = next((i for i, token in enumerate(tokens) if token in ("bp", "aa")), None)
    if unit_index is None or unit_index == 0:
        raise ReferenceFormatError(f"LOCUS 行缺少长度单位（bp / aa）：{line.strip()!r}")
    if tokens[unit_index] == "aa":
        raise ReferenceFormatError(
            f"参考资料 {seq_id} 是蛋白序列（aa）而不是核酸序列，不能用作重测序参考。"
        )
    declared = int(tokens[unit_index - 1]) if tokens[unit_index - 1].isdigit() else 0
    return seq_id, declared, "circular" in (token.lower() for token in tokens)


def _parse_features(body: list[str]) -> tuple[Feature, ...]:
    """解析 FEATURES 表体。"""
    features: list[Feature] = []
    kind = ""
    location_chunks: list[str] = []
    qualifiers: dict[str, list[str]] = {}
    index = 0
    pending_name: str | None = None
    pending_raw: str = ""

    def store(name: str, raw: str) -> None:
        """结算一个限定符的取值（处理引号与转义）。"""
        text = raw.strip()
        if text.startswith('"'):
            text = text[1:]
            if text.endswith('"'):
                text = text[:-1]
            text = text.replace('""', '"')
        qualifiers.setdefault(name, []).append(text)

    def flush_qualifier() -> None:
        nonlocal pending_name, pending_raw
        if pending_name is not None:
            store(pending_name, pending_raw)
            pending_name, pending_raw = None, ""

    def flush_feature() -> None:
        nonlocal kind, location_chunks, qualifiers, index
        flush_qualifier()
        if not kind:
            return
        raw_location = "".join(location_chunks).strip()
        if not raw_location:
            raise ReferenceFormatError(f"特征 {kind}（第 {index} 个）没有位置。")
        features.append(
            Feature(
                kind=kind,
                location=parse_location(raw_location),
                qualifiers={name: tuple(values) for name, values in qualifiers.items()},
                index=index,
            )
        )
        index += 1
        kind, location_chunks, qualifiers = "", [], {}

    for line in body:
        if not line.strip():
            continue
        head = line[_KEY_COLUMN:]
        is_feature_line = (
            len(line) > _KEY_COLUMN
            and line[:_KEY_COLUMN].strip() == ""
            and head[:1] not in (" ", "\t")
        )
        if is_feature_line:
            flush_feature()
            key_field = line[_KEY_COLUMN:_LOCATION_COLUMN].strip()
            rest = line[_LOCATION_COLUMN:].strip()
            if key_field:
                kind = key_field.split()[0]
            else:
                pieces = head.split()
                kind, rest = pieces[0], " ".join(pieces[1:])
            if rest:
                location_chunks = [rest]
            continue
        stripped = line.strip()
        if stripped.startswith("/"):
            flush_qualifier()
            name, _, raw = stripped[1:].partition("=")
            pending_name = name.strip()
            pending_raw = raw
            continue
        if pending_name is not None:
            pending_raw += stripped
            continue
        location_chunks.append(stripped)
    flush_feature()
    return tuple(features)


def _parse_origin(body: list[str], seq_id: str) -> str:
    """解析 ORIGIN 块（去掉行号与空白，统一大写）。"""
    letters: list[str] = []
    for line in body:
        letters.extend(character for character in line if character.isalpha())
    sequence = "".join(letters).upper()
    if not sequence:
        return ""
    invalid = sorted(set(sequence) - _SEQUENCE_LETTERS)
    if invalid:
        raise ReferenceFormatError(
            f"参考序列 {seq_id} 的序列里出现非核酸字母：{'、'.join(invalid)}。"
        )
    return sequence


def _parse_record(lines: list[str]) -> ReferenceSequence:
    """解析一条 GenBank 记录。"""
    seq_id = ""
    declared_length = 0
    circular = False
    description = ""
    features: tuple[Feature, ...] = ()
    sequence = ""
    has_contig = False

    for header, body in _split_blocks(lines):
        keyword = _keyword(header)
        if keyword == "LOCUS":
            seq_id, declared_length, circular = _parse_locus(header)
        elif keyword == "DEFINITION":
            pieces = header.split(None, 1)
            parts = [pieces[1].strip()] if len(pieces) > 1 else []
            parts.extend(part.strip() for part in body if part.strip())
            description = " ".join(part for part in parts if part)
        elif keyword == "FEATURES":
            features = _parse_features(body)
        elif keyword == "ORIGIN":
            sequence = _parse_origin(body, seq_id or "<未知>")
        elif keyword in ("CONTIG", "WGS", "WGS_SCAFLD"):
            has_contig = True

    if not seq_id:
        raise ReferenceFormatError("GenBank 记录缺少 LOCUS 行。")
    if not sequence:
        if has_contig:
            raise ReferenceFormatError(
                f"参考记录 {seq_id} 只有 CONTIG（未完成图），没有实际序列，无法作为参考。"
            )
        raise ReferenceFormatError(f"参考记录 {seq_id} 没有 ORIGIN 序列。")
    if declared_length and declared_length != len(sequence):
        raise ReferenceFormatError(
            f"参考记录 {seq_id} 声明的长度 {declared_length} 与实际序列长度 "
            f"{len(sequence)} 不符，文件可能被截断。"
        )
    return ReferenceSequence(
        seq_id=seq_id,
        sequence=sequence,
        features=features,
        circular=circular,
        description=description,
    )


def _iter_records(text: str) -> Iterator[list[str]]:
    """按 ``//`` 切出各条记录的行。"""
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == _RECORD_TERMINATOR:
            if current:
                yield current
                current = []
            continue
        current.append(line.rstrip("\r"))
    if any(line.strip() for line in current):
        yield current


def parse_genbank(text: str) -> ReferenceSet:
    """解析 GenBank 文本（一个文件可含多条记录）。"""
    records = [_parse_record(lines) for lines in _iter_records(text)]
    if not records:
        raise ReferenceFormatError("GenBank 文件里没有读到任何记录（缺少 // 结尾？）。")
    return ReferenceSet(tuple(records))


def read_genbank(path: str | Path) -> ReferenceSet:
    """读取 GenBank 文件（明文或 gzip，按魔数识别）。"""
    file = Path(path)
    if not file.exists():
        raise ReferenceFormatError(f"参考文件不存在：{file}")
    with _open_text(file) as handle:
        return parse_genbank(handle.read())
