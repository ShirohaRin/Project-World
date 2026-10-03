"""Genome Diff（``.gd``）读写：参考比对线的产物契约。

``.gd`` 是 breseq 的机器可读产物：突变一行、证据一行，突发行用编号引用证据行。
本项目产出同样的文件，好处有两个——**结果可以机读**（不是只有 HTML 能看）、
**能和上游逐条对拍**（算法清单 5.5.3 定的验收口径就是比突变集合）。它也是将来做
gdtools 等价物（比较多样本、给参考应用/撤销突变）的基础。

分片推进：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **格式模型与解析**（`model.py` / `text.py`）：头、突变记录、证据记录、逐字节往返 | 已完成 |
| B | **从我们的结果构造**（`build.py`）：替换 / 缺失 / 插入 / read 证据 + 装配编号（吃纯数据，不反向依赖算法层） | 已完成 |
| C1 | **比较多份 `.gd`**（`compare.py`）：一行一条突变、一列一个样本（对应上游 `gdtools COMPARE`） | 已完成 |
| C2 | **给参考应用突变**（`apply.py`）：按坐标从后往前应用，产出更新后的序列与突变的新位置（对应上游 `gdtools APPLY`） | 已完成 |

**它是工具不是算法**（见 `开发规则.md` 3.2 第 1 类）：没有自己的输入输出契约要做成算法广场
入口，也不产出统计结果——只有"读进来、写出去"。

用法：

```python
from modules.bio_analysis_function.common.genome_diff import (
    MutationEntry,
    assemble_diff,
    format_genome_diff,
    read_alignment_evidence,
    read_genome_diff,
    substitution_record,
    write_genome_diff,
)

# 读
diff = read_genome_diff("output.gd")
diff.header.value_of("PROGRAM")          # 'breseq 0.33.1 revision 8505477f25b3'
for record in diff.mutations():          # 只看突变（RA / MC / JC 那些是证据）
    print(record.type, record.position, record.frequency, record.attributes)

text = format_genome_diff(diff)          # 正常文件与原文逐字节一致
write_genome_diff("copy.gd", diff)       # 写回，返回记录条数

# 写：一条替换 + 支持它的证据（编号与证据列由装配器分配/回填）
diff = assemble_diff(
    [
        MutationEntry(
            mutation=substitution_record(
                seq_id="NC_001422", position=1234, call_base="T", frequency=0.98
            ),
            evidence=(
                read_alignment_evidence(
                    seq_id="NC_001422", position=1234,
                    reference_base="C", call_base="T", frequency=0.98,
                    consensus_score=133.2, prediction="consensus",
                ),
            ),
        )
    ],
    header_entries=(("PROGRAM", "Project IDEA bio"), ("REFSEQ", "NC_001422.gbk")),
)
```

**未做的**：跨复制原点的突变（环状参考上跨原点，报错说明）、`MOB` / `AMP` / `CON` / `INV` 的
应用、上游 `gdtools` 的其它子命令（`SUBTRACT` / `INTERSECT` / `UNION` / `FILTER` 与格式转换）。
另外本层**不做**注释——`gene_name` 之类的属性由突变注释模块算出来之后，通过构造函数的
``attributes`` 参数填进来。
"""

from __future__ import annotations

from .apply import (
    SUPPORTED_TYPES,
    AppliedGenome,
    AppliedMutation,
    apply_diff,
    apply_mutations,
)
from .build import (
    MutationEntry,
    assemble_diff,
    deletion_record,
    insertion_record,
    read_alignment_evidence,
    substitution_record,
)
from .compare import (
    COMPARISON_COLUMNS,
    ComparisonRow,
    ComparisonTable,
    MutationKey,
    compare_files,
    compare_samples,
)
from .model import (
    EVIDENCE_TYPES,
    GD_VERSION,
    GenomeDiff,
    GenomeDiffHeader,
    GenomeDiffRecord,
)
from .text import (
    format_genome_diff,
    parse_genome_diff,
    read_genome_diff,
    write_genome_diff,
)

__all__ = [
    "COMPARISON_COLUMNS",
    "EVIDENCE_TYPES",
    "GD_VERSION",
    "SUPPORTED_TYPES",
    "AppliedGenome",
    "AppliedMutation",
    "ComparisonRow",
    "ComparisonTable",
    "GenomeDiff",
    "GenomeDiffHeader",
    "GenomeDiffRecord",
    "MutationEntry",
    "MutationKey",
    "apply_diff",
    "apply_mutations",
    "assemble_diff",
    "compare_files",
    "compare_samples",
    "deletion_record",
    "format_genome_diff",
    "insertion_record",
    "parse_genome_diff",
    "read_alignment_evidence",
    "read_genome_diff",
    "substitution_record",
    "write_genome_diff",
]
