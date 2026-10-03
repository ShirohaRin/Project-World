"""序列层面的公共工具：反向互补、带提前退出的错配计数。

**这是工具，不是算法**（见 `开发规则.md` 3.2 第 1 类）：它没有自己的输入输出契约，
只是别的算法里重复出现的那几行。目前的使用方：接头裁剪（错配计数）、双端 overlap
分析（两者都用）。

**产出**：给定碱基串，返回反向互补串；给定两条等长碱基串，返回前若干个碱基的错配数
（超过上限就提前收工，返回值语义见下）。没有文件、没有统计。
"""

from __future__ import annotations

from typing import Final

#: 互补表（A↔T、C↔G）。用小写也兼容，其余字符原样保留（上游对未知字符不报错）。
#: ``bytes.translate`` 需要 256 字节的转换表，因此用 ``bytes.maketrans``
#: （``str.maketrans`` 返回的是字典，喂给 bytes.translate 会报错）。
_COMPLEMENT: Final[bytes] = bytes.maketrans(b"ACGTNacgtn", b"TGCANtgcan")


def reverse_complement(sequence: bytes) -> bytes:
    """返回 ``sequence`` 的反向互补串。

    与上游 ``fastp_simd::reverseComplement`` 一致：只做互补与反转，
    非 ACGTN 的字符原样保留（上游不校验，这里也不校验）。
    """
    return sequence.translate(_COMPLEMENT)[::-1]


def complement(base: int) -> int:
    """单个碱基的互补（参数与返回值都是 ASCII 字节值）。

    与 C++ 侧 ``sequence.h`` 的 ``complement_of`` 对应。上游 base correction
    要逐位比较互补碱基，用的是**单个碱基**的互补，而不是整串。
    """
    return _COMPLEMENT[base]


def count_mismatches_bounded(
    left: bytes,
    right: bytes,
    length: int,
    limit: int,
) -> int:
    """比对前 ``length`` 个碱基的错配数；超过 ``limit`` 就提前收工。

    对应上游 ``fastp_simd::countMismatchesBounded``：返回值的语义是
    "错配数，但一旦超过 limit 就返回一个 > limit 的数"，因此调用方用
    ``<= limit`` 判断命中。``length`` 超出任一条串的长度时由调用方保证不发生
    （上游同样不做边界检查）。
    """
    mismatches = 0
    for index in range(length):
        if left[index] != right[index]:
            mismatches += 1
            if mismatches > limit:
                return mismatches
    return mismatches


def count_mismatches(left: bytes, right: bytes, length: int) -> int:
    """比对前 ``length`` 个碱基的**完整**错配数（不提前退出）。

    上游在两个地方需要"精确值"而不是"是否超限"：overlap 分析在比对长度超过
    50 时要把真实错配数报出去（哪怕它很大），因此这里不做提前退出。
    """
    mismatches = 0
    for index in range(length):
        if left[index] != right[index]:
            mismatches += 1
    return mismatches
