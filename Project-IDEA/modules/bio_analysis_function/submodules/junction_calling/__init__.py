"""junction 检测与打分（JC）：从嵌合比对里找出"参考上原本不相连的两段被接上了"。

这是参考比对与变异检测线的第六项（算法清单 5.5.4），对应上游 breseq 的 `JC` 证据线
（new junction evidence）。上游的思路（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)
"New junction evidence (JC)"）：

1. **预处理**：把带 **> 2 bp 空位**的比对拆成子比对（长空位在比对里本来就不稳）。
2. **找候选**：一条 read 的多段比对两两组合，用五条判据筛出"两段拼起来明显比任何单段解释得更好"
   的对，据此拼出**候选连接序列**（左段贴断点的参考碱基 + 中间那截 read 碱基 + 右段从断点起的
   参考碱基）。
3. **打分与截断**：数"支持这条连接的 read 有多少个不同的起点"（位置哈希分），并列时用
   "每条 read 两侧独有碱基数的较小值之和"（最小重叠分）；按这两个分数保留到累计长度超过
   0.1×参考总长或候选数超过 5000。
4. **重比与接受**：把所有 read 再比到候选连接序列上，重算位置哈希分，按其显著性（"skew"）
   与一串支撑判据决定接受与否。

它管的是**结构变异**：`JC + JC → MOB`（移动元件插入）、`JC → AMP`（扩增）、
`JC + MC → DEL`（精确端点的大片段缺失）、还有插入/缺失/替换的 junction 形态。

分片推进（每片带自己的测试）：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **分段与候选对**：>2 bp 空位拆段 + 五条判据 + 连接序列构造（`segments.py` / `candidates.py`） | 已完成 |
| B | **打分与排序**：位置哈希分、最小重叠分、按上游两条上限截断（`scoring.py`） | 已完成 |
| C | **接受判据**：两条链、两侧各 14 bp、每条链 9 bp、较短一侧 3 bp，加位置哈希分下限（`acceptance.py`） | 已完成（**重比那一步未做**，见文档） |
| D | **证据行与端到端**：`JC` 证据行构造、合成已知结构变异对拍 | 待做 |
"""

from __future__ import annotations

from .acceptance import (
    AcceptanceSettings,
    JunctionEvidence,
    accept_junctions,
    evaluate_candidate,
    evaluate_junctions,
)
from .candidates import (
    CandidateSettings,
    ChimericPair,
    build_junction_sequence,
    group_segments_by_read,
    iter_chimeric_pairs,
    longest_read_length,
)
from .scoring import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_CUMULATIVE_FRACTION,
    JunctionCandidate,
    JunctionKey,
    SupportingRead,
    call_junctions,
    group_candidates,
    rank_candidates,
)
from .segments import AlignmentSegment, MIN_SPLIT_GAP, segments_of

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_CUMULATIVE_FRACTION",
    "MIN_SPLIT_GAP",
    "AcceptanceSettings",
    "AlignmentSegment",
    "CandidateSettings",
    "ChimericPair",
    "JunctionCandidate",
    "JunctionEvidence",
    "JunctionKey",
    "SupportingRead",
    "accept_junctions",
    "build_junction_sequence",
    "call_junctions",
    "evaluate_candidate",
    "evaluate_junctions",
    "group_candidates",
    "group_segments_by_read",
    "iter_chimeric_pairs",
    "longest_read_length",
    "rank_candidates",
    "segments_of",
]
