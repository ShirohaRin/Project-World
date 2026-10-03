"""比较多份 `.gd`（分片 C 的第一半，对应上游 ``gdtools COMPARE``）。

用途是进化实验里最常见的那一问：**这几份样本里，哪些突变是共有的、哪些是新出现的**。

上游的 `gdtools COMPARE`（别名 ``ANNOTATE``）就是把多份 `.gd` 汇成一张表——按参考文献的说法，
"行是一条具体的突变、列是不同样本"；它支持 HTML 与 TABLE（CSV）两种输出，``-b`` 还会多带
``TEXT_*`` 列。**我们只对齐了语义**（一行一条突变、一列一个样本），列序与单元格写法是我们自己
定的明确口径——上游 TABLE 的真实输出我们没见过，不假装一致（记在文档的待办里）。

**"同一条突变"怎么判定**：类型相同、参考序列相同、位置相同，并且**类型特有字段也相同**
（``SNP`` 看新碱基、``DEL`` 看长度、``INS`` 看插入序列）。所以同一位置上的 ``A→C`` 与 ``A→G``
是两条不同的突变，各占一行——这条判定是整个比较的基础，有测试专门钉住。

**证据行不参与比较**：``RA`` / ``MC`` / ``JC`` 是支持突变的证据，不是突变本身；拿它们去"跨样本
对齐"没有意义（同一个突变在不同样本里的证据行本来就可能不同）。上游的 compare 表列的也是突变。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .model import GenomeDiff, GenomeDiffRecord
from .text import read_genome_diff

__all__ = [
    "COMPARISON_COLUMNS",
    "ComparisonRow",
    "ComparisonTable",
    "MutationKey",
    "compare_files",
    "compare_samples",
]

#: CSV 表前几列的名字（后面跟着"每个样本一列"）。
COMPARISON_COLUMNS: tuple[str, ...] = (
    "mutation_type",
    "seq_id",
    "position",
    "fields",
    "sample_count",
    "first_sample",
)


@dataclass(frozen=True, slots=True)
class MutationKey:
    """跨样本对齐用的"突变身份"。

    ``signature`` 是类型特有字段拼起来的串（``SNP`` 的新碱基、``DEL`` 的长度、``INS`` 的插入
    序列…），用制表符分隔以免"两个字段"与"一个含分隔符的字段"混淆。
    """

    type: str
    seq_id: str
    position: int
    signature: str = ""

    @classmethod
    def of(cls, record: GenomeDiffRecord) -> MutationKey:
        """从一条突变记录取出身份键。"""
        return cls(
            type=record.type,
            seq_id=record.seq_id,
            position=record.position,
            signature="\t".join(record.fields),
        )

    @property
    def sort_key(self) -> tuple[str, int, str, str]:
        """排序用：（参考名、位置、类型、字段）。"""
        return (self.seq_id, self.position, self.type, self.signature)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """一条突变 + 它出现在哪些样本里。"""

    key: MutationKey
    #: 代表记录：第一次遇到这条突变时的那条记录（保留它的属性，例如注释）。
    record: GenomeDiffRecord
    #: 出现该突变的样本名（按调用方给的样本顺序）。
    samples: tuple[str, ...]
    #: 各样本里这条突变的频率（`NA` / 没有频率时为 ``None``）；键与 :attr:`samples` 一致。
    frequencies: Mapping[str, float | None] = MappingProxyType({})

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def is_shared(self) -> bool:
        """是否出现在多个样本里。"""
        return len(self.samples) > 1

    @property
    def first_sample(self) -> str:
        """第一个出现该突变的样本（按样本顺序——进化实验里就是"最早哪一代出现"）。"""
        return self.samples[0]


@dataclass(frozen=True, slots=True)
class ComparisonTable:
    """多份样本的比较表。"""

    samples: tuple[str, ...]
    rows: tuple[ComparisonRow, ...]

    def shared(self) -> tuple[ComparisonRow, ...]:
        """出现在多个样本里的突变。"""
        return tuple(row for row in self.rows if row.is_shared)

    def unique_to(self, sample: str) -> tuple[ComparisonRow, ...]:
        """只在某一个样本里出现的突变（即该样本独有的那些）。"""
        self._check_sample(sample)
        return tuple(row for row in self.rows if row.samples == (sample,))

    def rows_of(self, sample: str) -> tuple[ComparisonRow, ...]:
        """某个样本里出现过的全部突变（按表的行序）。"""
        self._check_sample(sample)
        return tuple(row for row in self.rows if sample in row.samples)

    def _check_sample(self, sample: str) -> None:
        if sample not in self.samples:
            raise ValueError(
                f"没有名为 {sample!r} 的样本；现有样本：{'、'.join(self.samples) or '（无）'}。"
            )

    def to_csv(self) -> str:
        """摊成 CSV：一行一条突变，一列一个样本。

        样本列写的是**该样本里这条突变的频率**（各样本各写各的），没出现就留空，出现但上游没给
        频率（`frequency=NA`）写 ``NA``。这是我们的口径——上游 TABLE 的单元格写法我们没见过。
        """
        header = [*COMPARISON_COLUMNS, *self.samples]
        lines = [",".join(header)]
        for row in self.rows:
            cells = [
                row.key.type,
                row.key.seq_id,
                str(row.key.position),
                row.key.signature.replace("\t", " "),
                str(row.sample_count),
                row.first_sample,
            ]
            cells.extend(_frequency_cell(row, sample) for sample in self.samples)
            lines.append(",".join(_quote(cell) for cell in cells))
        return "\n".join(lines) + "\n"


def _frequency_cell(row: ComparisonRow, sample: str) -> str:
    if sample not in row.samples:
        return ""
    frequency = row.frequencies.get(sample)
    return "NA" if frequency is None else repr(frequency)


def _quote(cell: str) -> str:
    if any(character in cell for character in ',"'):
        return '"' + cell.replace('"', '""') + '"'
    return cell


def compare_samples(samples: Iterable[tuple[str, GenomeDiff]]) -> ComparisonTable:
    """比较多份"已读好的" `.gd`；样本名与突变出现的顺序都按调用方给的来。

    同一份样本名出现两次会报错——"哪个样本里有这条突变"会因此变得没法说明白。
    每行的代表记录取第一次遇到的那条（保留属性/注释），各样本的频率另存在
    :attr:`ComparisonRow.frequencies` 里，不会互相串。
    """
    sample_list = tuple(samples)
    names: list[str] = []
    appearances: dict[MutationKey, list[tuple[str, float | None]]] = {}
    representatives: dict[MutationKey, GenomeDiffRecord] = {}

    for name, diff in sample_list:
        if name in names:
            raise ValueError(f"样本名 {name!r} 重复了，无法比较。")
        names.append(name)
        for record in diff.mutations():
            key = MutationKey.of(record)
            if key not in appearances:
                appearances[key] = []
                representatives[key] = record
            appearances[key].append((name, record.frequency))

    rows = []
    for key in sorted(appearances, key=lambda item: item.sort_key):
        found = appearances[key]
        rows.append(
            ComparisonRow(
                key=key,
                record=representatives[key],
                samples=tuple(name for name, _frequency in found),
                frequencies=MappingProxyType(dict(found)),
            )
        )
    return ComparisonTable(samples=tuple(names), rows=tuple(rows))


def compare_files(files: Iterable[tuple[str, str | Path]]) -> ComparisonTable:
    """便捷入口：``[(样本名, .gd 路径), ...]`` 直接读文件再比较。"""
    return compare_samples((name, read_genome_diff(path)) for name, path in files)
