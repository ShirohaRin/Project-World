"""breseq 参考比对与变异检测工作流。

这是 `modules/workflow/` 里的**第二条流程**（第一条是 fastp 预处理，见上级目录的
`workflow.md`）：它自己不做判定，只把生物模块 `modules.bio_analysis_function` 已有的算法
按上游 breseq 的顺序串起来——**参考 + FASTQ 进，`.gd` 与注释表出**。

与 fastp 工作流最大的结构差异：**它不是单遍流程**。比对一次落盘成 SAM，后面的步骤都从 SAM 走
（理由与后果见 `runner.py` 的模块文档与 `breseq_workflow.md`）——这也让"拿 bowtie2 的 SAM
直接从后半段接进来"成为可能。

对外的三样东西：

- :class:`BreseqConfig`：一次分析的配置（参考、读段、产物目录、比对与调用参数）；
- :func:`run_breseq_workflow`：跑一次，产出 SAM / `.gd` / 注释表 / 摘要表；
- :class:`BreseqOutcome`：阶段 A 结论、比对统计与突变清单。

**本版范围**：RA 线（碱基替换，共识档 + 多态档）+ 突变注释 + `.gd`。MC / JC 两条证据线与
报告层还没接上，见 `breseq_workflow.md` 的「已知边界」。
"""

from __future__ import annotations

from .config import (
    ANNOTATION_NAME,
    DIFF_NAME,
    SAM_NAME,
    SUMMARY_COLUMNS,
    SUMMARY_NAME,
    BreseqConfig,
    BreseqOutcome,
    BreseqScanReport,
)
from .report import (
    BRESEQ_LOG_NAME,
    build_breseq_json,
    render_breseq_html,
    write_breseq_reports,
)
from .runner import load_reference, run_breseq_workflow
from .variants import (
    Variant,
    merge_variants,
    mutation_entries,
    to_diff_record,
    to_evidence_record,
    variants_from_calls,
)

__all__ = [
    "ANNOTATION_NAME",
    "BRESEQ_LOG_NAME",
    "DIFF_NAME",
    "SAM_NAME",
    "SUMMARY_COLUMNS",
    "SUMMARY_NAME",
    "BreseqConfig",
    "BreseqOutcome",
    "BreseqScanReport",
    "Variant",
    "build_breseq_json",
    "load_reference",
    "merge_variants",
    "mutation_entries",
    "render_breseq_html",
    "run_breseq_workflow",
    "to_diff_record",
    "to_evidence_record",
    "variants_from_calls",
    "write_breseq_reports",
]
