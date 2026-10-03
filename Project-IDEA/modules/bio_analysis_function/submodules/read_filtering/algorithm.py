"""read_filtering：reads 过滤（质量 / N 含量 / 长度 / 低复杂度）。

**设计目标：与 fastp 1.3.x 的 ``Filter::passFilter``（``src/filter.cpp``）
逐位一致**，包括判定顺序、边界比较的方向（是 ``>`` 还是 ``>=``）、
以及平均质量那一处整数除法的口径。改这里之前请先读本文件的说明与
同目录 `read_filtering.md` 的设计记录：上游若干处"看起来可以简化"的写法是刻意的。

本文件只做单条 read 的判定，不读写文件；文件级接口见 ``runner.py``。

与相邻算法的分工：

- :mod:`quality_trimming` 是**改序列**（切掉低质量末端），本算法是**判去留**
  （整条 read 过不过关），两者互不修改对方的输入；
- 上游的处理顺序是"质量剪切 → poly 修剪 → 接头裁剪 → 过滤"，
  因此本算法的输入通常是已经修好的 read。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# 过滤结果码
#
# 数值与名称都取自上游 ``src/common.h``：那里刻意留出间隔（4、8、12……），
# 以便日后插入新类别，并且注释写明"数字越大表示越差"。照抄数值是为了让
# 统计口径、报告字段与上游对得上，不是为了好玩。
# ---------------------------------------------------------------------------

PASS_FILTER: Final = 0
FAIL_N_BASE: Final = 12
FAIL_LENGTH: Final = 16
FAIL_TOO_LONG: Final = 17
FAIL_QUALITY: Final = 20
FAIL_COMPLEXITY: Final = 24

#: 结果码 → 标签。名称与上游 ``FAILED_TYPES`` 一致，便于与 fastp 报告对照。
FAILURE_LABELS: Final[dict[int, str]] = {
    PASS_FILTER: "passed",
    FAIL_N_BASE: "failed_too_many_n_bases",
    FAIL_LENGTH: "failed_too_short",
    FAIL_TOO_LONG: "failed_too_long",
    FAIL_QUALITY: "failed_quality_filter",
    FAIL_COMPLEXITY: "failed_low_complexity",
}

#: 输出统计里按固定顺序排列的失败类别（顺序即报告顺序）。
FAILURE_ORDER: Final[tuple[int, ...]] = (
    FAIL_QUALITY,
    FAIL_N_BASE,
    FAIL_LENGTH,
    FAIL_TOO_LONG,
    FAIL_COMPLEXITY,
)

#: Phred+33 的字符偏移：字符码 = Q + 33。
_PHRED_OFFSET: Final = 33


def verdict_label(verdict: int) -> str:
    """把结果码转成与上游一致的标签。"""
    return FAILURE_LABELS.get(verdict, f"failed_unknown_{verdict}")


@dataclass(frozen=True, slots=True)
class ReadFilterConfig:
    """过滤参数。默认值与 fastp 命令行一致。

    三组过滤各自可开关，但**零长度 read 永远失败**（见 :func:`filter_verdict`），
    这一条不受开关控制——上游把"空 read"当作格式层面的异常，而不是过滤偏好。

    字段含义与上游参数：

    | 本字段 | fastp 参数 | 说明 |
    | --- | --- | --- |
    | `enabled_quality` | `--disable_quality_filtering` 的反面 | 质量过滤默认开启 |
    | `qualified_quality_phred` | `-q` / `--qualified_quality_phred` | 低于它的碱基算低质量 |
    | `unqualified_percent_limit` | `-u` / `--unqualified_percent_limit` | 低质量碱基比例上限（百分数） |
    | `n_base_limit` | `-n` / `--n_base_limit` | N 碱基个数上限 |
    | `average_qual` | `-e` / `--average_qual` | 平均质量下限，0 表示不限 |
    | `enabled_length` | `--disable_length_filtering` 的反面 | 长度过滤默认开启 |
    | `required_length` | `-l` / `--length_required` | 短于它的 read 被丢弃 |
    | `max_length` | `--length_limit` | 长于它的 read 被丢弃，0 表示不限 |
    | `enabled_complexity` | `-y` / `--low_complexity_filter` | 低复杂度过滤默认**关闭** |
    | `complexity_threshold` | `-Y` / `--complexity_threshold` | 复杂度下限，比例（0.3 = 30%） |
    """

    enabled_quality: bool = True
    qualified_quality_phred: int = 15
    unqualified_percent_limit: float = 40.0
    n_base_limit: int = 5
    average_qual: int = 0

    enabled_length: bool = True
    required_length: int = 15
    max_length: int = 0

    enabled_complexity: bool = False
    complexity_threshold: float = 0.3

    def __post_init__(self) -> None:
        # 校验只在此处做：这是用户会直接构造的对象，必须挡住越界值。
        # 内部热路径（filter_verdict）不再重复检查。
        if not 0 <= self.qualified_quality_phred <= 93:
            raise ValueError(
                f"qualified_quality_phred 必须在 0 到 93 之间，当前为 "
                f"{self.qualified_quality_phred}。"
            )
        if not 0 <= self.unqualified_percent_limit <= 100:
            raise ValueError(
                f"unqualified_percent_limit 必须在 0 到 100 之间，当前为 "
                f"{self.unqualified_percent_limit}。"
            )
        if self.n_base_limit < 0:
            raise ValueError(f"n_base_limit 不能为负数，当前为 {self.n_base_limit}。")
        if self.average_qual < 0:
            raise ValueError(f"average_qual 不能为负数，当前为 {self.average_qual}。")
        if self.required_length < 0:
            raise ValueError(f"required_length 不能为负数，当前为 {self.required_length}。")
        if self.max_length < 0:
            raise ValueError(f"max_length 不能为负数，当前为 {self.max_length}。")
        if not 0.0 <= self.complexity_threshold <= 1.0:
            raise ValueError(
                f"complexity_threshold 必须在 0 到 1 之间（0.3 表示 30%），"
                f"当前为 {self.complexity_threshold}。"
            )

    @property
    def qualified_code(self) -> int:
        """达标质量对应的**字符码**（Phred+33）。

        上游把阈值以字符形式存在配置里，解析命令行时用 ``num2qual`` 换算，
        并把越界值夹到 ``[0, 94]``；本实现对外用 Phred 值，在构造配置时
        直接拒绝越界值（而不是夹紧），换算放在这个属性里。
        """
        return self.qualified_quality_phred + _PHRED_OFFSET


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """一条 read 的质量统计（一次遍历同时算出三个量）。"""

    low_quality_bases: int
    """质量字符码严格小于阈值（Phred+33）的碱基数。"""

    n_bases: int
    """序列中大写 ``N`` 的个数。"""

    total_quality: int
    """所有碱基的 Phred 值之和（已减去 33）。"""


def count_quality_metrics(
    sequence: bytes,
    quality: bytes,
    *,
    qualified_code: int,
) -> QualityMetrics:
    """一次遍历统计低质量碱基数、N 个数与质量总和。

    上游在 ``fastp_simd::countQualityMetrics`` 里用 SIMD 做同一件事，
    语义完全一致：低质量判据是 ``quality[i] < qualified_code``（**严格小于**，
    所以恰好等于阈值算达标），质量累计的是 ``quality[i] - 33``。

    参数：
        sequence: 碱基序列。
        quality: 质量字符串，长度必须与 ``sequence`` 相同。
        qualified_code: 达标质量对应的字符码（即 ``Q + 33``）。
    """
    low_quality = 0
    n_bases = 0
    total = 0
    for base, code in zip(sequence, quality):
        total += code - _PHRED_OFFSET
        if code < qualified_code:
            low_quality += 1
        if base == 0x4E:  # ord("N")
            n_bases += 1
    return QualityMetrics(
        low_quality_bases=low_quality,
        n_bases=n_bases,
        total_quality=total,
    )


def passes_low_complexity(sequence: bytes, threshold: float) -> bool:
    """低复杂度判据：相邻碱基不同的比例是否达到阈值。

    复杂度定义为

    $$
    C = \\frac{\\#\\{i : s_i \\ne s_{i+1}\\}}{n - 1}
    $$

    即"每个碱基与它的下一个碱基不同"的比例。全同序列（``AAAA…``）为 0，
    随机序列约为 $1 - 1/4 = 0.75$（四碱基等概率时与下一碱基相同的概率是 1/4）。

    边界：``n <= 1`` 直接判为不通过（分母会是 0，上游明确返回 false）。
    """
    length = len(sequence)
    if length <= 1:
        return False
    differences = 0
    for index in range(length - 1):
        if sequence[index] != sequence[index + 1]:
            differences += 1
    return differences / (length - 1) >= threshold


def filter_verdict(
    sequence: bytes,
    quality: bytes,
    config: ReadFilterConfig | None = None,
) -> int:
    """判定一条 read 的去留，返回 :data:`PASS_FILTER` 或某个 ``FAIL_*``。

    判定顺序与上游 ``Filter::passFilter`` 一致，**顺序本身有意义**：

    1. 空 read（长度 0）直接判为 ``FAIL_LENGTH``，**不受长度过滤开关影响**。
       上游把它当作格式异常，此时连质量统计都不做。
    2. 质量过滤（开启时）：先看低质量碱基比例，再看平均质量，最后看 N 个数。
       三者是 ``else if`` 关系——**只报第一条命中的原因**，后面的不再检查。
    3. 长度过滤（开启时）：先"过短"后"过长"。
    4. 低复杂度过滤（开启时）。
    5. 都通过则返回 ``PASS_FILTER``。

    两个容易看错的口径：

    - 低质量比例用的是**浮点**比较 ``low_qual_num > limit * length / 100``，
      因此 ``limit * length`` 恰好整除、低质量数恰好等于该值时**通过**（不是 ``>=``）。
    - 平均质量用的是**整数除法** ``total_qual // length``（上游两个操作数都是
      ``int``），小数部分被丢掉。例如平均质量 14.9 与阈值 15 比较时，
      ``14 < 15`` 判定失败；而 15.0 恰好等于阈值时通过。

    参数：
        sequence: 碱基序列。
        quality: 质量字符串，长度必须与 ``sequence`` 相同。
        config: 过滤参数；``None`` 表示使用默认参数（与 fastp 命令行默认一致）。
    """
    if len(sequence) != len(quality):
        raise ValueError(
            f"序列长度（{len(sequence)}）与质量长度（{len(quality)}）不一致。"
        )

    config = config or ReadFilterConfig()
    length = len(sequence)

    # 1. 空 read：无论过滤开关如何，都是失败（上游写法如此）。
    if length == 0:
        return FAIL_LENGTH

    # 2. 质量过滤。统计只在需要时做——上游同样只在质量过滤开启时计算。
    if config.enabled_quality:
        metrics = count_quality_metrics(
            sequence, quality, qualified_code=config.qualified_code
        )
        if metrics.low_quality_bases > config.unqualified_percent_limit * length / 100.0:
            return FAIL_QUALITY
        if config.average_qual > 0 and metrics.total_quality // length < config.average_qual:
            return FAIL_QUALITY
        if metrics.n_bases > config.n_base_limit:
            return FAIL_N_BASE

    # 3. 长度过滤。
    if config.enabled_length:
        if length < config.required_length:
            return FAIL_LENGTH
        if config.max_length > 0 and length > config.max_length:
            return FAIL_TOO_LONG

    # 4. 低复杂度过滤。
    if config.enabled_complexity:
        if not passes_low_complexity(sequence, config.complexity_threshold):
            return FAIL_COMPLEXITY

    return PASS_FILTER


def passes_filter(
    sequence: bytes,
    quality: bytes,
    config: ReadFilterConfig | None = None,
) -> bool:
    """:func:`filter_verdict` 的布尔版本，用于只关心去留的场合。"""
    return filter_verdict(sequence, quality, config) == PASS_FILTER
