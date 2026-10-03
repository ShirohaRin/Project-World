"""deduplication：重复序列检测与去重（fastp 的 ``Duplicate`` / ``--dedup``）。

**设计目标：与 fastp 1.3.x 的 ``Duplicate``（``src/duplicate.cpp``）逐位一致**，
包括布隆过滤器的位图布局、素数表的生成方式、以及"序列 → 位置向量"这步
特意用乘法与加法搅出来的哈希。改这里之前请先读同目录 `deduplication.md`
的设计记录：上游若干处"看起来可以简化"的写法是刻意的。

本文件只做序列层面的判重，不读写文件；文件级接口见 ``runner.py``。

--------------------------------------------------------------------------
怎么判"重复"
--------------------------------------------------------------------------

用**布隆过滤器**：不为每条序列存原文，而是把序列压成若干个 64 位位置向量，
把这些位置对应的比特置 1。一条序列**所有**位置上的比特在本次运行中**都已经被
置起**，就判为重复。因此它有一个代价：**假阳性**——两条不同的序列可能撞到
同一组比特上，被判成重复。这是上游的取舍（省内存换速度），本实现照搬，
并把假阳性率随内存档位的变化写进文档。

判定是**顺序相关**的：谁先出现谁"不重复"。所以同一份数据换个顺序跑，
重复条数会不同——这是算法本身的定义，不是实现的不确定性（见 `deduplication.md`
的"顺序依赖性"一节）。

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------

本算法**不改写任何碱基**：它只决定哪几条（或哪几对）与前面出现过的重复。
与 :mod:`read_filtering` 的区别是判据完全不同——过滤看的是"这条 read 本身
够不够好"，去重看的是"这条 read 与前面的有没有重复"；两者互不影响，
上游把它们放在处理链的不同位置（过滤在写出前、去重在写出时）。
"""

from __future__ import annotations

from functools import lru_cache
from math import isqrt
from typing import Final

# ---------------------------------------------------------------------------
# 内存档位
#
# 上游的 `--dup_calc_accuracy` 是 1~6 档，每档给多少内存是写死的：
# 档位越高，比特越多、假阳性率越低，内存翻倍增长。数值照抄，
# 因为它是**产品参数**（用户看到的就是"多花多少内存换多少准确度"），
# 不是为了好玩。
# ---------------------------------------------------------------------------

#: 档位 → (缓冲区个数, 每个缓冲区的字节数)。总内存 = 两者相乘。
#:
#: | 档位 | 缓冲区 | 总内存 |
#: | --- | --- | --- |
#: | 1 | 2 × 512 MiB | 1 GiB |
#: | 2 | 2 × 1 GiB | 2 GiB |
#: | 3 | 4 × 1 GiB | 4 GiB |
#: | 4 | 4 × 2 GiB | 8 GiB |
#: | 5 | 4 × 4 GiB | 16 GiB |
#: | 6 | 8 × 4 GiB | 32 GiB |
_ACCURACY_PLAN: Final[dict[int, tuple[int, int]]] = {
    1: (2, 1 << 29),
    2: (2, 1 << 30),
    3: (4, 1 << 30),
    4: (4, 1 << 31),
    5: (4, 1 << 32),
    6: (8, 1 << 32),
}

#: 只评估重复率（不去重）时的默认档位。上游同样如此。
DEFAULT_ACCURACY_ANALYZE: Final = 1

#: 真正去重时的默认档位。比只评估高一档，因为判错了要丢数据。上游同样如此。
DEFAULT_ACCURACY_DEDUP: Final = 3

#: 素数表的**每缓冲区**长度。上游写法是 ``1 << 9``，即 512。
_PRIME_ARRAY_LEN: Final = 1 << 9

#: uint64 的模，用来模拟 C 侧无符号整数的回绕。
_U64_MASK: Final = (1 << 64) - 1

#: 碱基 → 哈希权重。取值照抄上游 ``SEQ_HASH_VAL``：A=7、C=74、G=31、T=222，
#: 其余字符（含 N、小写碱基与非碱基字符）一律 13。
#:
#: 这几个数是素数，且**互不相同**——上游没解释来源，但显然是为了让四种碱基
#: 在位运算下尽量分散。它们是算法的一部分，不能"顺手换成更好看的数"：
#: 换了以后同一份数据的重复率就会变。
_BASE_HASH_VALUES: Final[dict[int, int]] = {
    ord("A"): 7,
    ord("C"): 74,
    ord("G"): 31,
    ord("T"): 222,
}

