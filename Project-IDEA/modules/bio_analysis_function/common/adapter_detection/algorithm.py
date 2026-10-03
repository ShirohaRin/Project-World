"""adapter_detection：从一份（或一批）reads 里检测接头序列。

**设计目标：与 fastp 1.3.x 的 ``Evaluator::evalAdapterAndReadNum``
（``src/evaluator.cpp``）逐位一致**——包括它的两段式结构、所有阈值与扫描边界，
以及若干"看起来可以简化"的写法。改这里之前请先读同目录的 `adapter_detection.md`。

两段式结构：

1. **查已知接头表**（:func:`check_known_adapters`）。逐条 read 在任意位置匹配
   内置表里的接头，带"每 16 个碱基允许 1 个错配"的容错；命中率超过阈值就直接
   返回表中那条完整序列。这一段快、结果干净，是最常见的情形。
2. **从头检测**（:func:`detect_from_kmers`）。先统计所有 read 的 10-mer
   （从第 20 个碱基开始，跳过末尾 1 个碱基），挑出**显著富集**的 k-mer 作为种子，
   再用两棵前缀树向前、向后延伸出整条接头序列。

为什么第 2 段要这么做：接头是"接在 insert 之后的一段固定序列"。同一份数据里，
不同 read 的插入片段长短不同，因此接头出现在不同位置——但**接头的开头那几个碱基
一定会大量重复出现**，形成远超背景的富集。找到这个富集 k-mer，就等于找到了接头的
锚点；向前、向后各自延伸，是因为一条 read 可能只覆盖接头的后半段（锚点在 read 内部）
或前半段（锚点在 read 末端附近）。

本文件只处理单端 read，也不知道 read 的配对关系；双端的 overlap 校正属于后续算法。
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Final, Sequence

from ..known_adapters import KNOWN_ADAPTERS

# ---------------------------------------------------------------------------
# 上游常量
#
# 名字改成可读的写法，值一个不改。其中带"种子""树"注释的那些是上游写死在
# 函数体里的；与采样规模有关的三个放在配置里（见 AdapterDetectionConfig）。
# ---------------------------------------------------------------------------

_KEY_LENGTH: Final = 10            # 种子 k-mer 长度（上游 keylen）
_SEED_SCAN_START: Final = 20       # 从第 20 个碱基起扫（跳过 read 开头，那里不会有接头）
_MAX_SEARCH_LENGTH: Final = 500    # 建树时扫描位置的上限
_TOP_SEEDS: Final = 10             # 候选种子个数（上游 topnum）
_FOLD_THRESHOLD: Final = 20        # 富集倍数下限
_MIN_SEED_COUNT: Final = 10        # 种子出现次数下限
_MIN_SEED_DIFFERENCES: Final = 3   # 种子自身的复杂度下限（相邻不同的碱基数）
_MAX_ADAPTER_LENGTH: Final = 60    # 返回的接头序列截断长度

# 前缀树（NucleotideTree）的两个阈值
_NODE_RATIO: Final = 0.95          # 子节点占比达到它才算"占优"
_NODE_MIN_COUNT: Final = 50        # 节点计数低于它就不再延伸

# 已知接头表那一段的常量
_KNOWN_MATCH_MIN_LENGTH: Final = 8       # 最短匹配长度（上游 matchReq）
_KNOWN_ALLOW_ONE_MISMATCH_FOR_EACH: Final = 16
_KNOWN_MIN_RETURN_LENGTH: Final = 8      # 返回长度要 > 这个值（上游 `> 8`）
_KNOWN_MAX_READS: Final = 100_000        # 这一段最多看多少条 read
_KNOWN_MAX_BASES: Final = 100_000 * 1000
_KNOWN_MAX_HIT: Final = 1000             # 某个接头命中这么多就不再继续
_KNOWN_PRUNE_AFTER: Final = 20           # 最大命中数超过它之后开始剪枝
_KNOWN_PRUNE_RATIO: Final = 10           # 剪枝条件：命中数 < 最大值/10

# 碱基 → 2 bit。A=0、T=1、C=2、G=3，与上游 int2seq 的 bases[4] 同序。
_BASES: Final = ("A", "T", "C", "G")
_BASE_CODE: Final = [-1] * 256
for _index, _base in enumerate(b"ATCG"):
    _BASE_CODE[_base] = _index
del _index, _base


@dataclass(frozen=True, slots=True)
class AdapterDetectionConfig:
    """检测参数。默认值与 fastp 命令行一致。

    | 本字段 | fastp 对应物 | 说明 |
    | --- | --- | --- |
    | `max_reads` | `READ_LIMIT = 256*1024` | 最多采样多少条 read |
    | `max_bases` | `BASE_LIMIT = 151*READ_LIMIT` | 最多采样多少个碱基 |
    | `min_reads` | 硬编码的 10000 | 少于它就不做检测（上游认为样本太小不可信） |
    | `key_length` | 硬编码的 10 | 种子 k-mer 长度 |
    | `shift_tail` | `max(1, trim.tail1)` | 扫描时忽略末尾几个碱基（末位很吵） |
    | `max_adapter_length` | 硬编码的 60 | 返回序列的截断长度 |
    | `adapters` | 内置 234 条已知接头 | 已知接头表；传空元组表示跳过第一段 |

    ``key_length`` 只影响计数数组的大小（$4^k$ 个槽，每个 4 字节）：上游写死 10
    （4 MB），本实现允许在 4~12 之间调，主要是为了测试能跑得快一些——
    改它并不改变算法结构，但会改变"什么算显著富集"的尺度。
    """

    max_reads: int = 262_144
    max_bases: int = 151 * 262_144
    min_reads: int = 10_000
    key_length: int = _KEY_LENGTH
    shift_tail: int = 1
    max_adapter_length: int = _MAX_ADAPTER_LENGTH
    adapters: tuple[str, ...] = KNOWN_ADAPTERS

    def __post_init__(self) -> None:
        if self.max_reads < 1:
            raise ValueError(f"max_reads 必须不小于 1，当前为 {self.max_reads}。")
        if self.max_bases < 1:
            raise ValueError(f"max_bases 必须不小于 1，当前为 {self.max_bases}。")
        if self.min_reads < 1:
            raise ValueError(f"min_reads 必须不小于 1，当前为 {self.min_reads}。")
        if not 4 <= self.key_length <= 12:
            raise ValueError(f"key_length 必须在 4 到 12 之间，当前为 {self.key_length}。")
        if self.shift_tail < 0:
            raise ValueError(f"shift_tail 不能为负数，当前为 {self.shift_tail}。")
        if self.max_adapter_length < 1:
            raise ValueError(
                f"max_adapter_length 必须不小于 1，当前为 {self.max_adapter_length}。"
            )


@dataclass(frozen=True, slots=True)
class AdapterDetection:
    """一次检测的结果。

    ``adapter`` 为 ``None`` 表示没检测到；此时 ``reason`` 说明卡在哪一步
    （样本太小 / 已知表没命中且没有显著富集的 k-mer）。

    ``source`` 区分序列的来源：``"known"`` 是命中内置已知接头表（拿到的是完整序列），
    ``"kmer"`` 是从数据里拼出来的（可能只是接头的一段）。
    """

    adapter: str | None
    source: str | None
    sampled_reads: int
    sampled_bases: int
    reason: str
    seed_sequence: str | None = None
    seed_count: int = 0
    seed_fold: float = 0.0

    @property
    def detected(self) -> bool:
        """是否检测到接头。"""
        return self.adapter is not None

    def render(self) -> str:
        """渲染成便于阅读与日志记录的多行文本。"""
        lines = [
            f"采样 read 数：{self.sampled_reads}（{self.sampled_bases} 个碱基）",
        ]
        if self.adapter is None:
            lines.append(f"未检测到接头：{self.reason}")
            return "\n".join(lines)
        source = "命中已知接头表" if self.source == "known" else "从数据中拼出"
        lines.append(f"检测到接头（{source}）：{self.adapter}")
        if self.seed_sequence is not None:
            lines.append(
                f"  种子 k-mer：{self.seed_sequence}"
                f"（出现 {self.seed_count} 次，富集 {self.seed_fold:.1f} 倍）"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# k-mer 编解码
# ---------------------------------------------------------------------------


def _key_at(sequence: bytes, position: int, key_length: int, last_key: int) -> int:
    """取 ``[position, position + key_length)`` 的 2-bit 编码；含非 ACGT 返回 -1。

    与上游 ``Evaluator::seq2int`` 一样支持**滚动**：上一个窗口的键有效时，
    只把新进窗口的那个碱基接上去（整体复杂度从 $O(nk)$ 降到 $O(n)$）；
    上一个键无效（含 N）时退回完整重算——上游正是这么写的，不是可以顺手简化的地方。
    """
    if last_key >= 0:
        mask = (1 << (key_length * 2)) - 1
        code = _BASE_CODE[sequence[position + key_length - 1]]
        if code < 0:
            return -1
        return ((last_key << 2) & mask) + code

    key = 0
    for index in range(position, position + key_length):
        code = _BASE_CODE[sequence[index]]
        if code < 0:
            return -1
        key = (key << 2) + code
    return key


def _sequence_of_key(value: int, key_length: int) -> str:
    """上游 ``Evaluator::int2seq``：把键还原成碱基序列。"""
    chars = ["N"] * key_length
    done = 0
    while done < key_length:
        chars[key_length - done - 1] = _BASES[value & 0x03]
        value >>= 2
        done += 1
    return "".join(chars)


# ---------------------------------------------------------------------------
# 前缀树（上游 NucleotideTree）
# ---------------------------------------------------------------------------


class _NucleotideNode:
    """树上的一个节点。

    上游用 ``children[8]``（按 ``碱基 & 0x07`` 索引）存子节点，这里等价地用字典：
    在 A/C/G/T/N 这几种碱基上，``& 0x07`` 不会撞车，字典与定长数组的行为一致。
    """

    __slots__ = ("count", "children")

    def __init__(self) -> None:
        self.count = 0
        self.children: dict[int, _NucleotideNode] = {}


def _add_sequence(root: _NucleotideNode, sequence: bytes) -> None:
    """把一条序列挂到树上；遇到 ``N`` 就停（上游行为）。"""
    node = root
    for base in sequence:
        if base == 0x4E:  # ord("N")
            break
        child = node.children.get(base)
        if child is None:
            child = _NucleotideNode()
            node.children[base] = child
        child.count += 1
        node = child


def _dominant_path(root: _NucleotideNode) -> tuple[str, bool]:
    """从根出发一路走"占优"的子节点，返回 ``(路径, 是否走到叶)``。

    每一步要求某个子节点占比 $\ge 0.95$，且当前节点总数 $\ge 50$；
    没有占优子节点时停下并标记 ``reached_leaf=False``——这个标记决定
    "拼出来的接头可信不可信"（见 :func:`detect_from_kmers`）。

    占比阈值 ≥0.95 意味着至多只有一个子节点占优，因此遍历子节点的顺序不影响结果。
    """
    pieces: list[str] = []
    reached_leaf = True
    node = root
    while True:
        total = 0
        for child in node.children.values():
            total += child.count
        if total < _NODE_MIN_COUNT:
            break
        chosen: int | None = None
        for base, child in node.children.items():
            if child.count / total >= _NODE_RATIO:
                chosen = base
                break
        if chosen is None:
            reached_leaf = False
            break
        pieces.append(chr(chosen))
        node = node.children[chosen]
    return "".join(pieces), reached_leaf


# ---------------------------------------------------------------------------
# 已知接头表
# ---------------------------------------------------------------------------


def match_known_adapter(sequence: str, adapters: Sequence[str] | None = None) -> str:
    """从 ``sequence`` 里认出已知接头（只看开头，要求完全一致）。

    上游 ``Evaluator::matchKnownAdapter``：按表的顺序逐条比，若某条已知接头是
    ``sequence`` 的前缀（**一个错配都不允许**）就返回那一条——因此返回的是表里的
    完整序列，而不是数据里拼出来的片段。没认出来返回空串。
    """
    table = KNOWN_ADAPTERS if adapters is None else adapters
    for adapter in table:
        if len(sequence) < len(adapter):
            continue
        if sequence[: len(adapter)] == adapter:
            return adapter
    return ""


def _match_known_adapter_bytes(sequence: bytes, adapters: Sequence[bytes]) -> bytes:
    """:func:`match_known_adapter` 的字节版（热路径用，避免来回转换）。"""
    for adapter in adapters:
        if len(sequence) < len(adapter):
            continue
        if sequence[: len(adapter)] == adapter:
            return adapter
    return b""


def check_known_adapters(
    reads: Sequence[bytes],
    config: AdapterDetectionConfig,
) -> tuple[str, int, int]:
    """第一段：在内置已知接头表里找命中率最高的一条。

    返回 ``(接头序列, 检查的 read 数, 命中的 read 数)``；没命中返回空串。

    匹配规则（上游的写法，逐字保留）：对每条 read 的每个起点 ``pos``，
    拿已知接头的前 ``min(剩余长度, 接头长度)`` 个碱基去比，允许的错配数是
    ``比对的碱基数 // 16``；某条接头在任意一个起点上过关就算这条 read命中它，
    然后**换下一条接头**（同一条 read 可以在多条接头上各记一次）。

    两处刻意的剪枝：某个接头命中数已经落后于当前最大值 10 倍时跳过它；
    某个接头命中超过 1000 次就整段收工。这两条不是可有可无的优化——
    它们决定了"检查了多少条 read"，从而影响 ``checked_reads`` 这个分母。
    """
    adapters: tuple[str, ...] = config.adapters
    if not adapters:
        return "", 0, 0

    # 上游用 std::map<string,string> 存这张表，遍历顺序**天然是字典序**；而"并列时
    # 谁被返回"依赖这个顺序。这里显式排序，好让结果与调用方传入的顺序无关，
    # 也与上游一致（内置表本身已排好序，排序是幂等的）。
    adapters = tuple(sorted(adapters))
    adapter_bytes = [adapter.encode("ascii") for adapter in adapters]
    possible_counts = [0] * len(adapters)
    mismatches = [0] * len(adapters)

    checked_reads = 0
    checked_bases = 0
    current_max = 0

    for sequence in reads:
        checked_reads += 1
        checked_bases += len(sequence)
        if checked_reads > _KNOWN_MAX_READS or checked_bases > _KNOWN_MAX_BASES:
            break
        if current_max > _KNOWN_MAX_HIT:
            break

        read_length = len(sequence)
        for index, adapter in enumerate(adapter_bytes):
            adapter_length = len(adapter)
            if adapter_length >= read_length:
                continue
            if (
                current_max > _KNOWN_PRUNE_AFTER
                and possible_counts[index] < current_max // _KNOWN_PRUNE_RATIO
            ):
                continue

            for position in range(read_length - _KNOWN_MATCH_MIN_LENGTH):
                compare_length = min(read_length - position, adapter_length)
                allowed = compare_length // _KNOWN_ALLOW_ONE_MISMATCH_FOR_EACH
                mismatch = 0
                matched = True
                for offset in range(compare_length):
                    if adapter[offset] != sequence[position + offset]:
                        mismatch += 1
                        if mismatch > allowed:
                            matched = False
                            break
                if matched:
                    possible_counts[index] += 1
                    if current_max < possible_counts[index]:
                        current_max = possible_counts[index]
                    mismatches[index] += mismatch
                    break

    best_index = -1
    max_count = 0
    # 表已按字典序排列；用严格大于比较，并列时取字典序更小的那条——与上游
    # std::map 的遍历顺序一致。
    for index, count in enumerate(possible_counts):
        if count > max_count:
            max_count = count
            best_index = index

    if best_index < 0:
        return "", checked_reads, 0

    # 上游用整数除法（两个操作数都是整型）。
    if max_count > checked_reads // 50 or (
        max_count > checked_reads // 200
        and mismatches[best_index] < checked_reads
    ):
        return adapters[best_index], checked_reads, max_count
    return "", checked_reads, 0


# ---------------------------------------------------------------------------
# 从头检测：k-mer 富集 + 前缀树延伸
# ---------------------------------------------------------------------------


def _adapter_with_seed(
    seed: int,
    reads: Sequence[bytes],
    config: AdapterDetectionConfig,
) -> tuple[str, bool]:
    """围绕种子 k-mer 向前、向后延伸，拼出接头序列。

    返回 ``(接头, 是否到达叶节点)``；接头可能为空串（两棵树的路径都为空时）。

    上游在这里有一个容易看漏的细节：``reachedLeaf`` 是一个**复用的标记**，
    前向树写一次、后向树再写一次，而 ``getDominantPath`` 只会把它置 ``false``、
    从不置回 ``true``；因此它最终的含义是"**两棵树都没有遇到非占优分支**"。
    本实现照原样保留这个语义。
    """
    key_length = config.key_length
    shift_tail = config.shift_tail
    seed_sequence = _sequence_of_key(seed, key_length)

    forward_root = _NucleotideNode()
    backward_root = _NucleotideNode()

    for sequence in reads:
        last_position = min(
            len(sequence) - key_length - shift_tail, _MAX_SEARCH_LENGTH - 1
        )
        last_key = -1
        for position in range(_SEED_SCAN_START, last_position + 1):
            last_key = _key_at(sequence, position, key_length, last_key)
            if last_key != seed:
                continue
            # 前向：种子之后直到（去掉末尾 shift_tail 个碱基）的那一段
            _add_sequence(
                forward_root, sequence[position + key_length : len(sequence) - shift_tail]
            )
            # 后向：种子之前那一段**反过来**挂树（树在缺口左边生长）
            _add_sequence(backward_root, sequence[position - 1 :: -1])

    forward_path, forward_reached = _dominant_path(forward_root)
    backward_path, backward_reached = _dominant_path(backward_root)
    reached_leaf = forward_reached and backward_reached

    adapter = backward_path[::-1] + seed_sequence + forward_path
    if len(adapter) > config.max_adapter_length:
        adapter = adapter[: config.max_adapter_length]

    matched = match_known_adapter(adapter, config.adapters)
    if matched:
        return matched, reached_leaf
    if reached_leaf:
        return adapter, True
    return "", False


_ACCEPTABLE_KEYS_CACHE: dict[int, tuple[int, ...]] = {}


def _acceptable_keys(key_length: int) -> tuple[int, ...]:
    """通过"噪声过滤"的 k-mer 键（升序），按 key_length 缓存。

    上游在计数后遍历全部 $4^k$ 个键，逐个判断三类噪声：低复杂度（任一碱基出现
    $\ge k-4$ 次）、过富 GC（G+C $\ge k-2$）、以 ``GGGG`` 开头。这三条**只与
    键本身有关、与数据无关**，所以在 Python 里提前算好并缓存——遍历 100 万个
    键逐个判断是实打实的开销。过滤规则逐条与上游相同，遍历顺序（升序）也相同，
    因此 `total` 与 top-10 的结果不受影响。

    上游把"以 GGGG 开头"写成 ``k >> 12 == 0xff``（写死 k=10）；这里写成
    ``k >> ((k-4)*2) == 0xff``，默认值下与上游逐位等价，k 变化时也仍有意义。
    """
    cached = _ACCEPTABLE_KEYS_CACHE.get(key_length)
    if cached is not None:
        return cached

    size = 1 << (key_length * 2)
    leading_shift = (key_length - 4) * 2
    acceptable: list[int] = []
    for key in range(size):
        atcg = [0, 0, 0, 0]
        for offset in range(key_length):
            atcg[(key >> (offset * 2)) & 0x03] += 1
        if any(count >= key_length - 4 for count in atcg):
            continue
        if atcg[2] + atcg[3] >= key_length - 2:
            continue
        if key >> leading_shift == 0xFF:
            continue
        acceptable.append(key)

    result = tuple(acceptable)
    _ACCEPTABLE_KEYS_CACHE[key_length] = result
    return result


def detect_from_kmers(
    reads: Sequence[bytes],
    config: AdapterDetectionConfig,
) -> AdapterDetection:
    """第二段：统计 k-mer 富集，挑种子，再延伸成接头。

    判定链（每一步的阈值都来自上游，顺序不变）：

    1. 统计所有 read 在位置 $\ge 20$ 处的 10-mer 出现次数（跳过末尾 ``shift_tail`` 个碱基）；
    2. 把 ``AAAAAAAAAA`` 的计数清零（polyA 尾巴会淹没一切）；
    3. 取计数最高的 10 个 k-mer，但先排除三类显然是噪声的：低复杂度
       （任一碱基出现 $\ge 6$ 次）、过富 GC（G+C $\ge 8$）、以 ``GGGG`` 开头；
    4. 从高到低试这 10 个种子：出现次数少于 10 次、或富集倍数不到 20 倍就**停止**
       （不是跳过——上游是 break，说明后面的只会更差）；种子自身相邻不同的碱基数
       少于 3 个也跳过；
    5. 用该种子延伸；延伸出来的接头若是空或没到叶节点，继续试下一个种子。
    """
    key_length = config.key_length
    size = 1 << (key_length * 2)
    counts = array("I", bytes(4 * size))
    shift_tail = config.shift_tail

    for sequence in reads:
        last_position = len(sequence) - key_length - shift_tail
        last_key = -1
        for position in range(_SEED_SCAN_START, last_position + 1):
            last_key = _key_at(sequence, position, key_length, last_key)
            if last_key >= 0:
                counts[last_key] += 1

    counts[0] = 0  # 甩掉 AAAAAAAAAA

    top_keys = [0] * _TOP_SEEDS
    total = 0
    for key in _acceptable_keys(key_length):
        value = counts[key]
        total += value
        # 插入排序，把 key 放进"计数最高的 10 个"里；与上游的写法逐行对应
        # （包括并列时的取舍：后遇到的键占据更靠前的位置）。
        for slot in range(_TOP_SEEDS - 1, -1, -1):
            if value < counts[top_keys[slot]]:
                if slot < _TOP_SEEDS - 1:
                    for move in range(_TOP_SEEDS - 1, slot + 1, -1):
                        top_keys[move] = top_keys[move - 1]
                    top_keys[slot + 1] = key
                break
            if slot == 0:
                for move in range(_TOP_SEEDS - 1, 0, -1):
                    top_keys[move] = top_keys[move - 1]
                top_keys[0] = key

    sampled_bases = sum(map(len, reads))

    for slot in range(_TOP_SEEDS):
        key = top_keys[slot]
        if key == 0:
            continue
        count = counts[key]
        # 上游是 break：候选按计数降序试，一旦不达标，后面的只会更差。
        if count < _MIN_SEED_COUNT or count * size < total * _FOLD_THRESHOLD:
            break
        seed_sequence = _sequence_of_key(key, key_length)
        differences = 0
        for index in range(len(seed_sequence) - 1):
            if seed_sequence[index] != seed_sequence[index + 1]:
                differences += 1
        if differences < _MIN_SEED_DIFFERENCES:
            continue

        adapter, _reached_leaf = _adapter_with_seed(key, reads, config)
        if adapter:
            fold = count * size / total if total else 0.0
            return AdapterDetection(
                adapter=adapter,
                source="kmer",
                sampled_reads=len(reads),
                sampled_bases=sampled_bases,
                reason="",
                seed_sequence=seed_sequence,
                seed_count=count,
                seed_fold=fold,
            )

    return AdapterDetection(
        adapter=None,
        source=None,
        sampled_reads=len(reads),
        sampled_bases=sampled_bases,
        reason="已知接头表未命中，且没有找到显著富集的 k-mer 种子",
    )


def detect_adapter(
    reads: Sequence[bytes],
    config: AdapterDetectionConfig | None = None,
) -> AdapterDetection:
    """检测接头序列：先查已知接头表，查不到再从数据里拼。

    参数：
        reads: **已经读入内存**的 read 序列列表（只要序列，不要质量）。
            上游同样把采样到的 read 留在内存里，因为后面要多次遍历它们。
        config: 检测参数；``None`` 表示默认（与 fastp 一致）。

    返回：
        :class:`AdapterDetection`；``adapter`` 为 ``None`` 表示没检测到，
        具体卡在哪一步写在 ``reason`` 里。
    """
    config = config or AdapterDetectionConfig()
    sampled_bases = sum(map(len, reads))

    # 上游：样本少于 10000 条就整体放弃检测。
    if len(reads) < config.min_reads:
        return AdapterDetection(
            adapter=None,
            source=None,
            sampled_reads=len(reads),
            sampled_bases=sampled_bases,
            reason=f"样本不足：只有 {len(reads)} 条 read，需要 {config.min_reads} 条",
        )

    if config.adapters:
        known, checked_reads, hits = check_known_adapters(reads, config)
        if len(known) > _KNOWN_MIN_RETURN_LENGTH:
            return AdapterDetection(
                adapter=known,
                source="known",
                sampled_reads=len(reads),
                sampled_bases=sampled_bases,
                reason="",
                seed_count=hits,
                seed_fold=hits / checked_reads if checked_reads else 0.0,
            )

    return detect_from_kmers(reads, config)
