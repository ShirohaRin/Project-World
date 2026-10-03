"""adapter_trimming：把接头从 read 上剪掉。

**设计目标：与 fastp 1.3.x 的 ``AdapterTrimmer::trimBySequence`` /
``trimByMultiSequences``（``src/adaptertrimmer.cpp``）逐位一致**，包括那段"从负位置
开始扫"的处理、允许 1 个插入/缺失的容错、以及所有阈值。改这里之前请先读同目录的
`adapter_trimming.md`。

算法核心只有一件事：**在 read 上找接头，找到就把从该位置起到末尾的部分切掉**。
难的地方在于"怎么算找到"：

1. **从负位置开始扫**。负起点模拟的是 Illumina 接头二聚体的常见形态——read 的开头
   少了一个 A（A-tailing 的后果），因此接头的第 1~4 个碱基被跳过也要算命中。
2. **容错按比例给**：比对长度每 8 个碱基允许 1 个错配（``cmplen / 8``）。
3. **允许一个插入或一个缺失**：先按等长比对（Hamming 距离），失败再允许 read 侧多一个
   碱基（插入），最后允许位置侧多一个碱基（缺失）。这也是"接头最后一个碱基没读全"
   这类情况的兜底。
4. **命中即切，且只切一次**（多序列模式下会依次对同一条 read 反复裁）。

本文件只做单条 read 的裁剪，不读写文件；文件级接口见 ``runner.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence

# ---------------------------------------------------------------------------
# 上游常量
# ---------------------------------------------------------------------------

#: 每多少个碱基允许 1 个错配。
_ALLOW_ONE_MISMATCH_FOR_EACH: Final = 8
#: 允许插入/缺失时，比对长度要"多扣一个错配"，因此可能变成负数。
_MIN_COMPARE_LENGTH: Final = 2


@dataclass(frozen=True, slots=True)
class AdapterTrimConfig:
    """裁剪参数。

    | 字段 | fastp 对应物 | 说明 |
    | --- | --- | --- |
    | `match_required` | `matchReq`（默认 4） | 最短匹配长度；接头比它还短就直接不裁 |
    | `allow_one_gap` | 上游写死的行为 | 是否允许 1 个插入/缺失（上游总是允许） |

    说明：``match_required`` 在多序列模式下会被自动抬高（候选多于 16 条取 5、
    多于 256 条取 6）——上游用同一套规则，目的是候选越多、越要防止误伤。
    """

    match_required: int = 4
    allow_one_gap: bool = True

    def __post_init__(self) -> None:
        if self.match_required < 1:
            raise ValueError(f"match_required 必须不小于 1，当前为 {self.match_required}。")


@dataclass(frozen=True, slots=True)
class AdapterTrimResult:
    """一条 read 的裁剪结果。

    ``removed`` 是被切掉的序列（上游把它记进统计报告，用来汇总"检出了哪些接头"）。
    ``position`` 是切点：>= 0 表示从 read 的第几个碱基起切掉；< 0 表示**整条 read 都是
    接头**（接头二聚体），此时 read 被清空。``trimmed`` 为假表示这次没裁。
    """

    sequence: bytes
    quality: bytes
    trimmed: bool
    removed: bytes = b""
    position: int = 0

    @property
    def length(self) -> int:
        return len(self.sequence)


def _count_mismatches_bounded(left: bytes, right: bytes, length: int, limit: int) -> int:
    """比对前 ``length`` 个碱基的错配数；超过 ``limit`` 就提前收工。

    对应上游 ``fastp_simd::countMismatchesBounded``：返回值的语义是"错配数，
    但一旦超过 limit 就返回一个 > limit 的数"，因此调用方用 ``<= limit`` 判断命中。
    """
    mismatches = 0
    for index in range(length):
        if left[index] != right[index]:
            mismatches += 1
            if mismatches > limit:
                return mismatches
    return mismatches


def match_with_one_insertion(
    inserted: bytes,
    normal: bytes,
    compare_length: int,
    diff_limit: int,
) -> bool:
    """判断把 ``inserted`` 多出的那个碱基"吃掉"之后能否在容错范围内对上。

    上游 ``Matcher::matchWithOneInsertion`` 的逐位移植：它同时从两端累计错配
    （``left[i]`` = 前缀错配，``right[i]`` = 后缀错配），然后找一个插入位置
    ``i`` 使 ``left[i-1] + right[i] <= diff_limit``。两端累计都带提前退出，
    退出的那一步会把它后面**已经确定不行的** ``right`` 槽位填成 ``diff_limit + 1``。

    与上游的两点差别，都是为了**行为确定**：

    1. **不读未初始化的内存**。C++ 里 ``left`` 数组在提前 ``break`` 之后，后段是
       未初始化内存；上游最后那个判定循环会在某些输入下读到它们（症状是随机的
       假命中/漏命中）。本实现把前缀累计**算到底**（不提前退出），也就是把上游"想
       要的"那个单调递增的累计值算完整——所以这里的判定是确定的、可复现的。
       这一条只会在"上游读到垃圾值"的那些情形下与上游不同，而那些情形下上游本身
       没有确定行为。
    2. ``compare_length`` 小于 2 时直接判否（上游会越界访问）。

    ``compare_length`` 必须不小于 2（上游的调用点都满足）。
    """
    if compare_length < _MIN_COMPARE_LENGTH:
        return False

    left = [0] * compare_length
    right = [0] * compare_length

    left[0] = 0 if inserted[0] == normal[0] else 1
    right[compare_length - 1] = 0 if inserted[compare_length] == normal[compare_length - 1] else 1

    # 前缀累计：上游在这里会提前 break（省一点计算），代价是后段变成未初始化内存。
    # 这里算到底，值确定、且与"提前 break 之后本该继续累加"的结果一致。
    for index in range(1, compare_length):
        left[index] = left[index - 1] + (0 if inserted[index] == normal[index] else 1)

    for index in range(compare_length - 2, -1, -1):
        right[index] = right[index + 1] + (
            0 if inserted[index + 1] == normal[index] else 1
        )
        if right[index] + left[0] > diff_limit:
            for earlier in range(index):
                right[earlier] = diff_limit + 1
            break

    for index in range(1, compare_length):
        if left[index - 1] + right[compare_length - 1] > diff_limit:
            return False
        if left[index - 1] + right[index] <= diff_limit:
            return True
    return False


def _find_adapter_position(
    sequence: bytes,
    adapter: bytes,
    config: AdapterTrimConfig,
) -> int | None:
    """在 read 上找接头的起点；找不到返回 ``None``。

    三段式（与上游同序）：

    1. **等长比对**：从 ``start`` 扫到 ``len(read) - match_required``，
       逐位置算错配数，允许 ``cmplen/8`` 个错配。``start`` 是负数时表示"接头的开头
       被跳过"（接头二聚体），此时比对从 read 的第 0 个碱基开始、与接头的第 ``-start``
       个碱基对齐。
    2. **允许一个插入**：read 侧多一个碱基。
    3. **允许一个缺失**：接头侧（位置侧）多一个碱基。
    """
    match_required = config.match_required
    read_length = len(sequence)
    adapter_length = len(adapter)
    if adapter_length < match_required:
        return None

    # 负起点：接头的开头被跳过也要认（上游注释：接头二聚体常少一个 A）
    start = 0
    if adapter_length >= 16:
        start = -4
    elif adapter_length >= 12:
        start = -3
    elif adapter_length >= 8:
        start = -2

    for position in range(start, read_length - match_required):
        compare_length = min(read_length - position, adapter_length)
        allowed_mismatch = compare_length // _ALLOW_ONE_MISMATCH_FOR_EACH
        start_offset = max(0, -position)
        mismatches = _count_mismatches_bounded(
            adapter[start_offset:],
            sequence[start_offset + position :],
            compare_length - start_offset,
            allowed_mismatch,
        )
        if mismatches <= allowed_mismatch:
            return position

    if not config.allow_one_gap:
        return None

    # 允许一个插入：read 比对接头多一个碱基。
    # 注意上游的写法：这两段比对**锚定在 read 的开头**，随 pos 变动的只有比对长度
    # （``cmplen = min(rlen - pos - 1, alen)``），指针并不做 pos 偏移。看起来像笔误，
    # 但结果（要不要裁、切在哪里）依赖它，因此照原样保留。
    for position in range(0, read_length - match_required - 1):
        compare_length = min(read_length - position - 1, adapter_length)
        allowed_mismatch = compare_length // _ALLOW_ONE_MISMATCH_FOR_EACH - 1
        if match_with_one_insertion(
            sequence, adapter, compare_length, allowed_mismatch
        ):
            return position

    # 允许一个缺失：接头比对上 read 多一个碱基（两边调过来就是同一个函数）
    for position in range(0, read_length - match_required):
        compare_length = min(read_length - position, adapter_length - 1)
        allowed_mismatch = compare_length // _ALLOW_ONE_MISMATCH_FOR_EACH - 1
        if match_with_one_insertion(
            adapter, sequence, compare_length, allowed_mismatch
        ):
            return position

    return None


def trim_adapter(
    sequence: bytes,
    quality: bytes,
    adapter: str | bytes,
    config: AdapterTrimConfig | None = None,
) -> AdapterTrimResult:
    """按给定接头序列裁剪一条 read。

    参数：
        sequence / quality: 等长的碱基与质量串。
        adapter: 接头序列（``str`` 或 ``bytes``，大写）。
        config: 裁剪参数；``None`` 表示默认（与 fastp 一致）。

    返回：
        :class:`AdapterTrimResult`。``trimmed`` 为假时序列原样返回。

    说明：
        **切点之前保留、切点之后全部丢弃**——这与上游一致（它不做"只切接头那一段"的
        精细处理，因为接头之后本来就不该有基因组序列）。
        整条 read 都是接头时（``position < 0``）read 被清空，这在我们的统计里
        仍然算"保留"：上游把它留给后面的过滤步骤按长度丢弃。
    """
    if len(sequence) != len(quality):
        raise ValueError(
            f"序列长度（{len(sequence)}）与质量长度（{len(quality)}）不一致。"
        )

    config = config or AdapterTrimConfig()
    adapter_bytes = adapter.encode("ascii") if isinstance(adapter, str) else adapter
    if not adapter_bytes:
        return AdapterTrimResult(sequence=sequence, quality=quality, trimmed=False)

    position = _find_adapter_position(sequence, adapter_bytes, config)
    if position is None:
        return AdapterTrimResult(sequence=sequence, quality=quality, trimmed=False)

    if position < 0:
        # 整条 read 都是接头（接头二聚体）：清空，切掉的部分是接头的前 alen+pos 个碱基
        removed = adapter_bytes[: len(adapter_bytes) + position]
        return AdapterTrimResult(sequence=b"", quality=b"", trimmed=True, removed=removed, position=position)

    removed = sequence[position:]
    return AdapterTrimResult(
        sequence=sequence[:position],
        quality=quality[:position],
        trimmed=True,
        removed=removed,
        position=position,
    )


def match_required_for(count: int) -> int:
    """多序列模式下的最短匹配长度（上游：候选越多，要求越长）。"""
    if count > 256:
        return 6
    if count > 16:
        return 5
    return 4


def trim_adapters(
    sequence: bytes,
    quality: bytes,
    adapters: Sequence[str | bytes],
    config: AdapterTrimConfig | None = None,
) -> AdapterTrimResult:
    """依次用候选表里的每条接头裁剪同一条 read。

    与上游 ``trimByMultiSequences`` 一致的两点：**逐条依次裁**（上一条裁完的结果
    作为下一条的输入，因此一条 read 可能被裁多次）、**任一条命中就算裁过**
    （``trimmed`` 是各次结果的"或"）。
    """
    config = config or AdapterTrimConfig()
    if not adapters:
        return AdapterTrimResult(sequence=sequence, quality=quality, trimmed=False)

    match_required = match_required_for(len(adapters))
    effective = AdapterTrimConfig(
        match_required=match_required, allow_one_gap=config.allow_one_gap
    )

    current_sequence = sequence
    current_quality = quality
    trimmed = False
    removed = b""
    position = 0
    for candidate in adapters:
        result = trim_adapter(current_sequence, current_quality, candidate, effective)
        if result.trimmed:
            trimmed = True
            removed = result.removed if not removed else removed
            position = result.position
            current_sequence = result.sequence
            current_quality = result.quality
    return AdapterTrimResult(
        sequence=current_sequence,
        quality=current_quality,
        trimmed=trimmed,
        removed=removed,
        position=position,
    )
