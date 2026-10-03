"""FASTA 读取：只有序列，没有注释、没有拓扑。

**FASTA 表达不了环状拓扑**（这是格式本身的限制，上游 breseq 也明确写了这一点），
因此本文件读出来的参考一律标为 ``circular=False``。要区分环状与线状，用 GenBank。

SEQ_ID 取文件头里**第一个空白分隔的词**，其余部分作为描述。这与
`samtools faidx` / `bowtie2-build` 的口径一致，也是 breseq 认的 `SEQ_ID`。
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from ..fastq import is_gzip
from .model import ReferenceFormatError, ReferenceSequence, ReferenceSet

__all__ = ["parse_fasta", "read_fasta"]


def _open_text(path: Path) -> IO[str]:
    """按**内容**（魔数）而非扩展名判断是否 gzip，与公共层的 FASTQ 读写同一口径。"""
    if is_gzip(path):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def _split_header(text: str, line_number: int) -> tuple[str, str]:
    """把 ``>id description`` 拆成 ``(seq_id, description)``。"""
    body = text[1:].strip()
    if not body:
        raise ReferenceFormatError(f"第 {line_number} 行的 FASTA 头为空。")
    seq_id, _, description = body.partition(" ")
    return seq_id, description.strip()


def parse_fasta(text: str) -> ReferenceSet:
    """解析 FASTA 文本（可含多条记录）。"""
    sequences: list[ReferenceSequence] = []
    seq_id = ""
    description = ""
    chunks: list[str] = []

    def flush() -> None:
        if not seq_id:
            return
        sequence = "".join(chunks).upper()
        if not sequence:
            raise ReferenceFormatError(f"FASTA 记录 {seq_id} 没有任何序列。")
        sequences.append(
            ReferenceSequence(
                seq_id=seq_id,
                sequence=sequence,
                features=(),
                circular=False,
                description=description,
            )
        )

    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            seq_id, description = _split_header(line, line_number)
            chunks = []
            continue
        if not seq_id:
            raise ReferenceFormatError(
                f"第 {line_number} 行出现在任何 FASTA 头之前，文件不像 FASTA。"
            )
        chunks.append("".join(line.split()))
    flush()
    if not sequences:
        raise ReferenceFormatError("FASTA 文件里没有读到任何记录。")
    return ReferenceSet(tuple(sequences))


def read_fasta(path: str | Path) -> ReferenceSet:
    """读取 FASTA（明文或 gzip，按魔数识别）。"""
    file = Path(path)
    if not file.exists():
        raise ReferenceFormatError(f"参考文件不存在：{file}")
    with _open_text(file) as handle:
        return parse_fasta(handle.read())
