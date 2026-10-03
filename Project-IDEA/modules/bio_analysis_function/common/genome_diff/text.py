"""``.gd`` 的解析与写回。

解析只做格式层面的工作：**不认识的东西一律留着**（未知类型、未知属性都原样进模型），
所以拿它读一份新版本 breseq 的产物不会掉信息；写回时按同样顺序摊开，
正常文件能做到**逐字节往返**（见测试里那份真实产物的 round-trip）。

两条实现上的取舍：

- **字段与属性的分界**：记录的第 6 列起，先是一串"类型特有字段"，然后是 ``key=value`` 属性。
  分界点取**第一个含 ``=`` 的列**——真实产物里每个类型都符合这个规律（``frequency=``、
  ``alignment_overlap=``、``left_inside_cov=`` … 都是属性段的第一个）。若属性段里出现不带
  ``=`` 的裸词，读作"键、值为空"，写回时变成 ``键=``（这是唯一会让往返不完全一致的情形，
  真实产物里不会出现）。
- **位置必须是整数**：各类型的坐标都在单独的第 5 列（1-based），范围/另一侧坐标放在类型
  特有字段里。位置不是整数时报错并给出行号，而不是猜着解析——静默解析错位置比报错难查得多。
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from .model import (
    COMMENT_KEY,
    GENOME_DIFF_KEY,
    GD_VERSION,
    HEADER_PREFIX,
    GenomeDiff,
    GenomeDiffHeader,
    GenomeDiffRecord,
)

__all__ = [
    "format_genome_diff",
    "parse_genome_diff",
    "read_genome_diff",
    "write_genome_diff",
]

_ATTRIBUTE_SEPARATOR = "="


def _split_header(line: str) -> tuple[str, tuple[str, ...]]:
    """把一行头拆成 ``(键, 值...)``。"""
    body = line[len(HEADER_PREFIX) :]
    parts = body.split("\t")
    return parts[0], tuple(parts[1:])


def _split_record(parts: list[str], line_number: int) -> GenomeDiffRecord:
    """把一行记录的列拆成模型对象。"""
    if len(parts) < 5:
        raise ValueError(
            f"第 {line_number} 行只有 {len(parts)} 列，``.gd`` 记录至少要有 5 列"
            f"（类型、编号、证据、参考名、位置）：{parts!r}"
        )
    record_type, record_id, evidence_text, seq_id, position_text = parts[:5]
    try:
        position = int(position_text)
    except ValueError as error:
        raise ValueError(
            f"第 {line_number} 行的位置不是整数：{position_text!r}"
            f"（类型 {record_type}、编号 {record_id}）。"
        ) from error

    fields: list[str] = []
    attributes: dict[str, str] = {}
    for column in parts[5:]:
        if not attributes and _ATTRIBUTE_SEPARATOR not in column:
            fields.append(column)
            continue
        key, separator, value = column.partition(_ATTRIBUTE_SEPARATOR)
        attributes[key] = value if separator else ""

    return GenomeDiffRecord(
        type=record_type,
        id=record_id,
        evidence=tuple(item for item in evidence_text.split(",") if item != "."),
        seq_id=seq_id,
        position=position,
        fields=tuple(fields),
        attributes=MappingProxyType(attributes),
    )


def parse_genome_diff(text: str) -> GenomeDiff:
    """解析 ``.gd`` 文本。

    空行跳过；``#=`` 开头的是元信息；``#`` 开头但不是 ``#=`` 的行原样保留（写回时照抄）。
    版本行 ``#=GENOME_DIFF`` 取第一个值作为版本号，缺失时用默认版本。
    """
    version = GD_VERSION
    entries: list[tuple[str, tuple[str, ...]]] = []
    records: list[GenomeDiffRecord] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith(HEADER_PREFIX):
            key, values = _split_header(line)
            if key == GENOME_DIFF_KEY:
                if values:
                    version = values[0]
                continue
            entries.append((key, values))
            continue
        if line.startswith("#"):
            entries.append((COMMENT_KEY, (line,)))
            continue
        records.append(_split_record(line.split("\t"), line_number))

    header = GenomeDiffHeader(version=version, entries=tuple(entries))
    return GenomeDiff(header=header, records=tuple(records))


def read_genome_diff(path: str | Path) -> GenomeDiff:
    """从文件读 ``.gd``（按 UTF-8 读，兼容 CRLF）。"""
    return parse_genome_diff(Path(path).read_text(encoding="utf-8"))


def format_genome_diff(diff: GenomeDiff) -> str:
    """把模型写回 ``.gd`` 文本（末尾带换行；正常文件与原文逐字节一致）。"""
    lines = [f"{HEADER_PREFIX}{GENOME_DIFF_KEY}\t{diff.header.version}"]
    for key, values in diff.header.entries:
        if key == COMMENT_KEY:
            lines.append(values[0] if values else "")
            continue
        lines.append(HEADER_PREFIX + key + "".join("\t" + value for value in values))
    lines.extend(_format_record(record) for record in diff.records)
    return "".join(line + "\n" for line in lines)


def _format_record(record: GenomeDiffRecord) -> str:
    columns = [
        record.type,
        record.id,
        ",".join(record.evidence) if record.evidence else ".",
        record.seq_id,
        str(record.position),
        *record.fields,
    ]
    columns.extend(
        f"{key}{_ATTRIBUTE_SEPARATOR}{value}" for key, value in record.attributes.items()
    )
    return "\t".join(columns)


def write_genome_diff(path: str | Path, diff: GenomeDiff) -> int:
    """写出 ``.gd``，返回**记录条数**（不含头）。父目录会自动创建。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_genome_diff(diff), encoding="utf-8", newline="\n")
    return len(diff.records)
