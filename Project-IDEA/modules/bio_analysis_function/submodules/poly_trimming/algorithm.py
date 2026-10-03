"""reads 尾部 polyG / polyX 修剪。

本模块实现 fastp 的 ``trimPolyG`` 与 ``trimPolyX``（上游 ``src/polyx.cpp``）。
两者都是"从 3' 端往回扫，判断尾巴是不是一串同种碱基，是就切掉"，
但**判定逻辑完全不同**，不能互相替代——原因见下。

**设计目标：与 fastp 1.3.x 的 ``PolyX::trimPolyG`` / ``PolyX::trimPolyX``
逐位一致**，包括所有边界行为与循环退出值，便于对照验证。


## 一、polyG：为仪器的一个具体假象而生

Illumina 的部分机型（NovaSeq / NextSeq / 部分 HiSeq）使用**双色合成**
（two-color chemistry），只检测红色和绿色两个荧光通道。四种碱基的编码是：

    ┌──────┬────┬────┐
    │ 碱基 │ 红 │ 绿 │
    ├──────┼────┼────┤
    │  A   │ 有 │ 无 │
    │  C   │ 无 │ 有 │
    │  T   │ 有 │ 有 │
    │  G   │ 无 │ 无 │   <- 两个通道都没有信号
    └──────┴────┴────┘

**G 被编码为"两个通道都没有信号"**。当某个循环里这一簇的信号因为相位不同步、
聚合酶脱落等原因整体变暗时，识别系统就判成"无信号"——于是**被读成 G**。

结果是这类仪器在 3' 端会产生长串假 G，越靠尾部越多。这不是生物学现象，
是仪器化学的系统性假象，所以需要专门的处理。

**因为那个假象只会产生 G**，polyG 的判定非常简单：只关心"是不是 G"，
不统计其他碱基的种类。它从右往左扫，遇到非 G 就记一次 mismatch，
同时记录扫过的最左边的 G 的位置；扫描因错配过多而停止后，
若扫过的长度达到阈值，就把序列截断到那个 G 的位置。


## 二、polyX：通用的同种碱基尾巴

polyX 要在扫描过程中**同时跟踪 A/T/C/G 四种碱基的计数**，因为事先不知道
尾巴是哪种。它的停止条件也更严格：只有当**四种碱基都不像**（对每种碱基而言，
非它的数量都超过允许的错配数）时才会停。停下后再取计数最多的那种作为 poly 碱基。

两处容易被忽略的细节：

1. **N 同时计入四种碱基的计数**。因为 N 表示"无法判定"，它既不能用来投票
   选 poly 碱基，也不该中断扫描，所以让它对四个桶各投一票、相互抵消。
2. **切点会向左回退**。扫描可能停在尾巴中间的杂质处，但真正的 poly 尾巴可能
   延伸到更左边，所以要从扫描范围左端**向右**找到第一个 poly 碱基位置，
   从那里开始切——这样杂质左边属于尾巴的部分也一并去掉。


## 三、与质量剪切的关系

本模块只看碱基种类，**完全不看质量值**，也不使用 FASTQ 的第三行。
它与 :mod:`...quality_trimming.algorithm` 是彼此独立的步骤，
上游的处理顺序是质量剪切在前、poly 修剪在后。
"""

from __future__ import annotations

from dataclasses import dataclass

_ALLOW_ONE_MISMATCH_FOR_EACH = 8
"""每多少个碱基允许 1 个"非目标碱基"。"""

_MAX_MISMATCH = 5
"""polyG 的硬上限：错配超过它立刻停止扫描。"""

_G_CODE = ord("G")

_BASES = (ord("A"), ord("T"), ord("C"), ord("G"))
"""下标顺序与上游 ``ATCG_BASES`` 一致：A=0、T=1、C=2、G=3。"""

_BASE_INDEX = {ord("A"): 0, ord("T"): 1, ord("C"): 2, ord("G"): 3, ord("N"): 4}
"""其余字符一律视为 5（非碱基）。"""


