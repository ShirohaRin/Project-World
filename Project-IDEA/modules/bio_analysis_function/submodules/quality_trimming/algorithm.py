"""reads 滑窗质量剪切（sliding window quality trimming）。

本模块实现 fastp 的 ``cut_front`` / ``cut_right`` / ``cut_tail`` 三种质量剪切模式。
它是 reads 质控流水线里第一个需要算法设计的步骤：固定位置修剪只需要一个整数，
而"按质量决定切到哪里"必须逐条 read 计算。

**设计目标：与 fastp 1.3.x 的 `Filter::trimAndCut`（``src/filter.cpp``）逐位一致**，
包括所有边界行为。因此本文件的控制流刻意保持与上游相同的结构——
滚动求和的初始化个数、循环的退出值、"是否把命中的窗口一起切掉"，
这些都按上游原样保留，不做"看起来更优雅"的重构，以便逐条对照验证。


## 一、这个算法要解决什么问题

Illumina 测序是边合成边测序。随着循环数增加，三个物理过程会让信号逐渐劣化：
荧光基团的光漂白与残留、簇内分子的相位不同步（phasing / pre-phasing）、
聚合酶活性衰减。结果是**一条 read 从头到尾质量单调下降**，末端基本是噪声。

这些末端碱基既不能"整条丢弃"（前面几百 bp 还是好的），也不能"切固定长度"
（每条 read 的拐点不同）。所以必须**逐条 read、按质量数据本身**决定切到哪里。


## 二、Phred 质量值

FASTQ 第三行不是文本，而是**打包成 ASCII 的整数数组**，每个字符编码一个碱基的
错误概率估计：

    Q = -10 * log10(P_error)      <=>      P_error = 10^(-Q/10)

    Q20 -> 1% 错误率      Q30 -> 0.1% 错误率      Q40 -> 0.01% 错误率

取对数是因为错误是独立事件、概率相乘，取对数后变成相加，避免连乘下溢。
存储用 Phred+33 编码：``字符码 = Q + 33``（Sanger / Illumina 1.8+ 标准），
所以 ``"!" = 0``、``"5" = 20``、``"?" = 30``、``"I" = 40``。


## 三、为什么用窗口平均而不是单碱基判定

测序错误是**成簇出现**的：某个位置的信号塌了，往往连续几个碱基都低，
但中间可能偶然冒一个高值。逐碱基判定会被这种噪声打断，切在错误的位置上。

所以用**局部平均**：把连续 ``window`` 个碱基看作一个整体，判断这个小段是否达标。
窗口内的噪声被平均掉，真正的质量拐点才会显现。

判定条件是窗口内平均质量不低于阈值：

    (1/window) * SUM(Q_i) >= Q_thresh

两边同乘 window、再代入 ``Q_i = c_i - 33``（``c_i`` 为字符码）：

    SUM(c_i) >= window * (33 + Q_thresh)

**这就是上游源码里那个 ``totalQual >= w * (33 + q)``**——它不是优化技巧，
而是上面不等式的精确等价形式，只为了避免浮点除法。源码注释也写明了这一点。


## 四、三种模式的区别

三者共用同一套窗口判定，区别只在**找什么、往哪切**：

``cut_front``
    从 5' 往 3' 滑，找**第一个**平均质量达标的窗口，然后**从该窗口之后**开始保留。
    注意切点取 ``s + window - 1``——连找到的这个"好窗口"本身也一起切掉。
    这是保守策略：既然前面刚跌到不合格，这个刚好达标的窗口也不放心。
    切完还要剥掉紧接着的前导 N。

``cut_tail``
    从 3' 往 5' 滑，找从右边数**第一个**平均质量达标的窗口，
    保留到**该窗口的起始位置**为止，其余全切。切完剥掉尾部 N。

``cut_right``
    从 5' 往 3' 滑，找**第一个**平均质量低于阈值的窗口，然后只保留这个坏窗口里
    仍达标的**单碱基前缀**，后面全切。比 cut_tail 激进得多——它会砍掉"后面又好回来"
    的区域。**两者同时开启时只执行 cut_right**，这是上游的明确行为。
"""

from __future__ import annotations

from dataclasses import dataclass

_PHRED_OFFSET = 33
"""Phred+33 编码的偏移量：字符码 = Q + 33。"""

_N_CODE = ord("N")
"""字符 ``N`` 的 ASCII 码。低质量区常以 N 结尾，剪切后需要顺带剥掉。"""


