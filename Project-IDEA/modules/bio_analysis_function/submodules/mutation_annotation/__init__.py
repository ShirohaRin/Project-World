"""突变注释（mutation annotation）：把"参考上某个位置变了"翻译成"这对基因与蛋白意味着什么"。

这是参考比对与变异检测线的第五项（算法清单 5.5.4）。上游 breseq 的报告就是这一步的产物：

```text
RA  380,188  A→C  F239L (TTT→TTG)  araJ+  predicted transporter
RA  3,045,069  Δ16 bp  coding (96-111/4554 nt)  yghJ
RA  3,483,047  C→A  R455S (CGC→AGC)  malT→
RA  3,370,027  T→A  K117M (AAG→ATG)  rpsM+  30S ribosomal protein S13
BA  1,329,516  C→T  intergenic (-110/-179)  topA→
```

位置、碱基变化、氨基酸变化（含"第几个密码子、密码子怎么变"）、落在哪个基因上、
以及基因受什么影响——这些都要从参考的**特征表**（`common/reference_io` 给的 ``Feature`` /
``Location``）算出来。

分片推进（每片带自己的测试）：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **遗传密码与 CDS 翻译**（`translation.py`）：标准密码表、起始/终止密码子的口径 | 已完成 |
| B | **变异 → 密码子与氨基酸效应**（`effects.py`）：同义 / 错义 / 无义 / 终止丢失 / 起始改变、重叠基因 | 已完成 |
| C | **基因间与距离**（`intergenic.py`）：`intergenic (+22/-4)`、两侧最近基因与转录方向符号 | 已完成 |
| D | **端到端注释表**（`annotation.py`）：分类口径（coding / intergenic / noncoding / unannotated）、TSV 写出 | 已完成 |

**依赖边界**：本模块只依赖公共层 `common/reference_io`（坐标与特征表），不依赖其他子模块
（开发规则 3.1 第 4 条）。上游的变异调用结果通过**数据**进来（位置 + 参考碱基 + 判定碱基），
不通过 `import` 进来。
"""

from __future__ import annotations

from .annotation import (
    ANNOTATION_COLUMNS,
    AnnotationKind,
    NoncodingEffect,
    VariantAnnotation,
    annotate_variant,
    annotate_variants,
    iter_annotation_rows,
    write_annotation_table,
)
from .effects import (
    CodingEffect,
    GeneModel,
    Substitution,
    annotate_substitution,
    coding_offset,
)
from .intergenic import DEFAULT_GENE_KINDS, IntergenicEffect, intergenic_effect
from .translation import (
    CODON_TABLE,
    START_CODONS,
    STOP,
    UNKNOWN_RESIDUE,
    CdsTranslation,
    cds_translation,
    translate,
    translate_cds,
    translate_features,
)

__all__ = [
    "ANNOTATION_COLUMNS",
    "AnnotationKind",
    "CODON_TABLE",
    "DEFAULT_GENE_KINDS",
    "START_CODONS",
    "STOP",
    "UNKNOWN_RESIDUE",
    "CdsTranslation",
    "CodingEffect",
    "GeneModel",
    "IntergenicEffect",
    "NoncodingEffect",
    "Substitution",
    "VariantAnnotation",
    "annotate_substitution",
    "annotate_variant",
    "annotate_variants",
    "cds_translation",
    "coding_offset",
    "intergenic_effect",
    "iter_annotation_rows",
    "translate",
    "translate_cds",
    "translate_features",
    "write_annotation_table",
]