@dataclass(frozen=True, slots=True)
class PolyTrimConfig:
    """polyG / polyX 的开关与阈值。

    上游把两个阈值分别记为 ``--poly_g_min_len`` 与 ``--poly_x_min_len``，
    默认都是 10——即至少要有 10 个碱基的扫描长度才认为存在 poly 尾巴。
    上游还会根据测序仪型号自动开启 polyG（双色系统），那是上层决策，
    不属于算法本身，因此本模块只提供显式开关。
    """

    enabled_poly_g: bool = False
    enabled_poly_x: bool = False
    min_length_poly_g: int = 10
    min_length_poly_x: int = 10

    def __post_init__(self) -> None:
        for name, value in (
            ("min_length_poly_g", self.min_length_poly_g),
            ("min_length_poly_x", self.min_length_poly_x),
        ):
            if value < 1:
                raise ValueError(f"{name} 必须不小于 1，当前为 {value}。")


@dataclass(frozen=True, slots=True)
class PolyTrimResult:
    """一次 poly 尾部修剪的结果。

    ``trimmed_bases`` 为 0 表示没有检测到 poly 尾巴、序列原样保留。
    ``poly_base`` 是被判定的 poly 碱基（单字节 ``b"A"`` 等）；没有修剪时为 ``None``。
    """

    sequence: bytes
    trimmed_bases: int
    poly_base: bytes | None

    @property
    def changed(self) -> bool:
        """序列是否被改动。"""
        return self.trimmed_bases > 0


def _validate(sequence: bytes, min_length: int) -> None:
    if not isinstance(sequence, (bytes, bytearray)):
        raise TypeError("sequence 必须是 bytes（FASTQ 的碱基行）。")
    if min_length < 1:
        raise ValueError(f"min_length 必须不小于 1，当前为 {min_length}。")


def trim_poly_g(sequence: bytes, *, min_length: int = 10) -> PolyTrimResult:
    """切掉 3' 端的 polyG 尾巴（双色合成的假象）。

    从右往左逐碱基扫描，规则与上游一致：

    - 遇到非 ``G`` 时 ``mismatch`` 加一，遇到 ``G`` 时记录其位置
      （因此记录的是扫过范围内**最左边**那个 G）；
    - 扫描停止条件是 ``mismatch > 5``，或已经扫过至少 ``min_length`` 个碱基
      （``i >= min_length - 1``）之后 ``mismatch`` 超过 ``(i+1)/8``；
    - 仅当**扫过的碱基数达到 ``min_length``** 时才真的裁剪，
      且裁剪到"最左边那个 G"的位置——**那个 G 本身也被切掉**。

    参数：
        sequence: 碱基序列，ASCII 字节串。只识别大写 ``G``。
        min_length: 最少扫描多少个碱基才认为存在 polyG 尾巴，默认 10。

    返回：
        :class:`PolyTrimResult`；没有检测到尾巴时 ``trimmed_bases`` 为 0。

    异常：
        ``TypeError``：``sequence`` 不是字节串。
        ``ValueError``：``min_length`` 小于 1。
    """
    _validate(sequence, min_length)
    sequence = bytes(sequence)
    length = len(sequence)
    if length == 0:
        return PolyTrimResult(sequence, 0, None)

    mismatch = 0
    scanned = 0
    first_g_position = length - 1

    while scanned < length:
        if sequence[length - scanned - 1] != _G_CODE:
            mismatch += 1
        else:
            first_g_position = length - scanned - 1

        allowed_mismatch = (scanned + 1) // _ALLOW_ONE_MISMATCH_FOR_EACH
        if mismatch > _MAX_MISMATCH or (
            mismatch > allowed_mismatch and scanned >= min_length - 1
        ):
            break
        scanned += 1

    # 上游判断的是循环变量本身（正常结束时等于 read 长度），语义即"扫过的碱基数"。
    if scanned >= min_length:
        trimmed = length - first_g_position
        return PolyTrimResult(sequence[:first_g_position], trimmed, b"G")
    return PolyTrimResult(sequence, 0, None)


