"""Genome Diff（``.gd``）的数据模型。

``.gd`` 是 breseq 的机器可读产物格式：每个突变一行，证据（read 一致性、覆盖度缺失、新连接）
各占一行，突发行通过"证据编号"引用它们。它是这条参考比对线的**产物契约**——本项目要产出
同样的文件，也是将来做 gdtools 等价物（比较多个样本、给参考应用/撤销突变）的基础。

**格式依据**（照着真实产物定的，不是照文档猜的）：模型与下面这些规则来自一份公开的 breseq
输出 `output.gd`（breseq 0.33.1，ALE 数据库 aledb.org 里 pgi 进化实验的输出，
`.../breseq/15-3-0-1/output/output.gd`），并对照同一目录的 `index.html` 逐条核对了各列含义。
后面每一条规则都注明用的是哪一行。

结构：

```text
#=GENOME_DIFF  1.0                     ← 固定第一行
#=CREATED / PROGRAM / COMMAND / REFSEQ / READSEQ / MAPPED-BASES ...   ← 元信息，可重复
DEL 1 61,71 NC_000913 257908 776 frequency=1                          ← 突变记录
RA  35 .  NC_000913 701810 0 A . ... prediction=polymorphism           ← 证据记录
```

记录行的列：**类型、编号、证据编号列表、参考名、位置**，之后是"类型特有字段"与
``key=value`` 属性。各类型字段数不同（都取自上面那份真实文件的对应行）：

| 类型 | 特有字段 | 例子 |
| --- | --- | --- |
| ``SNP`` | 新碱基 | ``SNP 5 37 NC_000913 1269450 T``（对照 html 那一行是 `C→T`） |
| ``DEL`` | 缺失长度 | ``DEL 2 35 NC_000913 701810 1``（html 那个位置写的是 `Δ1 bp`） |
| ``INS`` | 插入的碱基 | ``INS 4 74 NC_000913 1159282 AGT``（html 写 `+AGT`） |
| ``MOB`` | 元件名、链、长度 | ``MOB 7 72,77 NC_000913 1293032 IS1 -1 8``（html 写 `IS1 (–) +8 bp`） |
| ``RA`` | 偏移、参考碱基、判定碱基 | ``RA 37 . NC_000913 1269450 0 C T`` |
| ``MC`` | 起点之后的 3 个数 | ``MC 62 . NC_000913 1299499 1300697 1198 0`` |
| ``JC`` | 另一侧的参考/位置/链… | ``JC 69 . NC_000913 1 1 NC_000913 4641652 -1 0`` |

两条容易踩的规矩：

1. **不认识的类型与属性一律保留**。格式里可选属性很多（`frequency`、`prediction`、
   `polymorphism_score`…），而且 breseq 各版本会增删；因为"不认识"就把它们丢掉，
   等于把产物改坏。所以模型里类型是字符串、属性是"保序的键值表"，没有白名单。
2. **证据编号列**在突发行里是编号列表（``61,71``），在证据行里写作 ``.``（没有上级）。
   ``.`` 解析成空元组，写回时还原成 ``.``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "EVIDENCE_TYPES",
    "GD_VERSION",
    "GenomeDiff",
    "GenomeDiffHeader",
    "GenomeDiffRecord",
]

#: 格式版本（第一行 ``#=GENOME_DIFF`` 后面那个值）。
GD_VERSION = "1.0"

#: 头行的前缀。
HEADER_PREFIX = "#="

#: 版本行的键名。
GENOME_DIFF_KEY = "GENOME_DIFF"

#: 手写注释行的键名（``#`` 开头但不是 ``#=`` 的行，原样保留、原样写回）。
COMMENT_KEY = "#"

#: breseq 里表示"证据"的记录类型。其余类型按"突变记录"看待。
#:
#: 取值来自真实产物与 breseq 文档：``RA``（read 一致性）、``MC``（覆盖度缺失）、
#: ``JC``（新连接）、``UN``（未指派）。**不认识的新类型不会被丢掉**——它们照样留在
#: ``GenomeDiff.records`` 里，只是不被这几个便捷方法归类。
EVIDENCE_TYPES = frozenset({"RA", "MC", "JC", "UN"})


@dataclass(frozen=True, slots=True)
class GenomeDiffHeader:
    """``.gd`` 的头：版本号 + 若干 ``#=键 值...`` 元信息（保序）。

    ``entries`` 里**不含**版本那一行（版本单独放在 :attr:`version`），其余按文件顺序保留，
    同名键可以出现多次（例如两个 ``#=READSEQ``）。
    """

    version: str = GD_VERSION
    entries: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        """出现过的键（含重复，按文件顺序）。"""
        return tuple(key for key, _values in self.entries)

    def values_of(self, key: str) -> tuple[str, ...]:
        """某个键的**全部**值（按出现顺序拼起来）。"""
        return tuple(
            value
            for entry_key, values in self.entries
            if entry_key == key
            for value in values
        )

    def value_of(self, key: str) -> str | None:
        """某个键的第一个值；没出现过返回 ``None``。"""
        values = self.values_of(key)
        return values[0] if values else None


@dataclass(frozen=True, slots=True)
class GenomeDiffRecord:
    """``.gd`` 的一条记录（突变或证据）。

    ``evidence`` 是被引用的证据编号（突发行用；证据行是空元组，文件里写作 ``.``）；
    ``fields`` 是类型特有字段（见模块开头那张表）；``attributes`` 是 ``key=value`` 属性，
    **保序**、且不认识的键也留着。
    """

    type: str
    id: str
    evidence: tuple[str, ...]
    seq_id: str
    position: int
    fields: tuple[str, ...] = ()
    attributes: Mapping[str, str] = MappingProxyType({})

    @property
    def is_evidence(self) -> bool:
        """是否是证据记录（类型在 :data:`EVIDENCE_TYPES` 里）。"""
        return self.type in EVIDENCE_TYPES

    def attribute(self, key: str, default: str | None = None) -> str | None:
        """取一个属性的原始字符串值。"""
        return self.attributes.get(key, default)

    @property
    def frequency(self) -> float | None:
        """``frequency`` 属性的数值形式；没有或不是数字（如 ``NA``）时返回 ``None``。"""
        raw = self.attributes.get("frequency")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class GenomeDiff:
    """一个 ``.gd`` 文件：头 + 记录（保序）。"""

    header: GenomeDiffHeader
    records: tuple[GenomeDiffRecord, ...] = ()

    def mutations(self) -> tuple[GenomeDiffRecord, ...]:
        """非证据记录（预测出的突变）。"""
        return tuple(record for record in self.records if not record.is_evidence)

    def evidence(self) -> tuple[GenomeDiffRecord, ...]:
        """证据记录。"""
        return tuple(record for record in self.records if record.is_evidence)

    def by_id(self, record_id: str) -> GenomeDiffRecord | None:
        """按编号找记录（同编号只保留最前面那条；正常文件里编号唯一）。"""
        for record in self.records:
            if record.id == record_id:
                return record
        return None

    def evidence_of(self, record: GenomeDiffRecord) -> tuple[GenomeDiffRecord, ...]:
        """取一条突变记录引用的全部证据（找不到的编号会被跳过——文件可能只截了一段）。"""
        found = []
        for record_id in record.evidence:
            item = self.by_id(record_id)
            if item is not None:
                found.append(item)
        return tuple(found)
