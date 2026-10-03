"""FASTQ 记录的流式读写，以及"读 → 逐条变换 → 写"的通用管道。

**归属**：本文件属于模块公共层。它不依赖任何算法语义——
不解释碱基、不解码质量值、不规定记录该怎么改。数值与生物学含义
一律留给各子模块的算法层。

它原先放在 `submodules/quality_trimming/` 内，按模块分层纪律
（公共能力至少出现两个真实使用方后才提取）暂居子模块；
`submodules/poly_trimming/` 成为第二个使用方后上移到本层。


## FASTQ 格式

每条记录固定 4 行：

    @SRR000001.1 长度=151
    ACGT...（碱基序列）
    +
    IIII...（质量字符串，长度必须与序列行相同）

第 3 行以 ``+`` 开头，其后可选地重复一次名字行内容（早期格式的遗留），
本模块读取时忽略该行内容。第 4 行是 Phred+33 编码的质量字节，
其数值含义由各子模块的算法层解释。


## 为什么必须流式

一个测序样本的 FASTQ 常在 10~50 GB 量级，无法整体读入内存。
因此读取接口返回**迭代器**，:func:`process_fastq` 也是逐条消费，
任何时候内存里只有当前记录。
"""

from __future__ import annotations

import gzip
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True, slots=True)
class FastqRecord:
    """一条 FASTQ 记录。

    ``name`` 不含行首的 ``@``；``sequence`` 与 ``quality`` 保持原始字节，
    不做大小写转换或质量解码——数值解释留给算法层，避免这一层携带语义。
    """

    name: str
    sequence: bytes
    quality: bytes

    @property
    def length(self) -> int:
        """序列长度（碱基数）。"""
        return len(self.sequence)


@dataclass(frozen=True, slots=True)
class FastqStreamSummary:
    """一次流式处理前后的统计结果。

    各子模块的算法都遵循同一套计数口径，因此共用本类型：

        total_reads = kept_reads + dropped_reads
        kept_reads >= changed_reads

    ``changed_reads`` 只统计**序列真的被改动**的 read；``dropped_reads``
    是被算法判定为应当丢弃的 read（例如修剪后长度不合法）。
    """

    total_reads: int
    kept_reads: int
    changed_reads: int
    dropped_reads: int
    bases_before: int
    bases_after: int

    @property
    def bases_removed(self) -> int:
        """被去掉的碱基总数。"""
        return self.bases_before - self.bases_after

    @property
    def removal_rate(self) -> float:
        """被去掉的碱基占输入总碱基的比例。输入为空时返回 0.0。"""
        if self.bases_before == 0:
            return 0.0
        return self.bases_removed / self.bases_before

    def render(self) -> str:
        """渲染成便于阅读与日志记录的多行文本。"""
        lines = [
            f"输入 read 数：{self.total_reads}",
            f"保留 read 数：{self.kept_reads}（其中 {self.changed_reads} 条被改动）",
            f"丢弃 read 数：{self.dropped_reads}",
            f"碱基数：{self.bases_before} → {self.bases_after}"
            f"（去掉 {self.bases_removed}，占 {self.removal_rate:.2%}）",
        ]
        return "\n".join(lines)


def is_gzip(path: str | Path) -> bool:
    """按**魔数**判断文件是否为 gzip 压缩，不看扩展名。

    上游 fastp 同样按内容探测压缩，这样 ``.fastq`` 被外部工具压成 gzip 后
    不改名也能正确处理。上层的文件级接口据此决定输出是否压缩。
    """
    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            return handle.read(2) == _GZIP_MAGIC
    except FileNotFoundError as error:
        raise ValueError(f"文件不存在：{file_path}") from error


