"""breseq 工作流的配置与结果类型。

字段分三组：**输入**（参考 + FASTQ）→ **输出**（都从 `output_dir` 派生）→ **参数**
（比对、共识/多态档阈值、read 端裁剪开关、读入上限）。默认值尽量走上游 breseq 的行为：
consensus 模式（`polymorphism` 字段给 `None` 就是只跑共识档）、E-value 10 与频率 0.8、
错误率重校准用数据自训练。

**为什么输出只用 `output_dir` 一个旋钮**：这条流程的产物是一组相互引用的文件
（`.gd` 引 SAM、报告引 `.gd`、注释表与 `.gd` 同行数），逐个给路径只会让调用方有机会
把一组本该配套的文件拆到不同目录。所以除两份**可选**报告（`html_path` / `log_dir`，
不给就不写，与 fastp 工作流同一约定）之外，其余产物按固定名字落在 `output_dir` 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...bio_analysis_function.submodules.consensus_calling import (
    ConsensusSettings,
    PolymorphismSettings,
)
from ...bio_analysis_function.submodules.read_mapping import MappingParams
from .variants import Variant

__all__ = [
    "ANNOTATION_NAME",
    "DIFF_NAME",
    "SAM_NAME",
    "SUMMARY_COLUMNS",
    "SUMMARY_NAME",
    "BreseqConfig",
    "BreseqOutcome",
    "BreseqScanReport",
]

#: 产物固定文件名（`output_dir` 下的相对名）。
SAM_NAME = "mapped.sam"
DIFF_NAME = "output.gd"
ANNOTATION_NAME = "annotations.tsv"
SUMMARY_NAME = "variant_summary.tsv"

#: 摘要表的列（一条突变一行，给人扫一眼用；注释明细在 `annotations.tsv`）。
SUMMARY_COLUMNS: tuple[str, ...] = (
    "seq_id",
    "position",
    "type",
    "reference_base",
    "call_base",
    "frequency",
    "prediction",
    "score",
    "annotation",
)


@dataclass(frozen=True, slots=True)
class BreseqConfig:
    """一次 breseq 分析的配置。"""

    #: 参考（GenBank 或 FASTA，按后缀识别）——染色体与质粒可以给多个文件。
    reference: tuple[Path, ...]
    #: 读段：单端只给 `read1`；双端给两个。
    read1: Path
    read2: Path | None = None
    #: 产物目录（`.gd` / SAM / 注释表都落在这里，名字固定）。
    output_dir: Path = Path("breseq_out")

    #: 比对参数（种子长度、步长、错配上限、软剪裁上限……）。
    mapping: MappingParams = MappingParams()
    #: 共识档阈值（默认 E-value 10 / 频率 0.8，上游默认值）。
    consensus: ConsensusSettings = ConsensusSettings()
    #: 多态档阈值；给 `None` 表示**只跑共识档**（上游的默认模式）。
    polymorphism: PolymorphismSettings | None = PolymorphismSettings()
    #: `QUAL` 为 `*` 时用的质量（默认 0，最保守）。
    default_quality: int = 0
    #: 是否用 read 端裁剪（参考 1–18 bp 完全重复）。上游默认裁，这里也默认开。
    trim_read_ends: bool = True
    #: 最多读入多少条 read（调试与小样本复现用）；`None` = 不限。
    max_reads: int | None = None
    #: 写进 `.gd` 头部的 `#=PROGRAM`。
    program: str = "Project-IDEA breseq workflow"

    def __post_init__(self) -> None:
        # 允许调用方给 list，但内部统一成 tuple（冻结对象只能这样改）。
        object.__setattr__(self, "reference", tuple(self.reference))
        if not self.reference:
            raise ValueError("至少要给一条参考。")
        if self.read2 is not None and self.read2 == self.read1:
            raise ValueError("双端的两条 read 文件不能是同一个。")
        if self.max_reads is not None and self.max_reads < 1:
            raise ValueError(f"读入上限必须 ≥ 1，当前为 {self.max_reads}。")
        if self.default_quality < 0:
            raise ValueError(f"默认质量不能为负，当前为 {self.default_quality}。")

    @property
    def paired(self) -> bool:
        """是否双端输入。"""
        return self.read2 is not None

    @property
    def sam_path(self) -> Path:
        """比对结果（也是这条流程与外部交换的中间产物）。"""
        return self.output_dir / SAM_NAME

    @property
    def diff_path(self) -> Path:
        """`.gd` 产物。"""
        return self.output_dir / DIFF_NAME

    @property
    def annotation_path(self) -> Path:
        """注释表（一个效应一行）。"""
        return self.output_dir / ANNOTATION_NAME

    @property
    def summary_path(self) -> Path:
        """变异摘要表（一条突变一行，给人扫一眼用）。"""
        return self.output_dir / SUMMARY_NAME


@dataclass(frozen=True, slots=True)
class BreseqScanReport:
    """阶段 A 的结论：读进来的是什么、有多少。"""

    seq_ids: tuple[str, ...]
    reference_length: int
    feature_count: int
    reads: int
    read_pairs: bool
    shortest_read: int
    longest_read: int
    mean_read_length: float
    bases: int
    #: 是否因为 `max_reads` 只读了一部分。
    cropped: bool = False


@dataclass(frozen=True, slots=True)
class BreseqOutcome:
    """一次 run 的全部结果（报告与调用方都从这里取值）。"""

    config: BreseqConfig
    scan: BreseqScanReport
    mapped_reads: int
    unmapped_reads: int
    unique_reads: int
    repeat_reads: int
    variants: tuple[Variant, ...]
    outputs: tuple[Path, ...]
    html_path: Path | None = None
    log_path: Path | None = None

    @property
    def mapped_fraction(self) -> float:
        """比对上的比例（按 read 条数）。"""
        total = self.mapped_reads + self.unmapped_reads
        return self.mapped_reads / total if total else 0.0

    @property
    def consensus_variants(self) -> tuple[Variant, ...]:
        """共识档的突变。"""
        return tuple(item for item in self.variants if item.prediction == "consensus")

    @property
    def polymorphism_variants(self) -> tuple[Variant, ...]:
        """多态档的突变。"""
        return tuple(item for item in self.variants if item.prediction == "polymorphism")