#: 256 项的查表，避免在内循环里做字典查找（Python 侧同样值得）。
SEQ_HASH_VALUE: Final[bytes] = bytes(
    _BASE_HASH_VALUES.get(code, 13) for code in range(256)
)


def _is_prime(number: int) -> bool:
    """试除法判素。范围小（最大到百万级），不需要更聪明的方法。"""
    limit = isqrt(number)
    divisor = 2
    while divisor <= limit:
        if number % divisor == 0:
            return False
        divisor += 1
    return True


def accuracy_memory_bytes(accuracy_level: int) -> int:
    """该档位的位图占用（字节）。界面上要如实告诉用户"选它要花多少内存"。"""
    if accuracy_level not in _ACCURACY_PLAN:
        raise ValueError(
            f"accuracy_level 必须在 1 到 6 之间，当前为 {accuracy_level}。"
        )
    count, buffer_bytes = _ACCURACY_PLAN[accuracy_level]
    return count * buffer_bytes


@lru_cache(maxsize=None)
def generate_prime_table(count: int) -> tuple[int, ...]:
    """复现上游 ``Duplicate::initPrimeArrays`` 的素数表。

    **它不是"前 ``count`` 个素数"**，而是"每万个数的区间里取第一个素数"：
    从 10001 开始，找到一个素数就记下，然后跳过剩下 9999 个数再找。
    这一点很容易看漏——按"前 N 个连续素数"实现，哈希位置会全错，
    而错的结果**看起来仍然像随机哈希**（重复率数字依然合理），
    因此必须有对照向量才能发现。本模块用上游同款生成方式。

    结果按 ``count`` 缓存：同一档位反复构造检测器时不必重算
    （每档最多 4096 个数，但试除法的循环量不小）。
    """
    table: list[int] = []
    number = 10000
    while len(table) < count:
        number += 1
        if _is_prime(number):
            table.append(number)
            number += 10000
    return tuple(table)


