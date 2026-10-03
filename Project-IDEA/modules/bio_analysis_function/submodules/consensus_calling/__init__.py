"""共识碱基调用（consensus calling）：从比对结果判定样本在每个位置的碱基。

这是参考比对与变异检测线的第四项（算法清单 5.5.4），也是 breseq 的 `RA` 证据线
（read alignment evidence）里真正下结论的那一步：**堆叠 → 重校准错误率 → 按似然给分 →
和参考不一致就报突变**。上游的思路（[breseq 0.35.7 文档 · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)）
是三件事：

1. **read 端裁剪**：read 末端落在参考重复序列里的碱基对"这里到底有几个拷贝"是不确定
   证据（既可能是重复拷贝数变了，也可能只是比对上来的末端），必须先裁掉，否则会把真
   突变读成"反证"。
2. **碱基质量重校准**：不直接信 FASTQ 里的 Phred 质量，而是拿数据自己数——统计
   "参考碱基 × read 碱基 × 质量"的出现次数，加伪计数后当作经验错误率。理由与做法见
   文档里的设计记录。
3. **共识打分**：每个位置对四种碱基各算一个似然，取最大的那个；若它不是参考碱基，
   就用"log₁₀ 似然 − log₁₀ 参考总长"作为打分（减参考总长是为了把"全基因组这么多位置
   都试过了"的多重比较折算进去），配合频率阈值决定是否报出突变。

分片推进（每片带自己的测试）：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **堆叠（pileup）**：SAM → 逐参考位置的碱基/插入/缺失证据（`pileup.py`） | 已完成 |
| B | **碱基错误率重校准**：参考碱基 × read 碱基 × 质量 计数 + 伪计数（`error_rates.py`） | 已完成 |
| C | **共识打分与判定**：似然比打分 − log₁₀ 参考总长 + 频率阈值（`consensus.py`） | 已完成 |
| D | **read 端裁剪**：参考 1–18 bp 完全重复 → 每端至少裁 1 个碱基（`trimming.py`） | 已完成 |
| E | **多态档判定**：混群体里的少数派碱基——换用**混合模型**（`f·P(b|X) + (1−f)·P(b|R)` 取最大似然，比的是"纯参考解释"），产出 `prediction="polymorphism"` 的调用（`polymorphism.py`） | 已完成 |
"""

from __future__ import annotations

from .consensus import (
    ConsensusCall,
    ConsensusSettings,
    PositionScore,
    call_consensus,
    score_position,
)
from .error_rates import BaseErrorRates, build_error_rates
from .pileup import (
    BaseObservation,
    DeletionObservation,
    InsertionObservation,
    PileupColumn,
    iter_pileup,
)
from .polymorphism import (
    PolymorphismCall,
    PolymorphismScore,
    PolymorphismSettings,
    call_polymorphisms,
    call_variants,
    score_polymorphisms,
)
from .trimming import DEFAULT_MAX_UNIT, ReferenceTrimmer, build_trimming

__all__ = [
    "DEFAULT_MAX_UNIT",
    "BaseErrorRates",
    "BaseObservation",
    "ConsensusCall",
    "ConsensusSettings",
    "DeletionObservation",
    "InsertionObservation",
    "PileupColumn",
    "PolymorphismCall",
    "PolymorphismScore",
    "PolymorphismSettings",
    "PositionScore",
    "ReferenceTrimmer",
    "build_error_rates",
    "build_trimming",
    "call_consensus",
    "call_polymorphisms",
    "call_variants",
    "iter_pileup",
    "score_position",
    "score_polymorphisms",
]