@dataclass(frozen=True, slots=True)
class QualityCutConfig:
    """滑窗质量剪切的参数。默认值与 fastp 1.3.x 一致。

    三个模式各自有窗口大小与质量阈值。上游还允许它们共享一组
    ``-W/--cut_window_size`` 与 ``-M/--cut_mean_quality``，那是命令行层的
    参数继承逻辑，不属于算法本身；本模块要求显式给出各自的值。
    """

    enabled_front: bool = False
    enabled_right: bool = False
    enabled_tail: bool = False
    window_size_front: int = 4
    window_size_right: int = 4
    window_size_tail: int = 4
    quality_front: int = 20
    quality_right: int = 20
    quality_tail: int = 20

    def __post_init__(self) -> None:
        for name, value in (
            ("window_size_front", self.window_size_front),
            ("window_size_right", self.window_size_right),
            ("window_size_tail", self.window_size_tail),
        ):
            if value < 1:
                raise ValueError(f"{name} 必须不小于 1，当前为 {value}。")
        for name, value in (
            ("quality_front", self.quality_front),
            ("quality_right", self.quality_right),
            ("quality_tail", self.quality_tail),
        ):
            # Phred+33 的字符范围是 '!'(33) 到 '~'(126)，因此 Q 的上限是 93。
            if not 0 <= value <= 93:
                raise ValueError(f"{name} 必须在 0 到 93 之间，当前为 {value}。")


@dataclass(frozen=True, slots=True)
class TrimResult:
    """剪切后仍然保留的 read。

    ``front_trimmed`` 是从头部一共去掉的碱基数，**同时包含固定修剪量和质量剪切量**。
    上游把它作为输出参数传给调用方，用于双端合并时还原 read 在原始片段上的位置，
    所以这里也保留。
    """

    sequence: bytes
    quality: bytes
    front_trimmed: int


def _scan_forward(
    quality: bytes,
    start: int,
    last_start: int,
    window: int,
    threshold: int,
    *,
    want_low: bool,
) -> tuple[bool, int]:
    """从左往右滚动扫描，窗口用**起点** ``s`` 表示，覆盖 ``[s, s+window-1]``。

    滚动求和：窗口每右移一位，只需"右端进一个、左端出一个"，两次加法。
    因此整体是 O(n) 而不是 O(n * window)。

    ``start`` 到 ``last_start``（含）是允许的窗口起点范围。
    **未命中时返回 ``last_start + 1``**——这是上游 ``for`` 循环在
    ``s + window < 上限`` 条件下的自然退出值，后续判断依赖它。

    ``want_low=True`` 找第一个"和低于阈值"的窗口（``cut_right`` 用）；
    否则找第一个"和达到阈值"的窗口（``cut_front`` 用）。
    """
    # 先装入窗口的前 window-1 个碱基；循环里再补第 window 个，构成完整窗口。
    total = 0
    for offset in range(window - 1):
        total += quality[start + offset]

    s = start
    while s <= last_start:
        total += quality[s + window - 1]
        if s > start:
            total -= quality[s - 1]
        hit = total < threshold if want_low else total >= threshold
        if hit:
            return True, s
        s += 1
    return False, last_start + 1


def _scan_backward(
    quality: bytes,
    first_end: int,
    last_end: int,
    window: int,
    threshold: int,
) -> tuple[bool, int]:
    """从右往左滚动扫描，窗口用**终点** ``t`` 表示，覆盖 ``[t-window+1, t]``。

    与 :func:`_scan_forward` 是同一套滚动求和，只是方向相反，
    因为 ``cut_tail`` 是"从 3' 端找第一个达标窗口"。

    ``first_end`` 是最右（起始）终点，``last_end`` 是允许的最小终点。
    **未命中时返回 ``last_end - 1``**，同样是上游 ``for`` 循环
    （``t - window >= 下限``）的自然退出值。

    前置条件（由调用方保证，函数内不做检查以免拖慢热路径）：
    ``first_end >= window - 1`` 且 ``last_end >= window - 1``。
    真实调用路径传入 ``first_end = length - tail - 1``、
    ``last_end = front + window``，两者都满足。
    若违反该条件，循环会访问 ``quality`` 的负下标——Python 会静默从尾部取值
    而不报错，因此**不要用越界参数直接调用本函数**。
    """
    total = 0
    for offset in range(window - 1):
        total += quality[first_end - offset]

    t = first_end
    while t >= last_end:
        total += quality[t - window + 1]
        if t < first_end:
            total -= quality[t + 1]
        if total >= threshold:
            return True, t
        t -= 1
    return False, last_end - 1


