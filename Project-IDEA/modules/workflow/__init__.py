"""fastp 预处理的完整工作流编排。

workflow 住在 ``modules/workflow/``，是**能力层**的模块：它自己不做判定，而是把生物
模块（``modules.bio_analysis_function``）已有的算法按上游 fastp 的顺序串成一条流程：

    规范化 → 按 index 过滤 → 去重（读取端，保序）
    首尾修剪 + 滑窗质量剪切 → polyG → 接头裁剪 → 碱基校正 → 合并（计算端，并行）
    reads 过滤 → 五路分流写出（写出端，保序）

**一次读入、逐条走完整条链**，省掉"每个算法各读一遍写一遍"的十几倍 I/O。
每一步调的都是对应算法的内部实现（同一份代码），口径与单独调用完全一致。

对外的三样东西：

- :class:`WorkflowConfig`：一次预处理的配置，默认值走 fastp 命令行默认行为；
- :func:`run_workflow`：跑一次，产出文件 + 报告；
- :class:`WorkflowOutcome`：统计、阶段 A 的结论、输出文件清单。

设计取舍、跨模块依赖与逐步顺序见 ``workflow.md``。

> **本模块下有两条流程**：这条（fastp 预处理，本目录根下的 `config/runner/report/wrapper.py`）
> 与 **breseq 参考比对与变异检测**（`breseq/` 子目录，见 `breseq/breseq_workflow.md`）。
> 两条同属能力层、各自独立：breseq 那条**不是单遍流程**（比对先落盘成 SAM），
> 也不依赖原生库，所以刻意没有复用这里的文件。
"""

from __future__ import annotations

from .config import (
    AdapterDetectionReport,
    CutSpec,
    FilterSpec,
    OverlapSpec,
    PolySpec,
    ScanReport,
    UmiSpec,
    WorkflowConfig,
    WorkflowOutcome,
)
from .report import build_workflow_json, render_workflow_html
from .runner import run_workflow

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
    "build_workflow_json",
    "render_workflow_html",
    "run_workflow",
]
