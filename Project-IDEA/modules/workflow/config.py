"""工作流的配置与结果类型。

workflow 是 ``modules/`` 下的**能力模块**，不再属于生物方法模块内部：它把生物模块
已经实现好的算法按上游 fastp 的顺序串成一条流程，回答的问题是"这份原始数据经过
标准预处理之后变成什么样、中间掉了多少"。它跨模块依赖
``modules.bio_analysis_function`` 的公共层与原生库，依赖关系在 ``workflow.md`` 里声明。

因此本目录的文件布局与算法子模块不同：没有 ``algorithm.py``（没有新算法），
配置与结果类型单独放在这里，编排逻辑在 ``runner.py``，报告在 ``report.py``。

各步的子配置类型直接复用生物模块 ``common/native/abi.py`` 里的那几个 spec——它们本来
就是按 fastp 命令行默认值写的，再包一层只会多出一份要同步维护的字段表。
本文件只负责那些**工作流层面**的决策：阶段 A 怎么跑、双端要不要校正、
落单 read 去哪、分卷怎么算。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..bio_analysis_function.common.native.abi import (
    CutSpec,
    FilterSpec,
    NativeReadStats,
    NativeWorkflowSummary,
    OverlapSpec,
    PolySpec,
    UmiSpec,
)

__all__ = [
    "AdapterDetectionReport",
    "CutSpec",
    "FilterSpec",
    "OverlapSpec",
    "PolySpec",
    "ScanReport",
    "UmiSpec",
    "WorkflowConfig",
    "WorkflowOutcome",
]


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """一次预处理的完整配置。

    默认值走的是 **fastp 命令行的默认行为**：接头裁剪与 reads 过滤开着、
    质量剪切与 poly 修剪要显式开、不去重、不合并。与 ``abi.WorkflowRequest``
    的区别就在这里——那一层是"什么都不做"的中性默认，这一层是产品默认。

    各项与 fastp 命令行的对应写在字段后面，便于逐项核对。
    """

    # --- 输入输出（必填） ---
    read1: Path
    output1: Path

    # --- 输入输出（双端 / 可选） ---
    read2: Path | None = None
    output2: Path | None = None
    #: 落单 read 的去向。``None`` 表示与输出同目录下的 ``unpaired.fq``；
    #: 传空串表示**丢弃**（上游不给 ``--unpaired1`` 时的行为）。
    unpaired: Path | str | None = None
    failed_out: Path | None = None  # --failed_out
    merged_out: Path | None = None  # --merged_out（非空即进入合并模式）
    overlapped_out: Path | None = None  # --overlapped_out

    # --- 规范化（--phred64 / --fix_mgi_id）---
    normalize: bool = False
    input_phred: int = 33
    fix_mgi: bool = False

    # --- 首尾修剪 + 滑窗质量剪切（-f/-t 与 -5/-3/-r）---
    cut: CutSpec = field(default_factory=CutSpec)
    #: 限长截断（--max_len1/2）。**这一项只在本层实现**，单独的质量剪切模块没有。
    max_length1: int = 0
    max_length2: int = 0

    # --- poly 修剪 ---
    poly: PolySpec = field(default_factory=PolySpec)
    #: ``None`` 表示"由数据决定"：二色测序仪的数据自动开 polyG（上游行为）。
    #: ``True``/``False`` 是用户显式指定，优先级高于自动判定。
    trim_poly_g: bool | None = None

    # --- 接头裁剪 ---
    adapter_trimming: bool = True
    #: ``None`` 表示按上游规则决定：单端自动检测、双端要显式打开。
    detect_adapter: bool | None = None
    #: 显式给定的接头（给了就不检测）。
    adapter1: str = ""
    adapter2: str = ""
    #: 多接头候选表（对应 --adapter_fasta，会与 adapter1/2 合并）。
    extra_adapters: Sequence[str] = ()
    adapter_allow_one_gap: bool = False  # --allow_gap_overlap_trimming
    adapter_dimer: bool = True           # 接头二聚体判定（--dimer_max_len）
    adapter_dimer_max_len: int = 2

    # --- reads 过滤 ---
    filter: FilterSpec = field(default_factory=FilterSpec)

    # --- 双端专用 ---
    overlap: OverlapSpec = field(default_factory=OverlapSpec)
    correction: bool = False          # --correction
    merge: bool = False               # --merge
    merge_include_unmerged: bool = False  # --include_unmerged

    # --- 其他预处理 ---
    dedup: bool = False               # --dedup
    dedup_evaluate: bool = True       # --dont_eval_duplication 的反面
    dedup_accuracy_level: int = 0     # --dup_calc_accuracy；0 = 按模式取默认
    #: 覆盖档位给的"每个缓冲区多少字节"。0 表示按档位（档位 1 就是 1 GiB）。
    #: 主要给两类场景用：内存紧张的机器压低占用，以及测试里把位图缩到很小
    #: 以免每次跑都提交 1 GiB 内存。**它会改变假阳性率**，因此两侧要对拍时
    #: 必须给同值。
    dedup_buffer_bytes: int = 0
    umi: UmiSpec | None = None        # --umi 系列
    index_blacklist1: Sequence[str] = ()  # --filter_by_index1 的内容
    index_blacklist2: Sequence[str] = ()
    index_threshold: int = 0          # --filter_by_index_threshold

    # --- 输出组织 ---
    compress: bool | None = None      # None = 跟随输入
    #: 每卷多少条 read（上游 ``--split_by_lines`` 的口径）。0 = 不分卷。
    split_records: int = 0
    #: 按文件数分卷（上游 ``--split``）。> 1 时本层会先数一遍总条数，
    #: 再换算成 ``split_records``——比上游的估算精确。
    split_number: int = 0
    split_digits: int = 4

    # --- 运行 ---
    threads: int = 0                  # <= 0 由原生层决定
    max_reads: int = 0                # --reads_to_process
    report_title: str = "IDEA 预处理报告"

    def __post_init__(self) -> None:
        if self.split_records < 0:
            raise ValueError(f"每卷条数不能为负数，当前为 {self.split_records}。")
        if self.split_number < 0:
            raise ValueError(f"分卷份数不能为负数，当前为 {self.split_number}。")
        if self.split_records and self.split_number:
            raise ValueError(
                "每卷条数与分卷份数是两种口径，只能给一个（另一个留 0）。"
            )
        if self.split_number == 1:
            raise ValueError("分卷份数为 1 等于不分卷，请留 0。")
        if self.input_phred not in (33, 64):
            raise ValueError(f"质量编码只支持 33 或 64，当前为 {self.input_phred}。")
        if self.merge and not self.read2:
            raise ValueError("合并模式只适用于双端输入。")
        if self.merge and self.merged_out is None:
            raise ValueError("合并模式需要指定 merged_out 输出路径。")
        if self.merge_include_unmerged and not self.merge:
            raise ValueError("merge_include_unmerged 只在合并模式下有意义。")
        if self.correction and not self.read2:
            raise ValueError("碱基校正只适用于双端输入。")
        if not self.read2 and (self.unpaired or self.overlapped_out):
            raise ValueError("落单 read 与重叠区输出只适用于双端输入。")

    @property
    def paired(self) -> bool:
        """是否双端。"""
        return self.read2 is not None

    @property
    def merged(self) -> bool:
        """是否进入合并模式（由 ``merged_out`` 决定，与 ``merge`` 取或）。"""
        return self.merge or self.merged_out is not None

    def splitting(self) -> bool:
        """是否分卷。"""
        return self.split_records > 0 or self.split_number > 0


@dataclass(frozen=True, slots=True)
class AdapterDetectionReport:
    """阶段 A 的接头检测结论。

    ``source1`` / ``source2`` 取 ``"known"``（命中已知接头表）、``"kmer"``
    （从数据里拼出来的）或空串（没检测到）。
    """

    adapter1: str = ""
    adapter2: str = ""
    source1: str = ""
    source2: str = ""
    seed1: str = ""
    seed2: str = ""
    sampled_reads: int = 0


@dataclass(frozen=True, slots=True)
class ScanReport:
    """阶段 A（预扫描）的全部结论。

    这一阶段做的三件事各自都是**独立可跑的算法**，结论在这里汇总，供报告如实
    写出"用了什么参数、为什么这么用"。
    """

    #: 全文件 read 条数。只在需要按文件数分卷时才真的去数（要读一遍文件）。
    read_count: int = 0
    #: 是否二色合成化学测序仪的数据（决定 polyG 是否自动开启）。
    is_two_color: bool = False
    #: polyG 最终是否开启（用户指定优先，其次看二色判定）。
    poly_g_enabled: bool = False
    #: 接头检测结论（未启用检测时为空）。
    adapter: AdapterDetectionReport = field(default_factory=AdapterDetectionReport)


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    """一次工作流的完整产出：配置、阶段 A 结论、统计、报告数据、写出路径。"""

    config: WorkflowConfig
    scan: ScanReport
    summary: NativeWorkflowSummary
    pre_stats1: NativeReadStats
    pre_stats2: NativeReadStats
    post_stats1: NativeReadStats
    post_stats2: NativeReadStats
    #: 插入片段长度直方图，下标即长度，最后一项是溢出桶（判不出或超上限）。
    insert_size_histogram: tuple[int, ...]
    #: 实际写出的文件（分卷时是全部卷）。
    outputs: tuple[Path, ...]
    #: 分卷产物，按序号排列；不分卷时为空。
    split_files: tuple[Path, ...] = ()
    #: 各步统计所在的那份 JSON（写进 log 目录，不呈现给用户）。
    log_path: Path | None = None
    #: 呈现给用户的 HTML 报告。
    html_path: Path | None = None

    @property
    def paired(self) -> bool:
        """是否双端。"""
        return self.config.paired

    @property
    def retention_rate(self) -> float:
        """保留下来的 read 比例。输入为空时返回 0.0。"""
        if not self.summary.total_reads:
            return 0.0
        return self.summary.output_reads / self.summary.total_reads

    @property
    def base_retention_rate(self) -> float:
        """保留下来的碱基比例。输入为空时返回 0.0。"""
        if not self.summary.total_bases:
            return 0.0
        return self.summary.output_bases / self.summary.total_bases
