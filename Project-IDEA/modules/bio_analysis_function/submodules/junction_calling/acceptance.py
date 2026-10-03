"""接受判据（分片 C）：一条候选连接够不够格被报成证据。

上游在算完位置哈希分之后，还要过一串**支撑判据**才接受一条连接
（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)，
"Scoring and accepting junctions" 末段，原文四条）：

1. 必须有 read 比到这条连接的**两条链**上；
2. 必须有 read 在连接**两侧**各延伸进参考至少 **14 bp**；
3. 每条链上都要有 read 在连接两侧各延伸至少 **9 bp**；
4. 必须有一条 read，其**较短那一侧**也延伸进参考至少 **3 bp**。

"延伸进某一侧多少"在本实现里就是 `SupportingRead` 的两个 `unique_*`——那条 read 在连接两侧
各自独有的碱基个数（见 `candidates.ChimericPair`），正好是"read 从连接点往这一侧铺了多远"。

**一处明确不复刻的地方**：上游的接受阈值用的是 ``neg_log10_pos_hash_p_value``（"skew"）。
上游文档（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)
"Scoring and accepting junctions"）把它的算法写清楚了：

1. 对 **unique-only 参考位置**的读段深度拟合一个**截断负二项**（overdispersed Poisson）分布；
2. 统计"全基因组上有多大比例的「位置 × 链」组合有 read 起点落在那里"，作为**基线**；
3. 用这个基线算出"某位置某链在给定深度下至少有一条 read 从那里起"的概率；
4. **按二项分布**算观察到实际位置哈希分的概率，试验次数取"两倍读长"（每条链）；
5. 取负 log10 即 skew；**默认 skew > 3.0 判为不通过**（即 p < 0.001）。
   0.34.0 起还加了一条"饱和"修正：未被任何 read 起点占用的「位置 × 链」比例在深处**收敛到
   0.10**（`--junction-minimum-pr-no-read-start-per-position`），以免高覆盖样本里
   "几乎所有位置都有起点"把 skew 普遍抬高、误杀候选。

也就是说它是个**双向的异常检验**：起点分布太挤（read 挤在同一处）或太散都算异常，只有"像
基因组上一个普通位置那样"才通过。**我们没有复刻它**——第 1、3 步都要重做一套覆盖度拟合与
"起点基线"的口径，而我们**连上游那句"某位置某链在给定深度下至少有一条 read 从那里起"的
具体函数形式都没有公开依据**。宁可给一个能解释的阈值，也不去编一个看着像 p 值的数：这里
改成给**位置哈希分本身**一个可调下限，并把 :attr:`JunctionEvidence.skew_score`
恒置为 ``None``（把上游在 polymorphism 模式下的做法 ``NT`` 也用上）。

**与上游方向的差别要讲清楚**：上游是"起点分布一异常就拒"，我们是"起点数不够散就拒"
（``min_pos_hash_score``）。两者都在挡病理候选；我们这条更严，而且不依赖覆盖率模型——
代价是"由少数 read 从同一位置压过来、但确实是真连接"的情形我们可能漏掉。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .scoring import JunctionCandidate

__all__ = [
    "AcceptanceSettings",
    "JunctionEvidence",
    "accept_junctions",
    "evaluate_candidate",
    "evaluate_junctions",
]


@dataclass(frozen=True, slots=True)
class AcceptanceSettings:
    """四条支撑判据里的三个长度，加上位置哈希分的下限。"""

    #: 判据 2：至少有一条 read 在连接两侧各延伸这么长。
    min_extension_each_side: int = 14
    #: 判据 3：**每条链上**至少有一条 read 在两侧各延伸这么长。
    min_extension_per_strand: int = 9
    #: 判据 4：至少有一条 read 在两侧各延伸这么长。
    min_extension_any: int = 3
    #: 判据 1：是否要求两条链都有 read。上游是要求的，留成开关只为调试。
    require_both_strands: bool = True
    #: 位置哈希分下限。**这是我们的取值**，上游用的是 skew 的分位阈值（见模块文档）。
    min_pos_hash_score: int = 3


@dataclass(frozen=True, slots=True)
class JunctionEvidence:
    """一条候选连接的判定结果（不管接受没接受，都拿得到全部中间量）。"""

    candidate: JunctionCandidate
    #: 支撑 read 数。
    read_count: int
    #: 位置哈希分（不同起点数）。
    pos_hash_score: int
    #: 最小重叠分。
    min_overlap_score: int
    #: 支撑 read 里正链 / 负链各几条。
    plus_reads: int
    minus_reads: int
    #: 所有支撑 read 里"两侧独有碱基数较小值"的最大值（判据 2、4 看的就是它）。
    longest_extension: int
    #: 正链 / 负链各自的同一个量（判据 3 看的是这两者的较小值）。
    plus_longest_extension: int
    minus_longest_extension: int
    #: 没通过的判据名（空元组表示通过）。
    failures: tuple[str, ...]
    #: 恒为 ``None``：上游的 skew 分位值我们没有复刻（对应产物里的 `NT`）。
    skew_score: float | None = None

    @property
    def accepted(self) -> bool:
        """四条支撑判据与位置哈希分下限都过了才算接受。"""
        return not self.failures


def evaluate_candidate(
    candidate: JunctionCandidate,
    *,
    settings: AcceptanceSettings = AcceptanceSettings(),
) -> JunctionEvidence:
    """按四条支撑判据 + 位置哈希分下限判一条候选。"""
    support = candidate.support
    plus = [read for read in support if not read.is_reverse]
    minus = [read for read in support if read.is_reverse]
    longest = max((read.min_unique for read in support), default=0)
    longest_plus = max((read.min_unique for read in plus), default=0)
    longest_minus = max((read.min_unique for read in minus), default=0)

    failures: list[str] = []
    if settings.require_both_strands and not (plus and minus):
        failures.append("both_strands")
    if longest < settings.min_extension_each_side:
        failures.append("extension_each_side")
    if min(longest_plus, longest_minus) < settings.min_extension_per_strand:
        failures.append("extension_per_strand")
    if longest < settings.min_extension_any:
        failures.append("extension_any")
    if candidate.pos_hash_score < settings.min_pos_hash_score:
        failures.append("pos_hash_score")

    return JunctionEvidence(
        candidate=candidate,
        read_count=candidate.read_count,
        pos_hash_score=candidate.pos_hash_score,
        min_overlap_score=candidate.min_overlap_score,
        plus_reads=len(plus),
        minus_reads=len(minus),
        longest_extension=longest,
        plus_longest_extension=longest_plus,
        minus_longest_extension=longest_minus,
        failures=tuple(failures),
    )


def evaluate_junctions(
    candidates: Iterable[JunctionCandidate],
    *,
    settings: AcceptanceSettings = AcceptanceSettings(),
) -> tuple[JunctionEvidence, ...]:
    """逐条候选判定，**全部**结果都返回（接受与否由调用方看 `accepted`）。"""
    return tuple(evaluate_candidate(candidate, settings=settings) for candidate in candidates)


def accept_junctions(
    candidates: Iterable[JunctionCandidate],
    *,
    settings: AcceptanceSettings = AcceptanceSettings(),
) -> tuple[JunctionEvidence, ...]:
    """只返回**通过**的候选（对应上游"被接受为证据"的那些）。"""
    return tuple(
        evidence
        for evidence in evaluate_junctions(candidates, settings=settings)
        if evidence.accepted
    )