def _open_binary(path: Path) -> BinaryIO:
    """按**内容**而非扩展名判断是否 gzip，返回二进制流句柄。"""
    try:
        raw = path.open("rb")
    except FileNotFoundError as error:
        raise ValueError(f"文件不存在：{path}") from error

    magic = raw.read(2)
    raw.seek(0)
    if magic == _GZIP_MAGIC:
        # 交给 gzip 包装，原句柄由 GzipFile 负责关闭。
        return gzip.GzipFile(fileobj=raw, mode="rb")
    return raw


def read_fastq(
    path: str | Path, *, limit: int | None = None
) -> Iterator[FastqRecord]:
    """流式读取 FASTQ 文件，逐条产出记录。

    自动识别 gzip（按魔数，不看扩展名）。

    参数：
        path: FASTQ 或 FASTQ.GZ 文件路径。
        limit: 最多产出多少条记录；``None``（默认）表示读完全部。
            对应上游的 ``--reads_to_process``（它用 0 表示全部），
            用于"先拿一小段数据试跑"这类场景。

    产出：
        :class:`FastqRecord`，按文件中的顺序。

    异常：
        ``ValueError``：文件不存在、记录行数不是 4 的倍数、
        序列行与质量行长度不一致，或名字行不以 ``@`` 开头。
        错误信息会带上行号，便于在大文件中定位。
    """
    file_path = Path(path)
    handle = _open_binary(file_path)
    line_number = 0
    produced = 0
    try:
        while True:
            if limit is not None and produced >= limit:
                break
            produced += 1
            name_line = handle.readline()
            if not name_line:
                break
            line_number += 1

            sequence_line = handle.readline()
            plus_line = handle.readline()
            quality_line = handle.readline()
            if not sequence_line or not plus_line or not quality_line:
                raise ValueError(
                    f"第 {line_number} 行起的记录不完整：FASTQ 每条记录需要 4 行。"
                )
            line_number += 3

            name = name_line.rstrip(b"\r\n")
            if not name.startswith(b"@"):
                raise ValueError(
                    f"第 {line_number - 3} 行的名字行不以 '@' 开头：{name[:40]!r}。"
                )
            if not plus_line.startswith(b"+"):
                # 注意：不能把 rstrip 直接写进 f-string 的表达式里。
                # f-string 表达式包含反斜杠在 Python 3.11 中是语法错误（3.12 才放开），
                # 而客户端的嵌入式 Python 正是 3.11。先取出变量再插值。
                preview = plus_line.rstrip(b"\r\n")[:40]
                raise ValueError(
                    f"第 {line_number - 1} 行的分隔行不以 '+' 开头：{preview!r}。"
                )

            sequence = sequence_line.rstrip(b"\r\n")
            quality = quality_line.rstrip(b"\r\n")
            if len(sequence) != len(quality):
                raise ValueError(
                    f"第 {line_number - 3} 行起的记录中，序列长度（{len(sequence)}）"
                    f"与质量长度（{len(quality)}）不一致。"
                )

            yield FastqRecord(
                name=name[1:].decode("utf-8", "replace"),
                sequence=sequence,
                quality=quality,
            )
    finally:
        handle.close()


