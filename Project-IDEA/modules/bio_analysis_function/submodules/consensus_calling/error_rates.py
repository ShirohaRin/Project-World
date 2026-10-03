"""碱基错误率重校准（分片 B）：用数据自己数出"观测到什么碱基"的概率。

共识调用的第二步。为什么不信 FASTQ 里的 Phred 质量？因为那个数字是**测序仪的理论模型**，
和这一份数据里的真实错误率常有系统偏差——文库制备、比对、局部碱基组成都会影响它。上游
breseq 的办法很直接（[Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）：

1. **假设**：reads 与参考的不一致**绝大多数是测序错误**，不是真变异。对目标数据成立
   （样本与参考差异 <1‰，见算法清单 5.5.1）。
2. **计数**：数"参考碱基 × read 碱基 × 质量"的出现次数。**单碱基缺失也算一种 read "碱基"**，
   它的质量用 read 里**紧随其后的那个被比对碱基**的质量。
3. **伪计数**：每个格子加 **1**（``pseudocount=1``）——避免"某格恰好没数到"就得出
   "这种错误不可能发生"这种荒谬结论。
4. **归一化**：对每个 (参考碱基, 质量) 行，用该行所有 read 类别的总计数（含伪计数）作分母，
   得到条件概率 ``P(观测到 read 碱基 b | 参考碱基 r, 质量 q)``。

于是这张表就是一个**经验的条件分布**：对角元（read 与参考相同）大概率接近 1，非对角元就是
"在这个质量下，把 r 误读成 b"的经验错误率。共识打分（分片 C）要用的正是这张表。

两条必须说清的边界：

- **长缺失不进表**。上游在预处理阶段就把 indel > 2 bp 的比对拆成了子比对，`RA` 线只处理
  ≤2 bp 的小 indel；本实现同样只把**单碱基**缺失计入"gap"类别，更长的缺失只是计数上报
  （``skipped_long_deletions``），不污染错误率表。
- **非 ACGT 不参与统计**。参考碱基是 ``N`` 的位置、read 碱基是 ``N`` 的观测都排除在外，
  并分别计数上报（``skipped_reference_ambiguous`` / ``skipped_read_ambiguous``）——
  静默丢弃会让"为什么这张表看起来不对"变得无法解释。

**没有观测到的行**（某个 (参考碱基, 质量) 组合一条都没数到）返回**均匀分布**（五个 read
类别各 1/5）：这等于说"在这个质量上，数据什么都没告诉我"，比编造一个数要诚实。低覆盖数据上
这会削弱证据，从而让共识判定更保守——方向上是对的。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ...common.alignment_io import SamRecord
from ...common.reference_io import ReferenceSet
from .pileup import iter_pileup
from .trimming import ReferenceTrimmer

__all__ = ["BaseErrorRates", "build_error_rates"]

#: 可判读的碱基（其余 read 碱基一律排除）。
_BASES = "ACGT"
#: 单碱基缺失在统计里占的"read 类别"。
_GAP = "-"
#: 五个 read 类别：四种碱基 + 单碱基缺失。
_READ_CLASSES = ("A", "C", "G", "T", _GAP)
#: 伪计数。上游固定用 1；不做成参数，等真有数据证明需要再改。
_PSEUDOCOUNT = 1


@dataclass(frozen=True, slots=True)
class BaseErrorRates:
    """经验错误率表：``P(读段碱基 | 参考碱基, 质量)``。

    ``table`` 的键是 ``(参考碱基, 质量)``，值是五个 read 类别的概率（行和为 1）。
    """

    table: Mapping[tuple[str, int], Mapping[str, float]]
    observations: int = 0
    skipped_reference_ambiguous: int = 0
    skipped_read_ambiguous: int = 0
    skipped_long_deletions: int = 0
    #: 因为落在 read 末端被裁剪（见 `trimming.py`）而没进表的观测数。
    skipped_trimmed: int = 0

    def probability(self, reference_base: str, read_class: str, quality: int) -> float:
        """``P(观测到 read_class | 参考碱基 reference_base, 质量 quality)``。

        该 (参考碱基, 质量) 组合没有观测时返回**均匀分布**（五个类别各 1/5）。
        """
        if reference_base not in _BASES:
            raise ValueError(f"参考碱基必须是 ACGT 之一，当前为 {reference_base!r}。")
        if read_class not in _READ_CLASSES:
            raise ValueError(
                f"read 类别必须是 {_READ_CLASSES} 之一（{_GAP!r} 表示单碱基缺失），"
                f"当前为 {read_class!r}。"
            )
        row = self.table.get((reference_base, quality))
        if row is None:
            return 1.0 / len(_READ_CLASSES)
        return row[read_class]

    def match_probability(self, reference_base: str, quality: int) -> float:
        """``P(读段碱基与参考相同 | 参考碱基 reference_base, 质量 quality)``。"""
        return self.probability(reference_base, reference_base, quality)

    def row(self, reference_base: str, quality: int) -> Mapping[str, float]:
        """某个 (参考碱基, 质量) 行的完整分布（没有观测时是均匀分布）。"""
        if reference_base not in _BASES:
            raise ValueError(f"参考碱基必须是 ACGT 之一，当前为 {reference_base!r}。")
        row = self.table.get((reference_base, quality))
        if row is not None:
            return row
        uniform = 1.0 / len(_READ_CLASSES)
        return MappingProxyType({read_class: uniform for read_class in _READ_CLASSES})

    def rows(self) -> tuple[tuple[str, int], ...]:
        """已观测到的 (参考碱基, 质量) 行，按（碱基、质量）排序。"""
        return tuple(sorted(self.table))

    def summary(self) -> dict[str, int]:
        """统计量：入了表的观测数、被排除的各类计数、行数。"""
        return {
            "observations": self.observations,
            "rows": len(self.table),
            "skipped_reference_ambiguous": self.skipped_reference_ambiguous,
            "skipped_read_ambiguous": self.skipped_read_ambiguous,
            "skipped_long_deletions": self.skipped_long_deletions,
            "skipped_trimmed": self.skipped_trimmed,
        }


def _empty_row() -> dict[str, int]:
    return {read_class: 0 for read_class in _READ_CLASSES}


def _bump(
    counts: dict[tuple[str, int], dict[str, int]],
    reference_base: str,
    quality: int,
    read_class: str,
) -> None:
    row = counts.get((reference_base, quality))
    if row is None:
        row = _empty_row()
        counts[(reference_base, quality)] = row
    row[read_class] += 1


def build_error_rates(
    reference: ReferenceSet,
    source: str | Path | Iterable[SamRecord],
    *,
    default_quality: int = 0,
    trimming: Mapping[str, ReferenceTrimmer] | None = None,
) -> BaseErrorRates:
    """扫一遍堆叠，建出经验错误率表。

    参数：
        reference: 参考集合。
        source: SAM 文件路径，或一堆 :class:`SamRecord`（与 :func:`iter_pileup` 一致）。
        default_quality: ``QUAL`` 为 ``*`` 时使用的质量（见 :func:`iter_pileup`）。
        trimming: read 端裁剪表（见 :func:`build_trimming`）。给了它就**不把被裁末端的碱基**
            算进错误率——上游的流程里裁剪是先于重校准的（Methods 的顺序就是"端裁剪 → 重校准"）。
    """
    counts: dict[tuple[str, int], dict[str, int]] = {}
    observations = 0
    skipped_reference_ambiguous = 0
    skipped_read_ambiguous = 0
    skipped_long_deletions = 0
    skipped_trimmed = 0

    for column in iter_pileup(
        reference, source, default_quality=default_quality, trimming=trimming
    ):
        reference_ok = column.reference_base in _BASES
        for observation in column.bases:
            if observation.trimmed:
                skipped_trimmed += 1
            elif not reference_ok:
                skipped_reference_ambiguous += 1
            elif observation.base not in _BASES:
                skipped_read_ambiguous += 1
            else:
                _bump(counts, column.reference_base, observation.quality, observation.base)
                observations += 1
        for deletion in column.deletions:
            if deletion.length != 1:
                # 长缺失属于 junction 那一路的证据，不该被算成"这里的单碱基错误率"。
                skipped_long_deletions += 1
            elif deletion.trimmed:
                skipped_trimmed += 1
            elif not reference_ok:
                skipped_reference_ambiguous += 1
            else:
                _bump(counts, column.reference_base, deletion.quality, _GAP)
                observations += 1

    table: dict[tuple[str, int], Mapping[str, float]] = {}
    for key, row in counts.items():
        total = sum(row.values()) + _PSEUDOCOUNT * len(_READ_CLASSES)
        table[key] = MappingProxyType(
            {
                read_class: (row[read_class] + _PSEUDOCOUNT) / total
                for read_class in _READ_CLASSES
            }
        )
    return BaseErrorRates(
        table=MappingProxyType(table),
        observations=observations,
        skipped_reference_ambiguous=skipped_reference_ambiguous,
        skipped_read_ambiguous=skipped_read_ambiguous,
        skipped_long_deletions=skipped_long_deletions,
        skipped_trimmed=skipped_trimmed,
    )