def trim_and_cut(
    sequence: bytes,
    quality: bytes,
    *,
    front: int = 0,
    tail: int = 0,
    config: QualityCutConfig | None = None,
) -> TrimResult | None:
    """对一条 read 做固定位置修剪与滑窗质量剪切。

    处理顺序与上游一致：**先固定修剪（front / tail），再 cut_front，
    再 cut_right 或 cut_tail**。后一步始终在前一步更新过的 ``front`` 上继续，
    所以 cut_front 切掉的部分会改变 cut_tail 的可用范围。

    参数：
        sequence: 碱基序列，ASCII 字节串（``bytes``）。应与 FASTQ 中的写法一致，
            大写或小写皆可，但 N 的判定只识别大写 ``N``（与上游一致）。
        quality: 质量字符串，ASCII 字节串，长度必须与 ``sequence`` 相同。
        front: 从头部固定切掉的碱基数。
        tail: 从尾部固定切掉的碱基数。
        config: 质量剪切参数；为 ``None`` 时表示不做质量剪切。

    返回：
        :class:`TrimResult`；**返回 ``None`` 表示这条 read 应当被丢弃**
        （上游返回 ``NULL``）。丢弃有两种原因：固定修剪后长度已成负数；
        或剪切后剩余长度不大于 0、以及头指针已经走到倒数第二个碱基之后。

    异常：
        ``TypeError``：``sequence`` 或 ``quality`` 不是字节串。
        ``ValueError``：两者长度不一致，或 ``front`` / ``tail`` 为负数。
    """
    if not isinstance(sequence, (bytes, bytearray)):
        raise TypeError("sequence 必须是 bytes（FASTQ 的碱基行）。")
    if not isinstance(quality, (bytes, bytearray)):
        raise TypeError("quality 必须是 bytes（FASTQ 的质量行）。")
    sequence = bytes(sequence)
    quality = bytes(quality)

    if len(sequence) != len(quality):
        raise ValueError(
            f"序列与质量长度不一致：{len(sequence)} 与 {len(quality)}。"
        )
    if front < 0 or tail < 0:
        raise ValueError(f"front 与 tail 不能为负数：front={front}，tail={tail}。")

    settings = config if config is not None else QualityCutConfig()
    length = len(sequence)
    has_quality_cut = (
        settings.enabled_front or settings.enabled_right or settings.enabled_tail
    )

    remaining = length - front - tail
    if remaining < 0:
        return None

    # ---- 没有质量剪切：只做固定位置修剪 ----
    if not has_quality_cut:
        if front == 0 and tail == 0:
            # 完全不需要处理，原样返回（上游为省一次拷贝直接返回原对象）。
            return TrimResult(sequence, quality, 0)
        return TrimResult(
            sequence[front : front + remaining],
            quality[front : front + remaining],
            front,
        )

    # ---- cut_front：从 5' 端找第一个达标窗口 ----
    if settings.enabled_front:
        window = settings.window_size_front
        # 可用窗口至少要放得下一个，否则上游直接丢弃这条 read。
        if length - front - tail - window <= 0:
            return None
        threshold = window * (_PHRED_OFFSET + settings.quality_front)
        found, position = _scan_forward(
            quality,
            front,
            length - tail - window - 1,
            window,
            threshold,
            want_low=False,
        )
        cut_at = position
        if cut_at > 0:
            # 把命中的那个"好窗口"也一起切掉：从窗口之后开始保留。
            cut_at = cut_at + window - 1
        while cut_at < length and sequence[cut_at] == _N_CODE:
            cut_at += 1
        front = cut_at
        remaining = length - front - tail

    # ---- cut_right：从 5' 端找第一个不达标窗口，保留其内达标的单碱基前缀 ----
    if settings.enabled_right:
        window = settings.window_size_right
        if length - front - tail - window <= 0:
            return None
        threshold = window * (_PHRED_OFFSET + settings.quality_right)
        found, position = _scan_forward(
            quality,
            front,
            length - tail - window - 1,
            window,
            threshold,
            want_low=True,
        )
        if found:
            cut_at = position
            while (
                cut_at < length - 1
                and quality[cut_at] >= _PHRED_OFFSET + settings.quality_right
            ):
                cut_at += 1
            remaining = cut_at - front
        # 未命中说明整条 read 没有低质量窗口，remaining 保持不变。

    # ---- cut_tail：从 3' 端找第一个达标窗口 ----
    # 上游明确写了 enabledRight 与 enabledTail 互斥：cut_right 更激进，同时开启时它说了算。
    if not settings.enabled_right and settings.enabled_tail:
        window = settings.window_size_tail
        if length - front - tail - window <= 0:
            return None
        threshold = window * (_PHRED_OFFSET + settings.quality_tail)
        found, position = _scan_backward(
            quality,
            length - tail - 1,
            front + window,
            window,
            threshold,
        )
        keep_until = position
        if keep_until < length - 1:
            # 回退到命中窗口的起始位置：这个窗口本身保留。
            keep_until = keep_until - window + 1
        while keep_until >= 0 and sequence[keep_until] == _N_CODE:
            keep_until -= 1
        remaining = keep_until - front + 1

    if remaining <= 0 or front >= length - 1:
        return None

    return TrimResult(
        sequence[front : front + remaining],
        quality[front : front + remaining],
        front,
    )