class FastqWriter:
    """逐条写 FASTQ 文件的写入器。

    用于需要**同时写多个输出**的场景——双端算法要成对写出 R1/R2 两份文件，
    而 :func:`write_fastq` 是"喂一个可迭代对象、写一个文件"的形态，套不上。
    只写一个文件时直接用 :func:`write_fastq` 更省事。

    用法::

        with FastqWriter(path, compress=True) as writer:
            writer.write(record)

    写出格式与 :func:`write_fastq` **完全一致**（名字行补 ``@``、分隔行统一写 ``+``）：
    两者共用同一段写记录的代码，不会各自漂移。
    """

    def __init__(self, path: str | Path, *, compress: bool = False) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: BinaryIO = (
            gzip.open(self._path, "wb") if compress else self._path.open("wb")
        )
        self._written = 0

    @property
    def written(self) -> int:
        """已写出的记录条数。"""
        return self._written

    def write(self, record: FastqRecord) -> None:
        self._handle.write(b"@" + record.name.encode("utf-8") + b"\n")
        self._handle.write(record.sequence + b"\n")
        self._handle.write(b"+\n")
        self._handle.write(record.quality + b"\n")
        self._written += 1

    def close(self) -> None:
        """刷新并关闭；重复调用是安全的。"""
        self._handle.close()

    def __enter__(self) -> "FastqWriter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def write_fastq(
    records: Iterable[FastqRecord], path: str | Path, *, compress: bool = False
) -> int:
    """把记录写入 FASTQ 文件，返回写入的条数。

    参数：
        records: 可迭代的 :class:`FastqRecord`。
        path: 输出路径；父目录不存在会自动创建。
        compress: 为 ``True`` 时用 gzip 写出。

    说明：
        名字行会重新加上 ``@``，分隔行统一写 ``+``（不重复名字）。
        写出不做任何过滤或校验，调用方负责决定写哪些记录。
    """
    with FastqWriter(path, compress=compress) as writer:
        for record in records:
            writer.write(record)
    return writer.written


@dataclass(slots=True)
class _RunningStats:
    """流式处理期间的可变计数器。"""

    total: int = 0
    kept: int = 0
    changed: int = 0
    dropped: int = 0
    bases_before: int = 0
    bases_after: int = 0


def process_fastq(
    input_path: str | Path,
    output_path: str | Path,
    transform: Callable[[FastqRecord], FastqRecord | None],
    *,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """流式读取 → 逐条变换 → 写出，并返回前后统计。

    这是各算法子模块共用的**文件级管道**：算法只需提供一个把
    :class:`FastqRecord` 映射成新记录的纯函数，其余（流式读、统计、
    压缩策略、失败清理）都由本函数负责。

    参数：
        input_path: 输入 FASTQ 路径，gzip 按魔数自动识别。
        output_path: 输出 FASTQ 路径；父目录不存在会自动创建。
        transform: 逐条变换函数。返回 ``None`` 表示丢弃这条记录；
            返回的记录的 ``sequence`` 与输入相同则不计入 ``changed_reads``。
        compress: 输出是否 gzip 压缩。``None``（默认）表示**跟随输入**
            ——不看输出文件名的扩展名，因为文件名可能骗人。

    返回：
        :class:`FastqStreamSummary`。

    异常：
        ``ValueError``：输入文件不存在或其 FASTQ 格式不合法。

    说明：
        若中途失败（例如输入文件在记录边界处截断），**已写出的部分输出会被删除**，
        避免留下看似完整实则残缺的结果文件。
    """
    input_file = Path(input_path)
    if not input_file.exists():
        raise ValueError(f"文件不存在：{input_file}")

    if compress is None:
        compress = is_gzip(input_file)

    stats = _RunningStats()
    output_file = Path(output_path)

    def _transformed() -> Iterator[FastqRecord]:
        for record in read_fastq(input_file):
            stats.total += 1
            stats.bases_before += record.length

            result = transform(record)
            if result is None:
                stats.dropped += 1
                continue

            if result.sequence != record.sequence:
                stats.changed += 1
            stats.kept += 1
            stats.bases_after += len(result.sequence)
            yield result

    try:
        written = write_fastq(_transformed(), output_file, compress=compress)
    except Exception:
        output_file.unlink(missing_ok=True)
        raise

    # 写出条数与统计的保留条数一致；不一致说明统计与写出脱节，属于内部错误。
    if written != stats.kept:
        raise RuntimeError(
            f"内部统计不一致：写出 {written} 条，统计保留 {stats.kept} 条。"
        )

    return FastqStreamSummary(
        total_reads=stats.total,
        kept_reads=stats.kept,
        changed_reads=stats.changed,
        dropped_reads=stats.dropped,
        bases_before=stats.bases_before,
        bases_after=stats.bases_after,
    )
