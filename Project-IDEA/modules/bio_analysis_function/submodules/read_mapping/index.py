"""参考序列的 k-mer 种子索引。

比对器的第一块：把参考切成固定长度的 k-mer，记下每个 k-mer 在参考上的位置，
之后 read 的 k-mer 一查就知道"可能落在哪里"。本文件只管**建索引与查询**，
不做种子挑选、不做扩展、不判最佳命中——那些在后续分片里。

编码
----

碱基按 2 bit 打包：``A=00``、``C=01``、``G=10``、``T=11``，于是长度 k 的 k-mer 是一个
k*2 bit 的整数。含 ``N``（或任何非 ACGT）的窗口**不能做种子**，直接跳过——
这是比对的硬约束，不是保守：拿含 N 的 k-mer 去查只会得到无意义的命中。

稀疏索引与它成立的条件
----------------------

逐位建索引（每个位置一个窗口）最灵敏，但内存随参考长度线性增长：4.6 Mbp 的细菌基因组
在 Python 里大约要几百 MB。因此默认每隔 ``step`` 个位置建一个窗口。

稀疏**不损失正确性，只影响灵敏度**，条件可以写清楚：设真实比对的对角线为
``d``（参考位置 = d + read 偏移），窗口落在 ``≡ 0 (mod step)`` 的参考位置上，
于是只要 read 在真实对角线上存在一段**长度 ≥ step 的连续完全匹配**，
其中必有一个 read 偏移 ``j`` 满足 ``j ≡ -d (mod step)``，该处的种子就一定会命中。
换句话：错配/indel 把完全匹配切成的每一段都比 ``step`` 短时，才可能漏掉这个种子。
本线的目标是细菌重测序（与参考差异 <1‰、读长 100~150 bp），
在这种数据上连续完全匹配段远长于 4，所以默认 ``step=4`` 是安全的。

重复序列
--------

高度重复的 k-mer 会命中成千上万个位置，既慢又没用。因此建索引时给每个 k-mer 的
位置数设上限 ``max_hits``：超过上限的 k-mer **整条剔除**（不是截断保留前几个——
那样留下的是"偏心的"位置表，看起来能用、其实有系统性偏差）。
剔除条数记在 :attr:`SeedIndex.pruned_kmers` 里，会随统计一并报出来。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ...common.reference_io import ReferenceSet

__all__ = [
    "ReferenceIndex",
    "SeedIndex",
    "decode_kmer",
    "encode_kmer",
    "reverse_complement_kmer",
]

_BASE_TO_BITS = {"A": 0, "C": 1, "G": 2, "T": 3}
_BITS_TO_BASE = ("A", "C", "G", "T")

#: 默认种子长度与建窗口步长。见模块文档对 step 成立条件的说明。
DEFAULT_SEED_LENGTH = 16
DEFAULT_STEP = 4
#: 单个 k-mer 允许多少个位置；超过就整条剔除。
DEFAULT_MAX_HITS = 64


def encode_kmer(text: str) -> int | None:
    """把 k-mer 编码成整数；含非 ACGT 字母时返回 ``None``。

    小写会先转成大写（对 DNA 是无损的），空串视为不可用。
    """
    if not text:
        return None
    code = 0
    for character in text.upper():
        bits = _BASE_TO_BITS.get(character)
        if bits is None:
            return None
        code = (code << 2) | bits
    return code


def decode_kmer(code: int, k: int) -> str:
    """把编码还原成 k-mer 文本（排障与测试用）。"""
    if k < 1:
        raise ValueError(f"k 必须 ≥ 1，当前为 {k}。")
    if code < 0 or code >= 1 << (2 * k):
        raise ValueError(f"编码 {code} 超出长度 {k} 的 k-mer 取值范围。")
    characters = ["A"] * k
    for index in range(k - 1, -1, -1):
        characters[index] = _BITS_TO_BASE[code & 0b11]
        code >>= 2
    return "".join(characters)


def reverse_complement_kmer(code: int, k: int) -> int:
    """k-mer 编码的反向互补编码（按 2 bit 分组逐组互补并颠倒组序）。"""
    if k < 1:
        raise ValueError(f"k 必须 ≥ 1，当前为 {k}。")
    result = 0
    for _ in range(k):
        result = (result << 2) | (code & 0b11) ^ 0b11
        code >>= 2
    return result


@dataclass(frozen=True, slots=True)
class SeedIndex:
    """一条参考序列的 k-mer 种子索引。位置是 **0-based** 的窗口起点。"""

    seq_id: str
    length: int
    k: int
    step: int
    positions: Mapping[int, tuple[int, ...]]
    window_count: int = 0
    skipped_windows: int = 0
    pruned_kmers: int = 0

    @property
    def kmer_count(self) -> int:
        """索引里保留了多少个不同的 k-mer。"""
        return len(self.positions)

    def lookup(self, kmer: int) -> tuple[int, ...]:
        """查询某个 k-mer 的候选位置；没有命中或该 k-mer 已被剔除时返回空元组。"""
        return self.positions.get(kmer, ())

    def lookup_text(self, text: str) -> tuple[int, ...]:
        """按 k-mer **文本**查询（长度必须等于索引的 k）。"""
        if len(text) != self.k:
            raise ValueError(f"查询的 k-mer 长度必须是 {self.k}，当前为 {len(text)}。")
        code = encode_kmer(text)
        return () if code is None else self.lookup(code)

    @classmethod
    def build(
        cls,
        seq_id: str,
        sequence: str,
        *,
        k: int = DEFAULT_SEED_LENGTH,
        step: int = DEFAULT_STEP,
        max_hits: int = DEFAULT_MAX_HITS,
    ) -> SeedIndex:
        """对一条参考序列建索引。

        参数：
            seq_id: 参考序列名（与 ``ReferenceSequence.seq_id`` 一致）。
            sequence: 参考序列（大写或小写都可，内部按大写处理）。
            k: 种子长度，必须 ≥ 1。k 越大命中越少、越特异，但越容易被错配打断。
            step: 建窗口的步长，必须 ≥ 1；1 表示逐位建。
            max_hits: 单个 k-mer 允许的位置数上限，超过则整条剔除。
        """
        if k < 1:
            raise ValueError(f"种子长度 k 必须 ≥ 1，当前为 {k}。")
        if step < 1:
            raise ValueError(f"步长 step 必须 ≥ 1，当前为 {step}。")
        if max_hits < 1:
            raise ValueError(f"命中上限 max_hits 必须 ≥ 1，当前为 {max_hits}。")
        text = sequence.upper()
        collected: dict[int, list[int]] = {}
        overflowed: set[int] = set()
        windows = 0
        skipped = 0
        for start in range(0, max(0, len(text) - k + 1), step):
            code = encode_kmer(text[start : start + k])
            if code is None:
                skipped += 1
                continue
            windows += 1
            if code in overflowed:
                continue
            bucket = collected.get(code)
            if bucket is None:
                collected[code] = [start]
                continue
            bucket.append(start)
            if len(bucket) > max_hits:
                overflowed.add(code)
                del collected[code]
        positions = {code: tuple(bucket) for code, bucket in collected.items()}
        return cls(
            seq_id=seq_id,
            length=len(text),
            k=k,
            step=step,
            positions=positions,
            window_count=windows,
            skipped_windows=skipped,
            pruned_kmers=len(overflowed),
        )

    def stats(self) -> dict[str, int | float]:
        """统计量（给报告与基准用）。"""
        total = sum(len(bucket) for bucket in self.positions.values())
        return {
            "seq_id": self.seq_id,
            "length": self.length,
            "k": self.k,
            "step": self.step,
            "window_count": self.window_count,
            "skipped_windows": self.skipped_windows,
            "kmer_count": self.kmer_count,
            "pruned_kmers": self.pruned_kmers,
            "positions": total,
            "mean_hits": (total / self.kmer_count) if self.kmer_count else 0.0,
        }


@dataclass(frozen=True, slots=True)
class ReferenceIndex:
    """一组参考序列的索引（染色体 + 质粒各自一份）。

    breseq 的参考可以是多条序列，比对结果里用参考名区分，所以索引也按名字分开存，
    查询时先给名字再给 k-mer——**不把不同序列的命中混在一起**。
    """

    indexes: Mapping[str, SeedIndex]

    @property
    def seq_ids(self) -> tuple[str, ...]:
        """索引里包含的参考序列名。"""
        return tuple(self.indexes)

    def get(self, seq_id: str) -> SeedIndex:
        """取某条序列的索引，取不到就报错。"""
        try:
            return self.indexes[seq_id]
        except KeyError:
            raise KeyError(
                f"索引里没有参考序列 {seq_id!r}，现有：{'、'.join(self.seq_ids)}。"
            ) from None

    def lookup(self, seq_id: str, kmer: int) -> tuple[int, ...]:
        """在某条参考序列上查询 k-mer 的位置。"""
        return self.get(seq_id).lookup(kmer)

    @classmethod
    def build(
        cls,
        reference: ReferenceSet,
        *,
        k: int = DEFAULT_SEED_LENGTH,
        step: int = DEFAULT_STEP,
        max_hits: int = DEFAULT_MAX_HITS,
    ) -> ReferenceIndex:
        """对一组参考序列建索引。"""
        return cls(
            indexes={
                sequence.seq_id: SeedIndex.build(
                    sequence.seq_id, sequence.sequence, k=k, step=step, max_hits=max_hits
                )
                for sequence in reference
            }
        )

    def stats(self) -> Iterable[dict[str, int | float]]:
        """逐条序列的统计量。"""
        return (index.stats() for index in self.indexes.values())