def trim_poly_x(sequence: bytes, *, min_length: int = 10) -> PolyTrimResult:
    """切掉 3' 端任意碱基的同种尾巴（polyA / polyT / polyC / polyG）。

    与 :func:`trim_poly_g` 的区别在于判定方式：本函数在扫描过程中同时维护
    A/T/C/G 四个计数，只有当**四种碱基都不成立**时才停止——即对每一种碱基，
    "非它的数量"都超过了允许的错配数 ``min(5, 已扫长度/8)``。

    停下后取计数最多的一种作为 poly 碱基（并列时按下标顺序取，即 A > T > C > G），
    再从扫描范围左端**向右**找到第一个该碱基的位置，从那里裁剪。

    参数：
        sequence: 碱基序列，ASCII 字节串。
        min_length: 最少扫描多少个碱基才认为存在 poly 尾巴，默认 10。

    返回：
        :class:`PolyTrimResult`，``poly_base`` 为判定的碱基；没有修剪时为 ``None``。

    异常：
        ``TypeError``：``sequence`` 不是字节串。
        ``ValueError``：``min_length`` 小于 1。
    """
    _validate(sequence, min_length)
    sequence = bytes(sequence)
    length = len(sequence)
    if length == 0:
        return PolyTrimResult(sequence, 0, None)

    counts = [0, 0, 0, 0]
    scanned = 0

    while scanned < length:
        index = _BASE_INDEX.get(sequence[length - scanned - 1], 5)
        if index < 4:
            counts[index] += 1
        elif index == 4:
            # N 无法判定是哪种碱基，让它对四个桶各投一票、相互抵消。
            for base_index in range(4):
                counts[base_index] += 1

        length_scanned = scanned + 1
        allowed_mismatch = min(_MAX_MISMATCH, length_scanned // _ALLOW_ONE_MISMATCH_FOR_EACH)

        # 四种碱基全部不成立才会停：若某一种的"非它数量"仍在允许范围内，说明它仍可能是尾巴。
        all_bases_fail = all(
            length_scanned - counts[base_index] > allowed_mismatch
            for base_index in range(4)
        )
        if all_bases_fail and (
            scanned >= _ALLOW_ONE_MISMATCH_FOR_EACH
            or length_scanned >= min_length - 1
        ):
            break
        scanned += 1

    if scanned + 1 < min_length:
        return PolyTrimResult(sequence, 0, None)

    # 计数最多者胜出；严格大于保证并列时取下标更小的碱基。
    poly_index = 0
    max_count = -1
    for base_index in range(4):
        if counts[base_index] > max_count:
            max_count = counts[base_index]
            poly_index = base_index

    poly_base = _BASES[poly_index]
    cut_position = length - scanned - 1
    if cut_position < 0:
        # 扫描覆盖了整条 read（循环自然结束），说明整条都是一个 poly 尾巴。
        # 上游此处会读到 c_str() 之前的内存（pos=-1 时访问 data[-1]），属未定义行为；
        # 本实现明确取 0，即整条 read 都被切掉——这是与上游唯一的有意差异。
        cut_position = 0
    # 扫描可能停在尾巴中间的杂质上，向右找到该 poly 碱基真正开始的位置。
    while sequence[cut_position] != poly_base:
        cut_position += 1

    return PolyTrimResult(
        sequence=sequence[:cut_position],
        trimmed_bases=length - cut_position,
        poly_base=bytes((poly_base,)),
    )


def trim_poly_tails(sequence: bytes, *, config: PolyTrimConfig | None = None) -> PolyTrimResult:
    """按上游顺序串联执行 polyG 与 polyX 修剪。

    上游的处理顺序是 **polyG 在前、polyX 在后**，两者都作用于已经过质量剪切的序列。
    若 polyG 已经切掉了尾巴，polyX 会在剩下的序列上继续判断。

    参数：
        sequence: 碱基序列，ASCII 字节串。
        config: 开关与阈值；``None`` 表示两者都不做，原样返回。

    返回：
        :class:`PolyTrimResult`。``poly_base`` 记录的是**最后生效**的那一步所判定的碱基；
        两步都没有修剪时为 ``None``。

    异常：
        与 :func:`trim_poly_g` / :func:`trim_poly_x` 相同。
    """
    _validate(sequence, 1)
    settings = config if config is not None else PolyTrimConfig()

    current = bytes(sequence)
    total_trimmed = 0
    poly_base: bytes | None = None

    if settings.enabled_poly_g:
        result = trim_poly_g(current, min_length=settings.min_length_poly_g)
        if result.changed:
            current = result.sequence
            total_trimmed += result.trimmed_bases
            poly_base = result.poly_base

    if settings.enabled_poly_x:
        result = trim_poly_x(current, min_length=settings.min_length_poly_x)
        if result.changed:
            current = result.sequence
            total_trimmed += result.trimmed_bases
            poly_base = result.poly_base

    return PolyTrimResult(current, total_trimmed, poly_base)