class DuplicateDetector:
    """布隆过滤器式的重复检测器：逐条喂入，问它"见过没有"。

    用法::

        detector = DuplicateDetector(accuracy_level=1)
        is_duplicate = detector.check_read(b"ACGT...")
        print(detector.duplicate_rate)

    一个实例就是**一次运行的全部记忆**——换了数据集要新建实例，
    否则上一批的比特还留着，会凭空多出重复。

    ``buffer_bytes`` 是给测试与小内存场景的**覆盖口**，生产用默认（``None``，
    按档位取值）。两侧实现要对拍时**必须传同一个值**：位图大小直接决定
    位置向量怎么取模，进而决定假阳性有多少，改小了结论就变。
    """

    def __init__(
        self,
        accuracy_level: int = DEFAULT_ACCURACY_ANALYZE,
        *,
        buffer_bytes: int | None = None,
    ) -> None:
        if accuracy_level not in _ACCURACY_PLAN:
            raise ValueError(
                f"accuracy_level 必须在 1 到 6 之间，当前为 {accuracy_level}。"
            )
        self.accuracy_level = accuracy_level
        self.buffer_count, default_bytes = _ACCURACY_PLAN[accuracy_level]
        if buffer_bytes is None:
            self.buffer_bytes = default_bytes
        else:
            if buffer_bytes < 1:
                raise ValueError(f"buffer_bytes 必须为正，当前为 {buffer_bytes}。")
            self.buffer_bytes = buffer_bytes
        self.buffer_len_in_bits = self.buffer_bytes * 8
        # 位置向量在素数表里的下标用"与掩码"取值而不是取模：因为
        # 素数表长度 512 × 缓冲区个数 恒为 2 的幂，与掩码等价且更快。
        self.offset_mask = _PRIME_ARRAY_LEN * self.buffer_count - 1
        self.prime_table = generate_prime_table(
            self.buffer_count * _PRIME_ARRAY_LEN
        )
        self._bitmap = bytearray(self.buffer_bytes * self.buffer_count)

        self.total_reads = 0
        self.duplicate_reads = 0

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def memory_bytes(self) -> int:
        """位图占用的字节数（不含素数表，它只有几十 KB）。"""
        return self.buffer_bytes * self.buffer_count

    @property
    def duplicate_rate(self) -> float:
        """重复率。还没喂任何数据时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.duplicate_reads / self.total_reads

    def sequence_positions(
        self,
        sequence: bytes,
        pos_offset: int = 0,
        positions: list[int] | None = None,
    ) -> list[int]:
        """把一条序列压成 ``buffer_count`` 个 64 位位置向量。

        对应上游 ``Duplicate::seq2intvector``。逐个碱基累加::

            positions[i] += prime[(p + pos_offset) * buffer_count + i]
                            × (hash[base] + p + pos_offset)

        三点必须照做：

        1. **累加而不是赋值**。整条序列的所有碱基都往同一组位置上叠加，
           因此位置向量是"整条序列的函数"，不是某个碱基的函数。
        2. **uint64 回绕**。C 侧是无符号整数，溢出就是取模；Python 的整数
           无限精度，必须显式 ``& (2**64 - 1)``，否则两侧结果不同。
        3. **位置下标与碱基值耦合**（乘的是 ``hash + p``，不是 ``hash``）。
           这让同一条序列在不同位置上被区分开——只按碱基内容哈希的话，
           `AAC` 与 `ACA` 会撞在一起。

        ``pos_offset`` 是给双端用的：上游把 R1 与 R2 **首尾相接**成一条虚序列
        再一起哈希，所以 R2 的位置要从 ``len(R1)`` 起算。

        ``positions`` 传入时在它上面继续累加（双端就是这么做的），
        默认新建一个全 0 的。
        """
        if positions is None:
            positions = [0] * self.buffer_count
        hash_values = SEQ_HASH_VALUE
        primes = self.prime_table
        count = self.buffer_count
        mask = self.offset_mask
        for index, code in enumerate(sequence):
            weighted = hash_values[code] + index + pos_offset
            shifted = (index + pos_offset) * count
            for slot in range(count):
                value = positions[slot] + primes[(shifted + slot) & mask] * weighted
                positions[slot] = value & _U64_MASK
        return positions

    def _apply_bloom_filter(self, positions: list[int]) -> bool:
        """把位置向量对应的比特全部置 1；返回"置之前是否全都已经是 1"。

        对应上游 ``Duplicate::applyBloomFilter``。注意它**不短路**：
        哪怕第一个缓冲区就已经发现是新的，剩下的也照样要置位——
        否则后面的 read 就看不到这条序列留下的痕迹了。
        """
        is_duplicate = True
        bitmap = self._bitmap
        for slot, position in enumerate(positions):
            bit = position % self.buffer_len_in_bits
            index = slot * self.buffer_bytes + (bit >> 3)
            flag = 1 << (bit & 7)
            previous = bitmap[index]
            bitmap[index] = previous | flag
            is_duplicate = is_duplicate and (previous & flag) != 0
        return is_duplicate

    def check_read(self, sequence: bytes) -> bool:
        """单端：这条序列之前见过没有。统计也随之更新。"""
        is_duplicate = self._apply_bloom_filter(self.sequence_positions(sequence))
        self.total_reads += 1
        if is_duplicate:
            self.duplicate_reads += 1
        return is_duplicate

    def check_pair(self, read1: bytes, read2: bytes) -> bool:
        """双端：把 R1 与 R2 首尾相接后问"这一对见过没有"。

        对应上游 ``Duplicate::checkPair``——R2 的位置从 ``len(R1)`` 起算，
        其余与单端一样。因此一对 ``(R1, R2)`` 与另一对**调换了 R1/R2**
        不算重复（位置不同），这与"配对去重"的语义一致。
        """
        positions = self.sequence_positions(read1)
        self.sequence_positions(read2, len(read1), positions)
        is_duplicate = self._apply_bloom_filter(positions)
        self.total_reads += 1
        if is_duplicate:
            self.duplicate_reads += 1
        return is_duplicate
