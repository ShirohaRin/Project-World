"""重复检测核心的测试。

除了"重复的判得出来"，重点守三件容易出错、又很难靠肉眼发现的事：

1. **素数表是"每万区间取第一个素数"**，不是"前 N 个素数"。写错了哈希位置全错，
   而错的结果看起来仍然像合理的随机哈希——所以必须有硬编码的对照向量；
2. **uint64 回绕**。C 侧是无符号整数，Python 是无限精度；漏掉取模两侧就不同；
3. **位置向量与位置耦合**（乘的是 `hash + p`），所以 `AAC` 与 `ACA` 不能撞在一起。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.submodules.deduplication import (
    DEFAULT_ACCURACY_ANALYZE,
    DEFAULT_ACCURACY_DEDUP,
    SEQ_HASH_VALUE,
    DuplicateDetector,
    accuracy_memory_bytes,
    generate_prime_table,
)

_U64_MASK = (1 << 64) - 1

#: 上游 ``Duplicate::initPrimeArrays`` 生成的前 8 项。
#: 若按"前 8 个素数"实现，这里会是 (2, 3, 5, 7, 11, 13, 17, 19)——一眼可辨。
_UPSTREAM_FIRST_PRIMES = (10007, 20011, 30013, 40031, 50033, 60037, 70039, 80051)

#: 上游注释里写明的档位内存（1G / 2G / 4G / 8G / 16G / 32G）。
_UPSTREAM_MEMORY_PLAN = (
    1 << 30,
    1 << 31,
    1 << 32,
    1 << 33,
    1 << 34,
    1 << 35,
)


def small_detector(**kwargs) -> DuplicateDetector:
    """测试用的检测器：档位取 1（缓冲区个数 2），但位图压到 64 KiB。

    位图大小只影响假阳性率，不影响算法逻辑；两侧实现对拍时同样要传这个值。
    """
    return DuplicateDetector(1, buffer_bytes=1 << 16, **kwargs)


# ---------------------------------------------------------------------------
# 素数表与档位
# ---------------------------------------------------------------------------


def test_prime_table_takes_first_prime_of_each_ten_thousand_band() -> None:
    assert generate_prime_table(8) == _UPSTREAM_FIRST_PRIMES


def test_prime_table_is_cached_for_repeated_construction() -> None:
    """同一长度反复构造检测器时不该重算素数表。"""
    assert generate_prime_table(1024) is generate_prime_table(1024)


def test_accuracy_levels_match_upstream_memory_plan() -> None:
    assert [accuracy_memory_bytes(level) for level in range(1, 7)] == list(
        _UPSTREAM_MEMORY_PLAN
    )


def test_default_levels_follow_upstream_choice() -> None:
    """只评估取 1（省内存），去重取 3（多花内存换低假阳性）。"""
    assert DEFAULT_ACCURACY_ANALYZE == 1
    assert DEFAULT_ACCURACY_DEDUP == 3
    assert DuplicateDetector().accuracy_level == 1


def test_unknown_accuracy_level_rejected() -> None:
    for level in (0, 7, -1):
        with pytest.raises(ValueError, match="accuracy_level"):
            DuplicateDetector(level)
        with pytest.raises(ValueError, match="accuracy_level"):
            accuracy_memory_bytes(level)


def test_buffer_size_override_must_be_positive() -> None:
    with pytest.raises(ValueError, match="buffer_bytes"):
        DuplicateDetector(1, buffer_bytes=0)
    with pytest.raises(ValueError, match="buffer_bytes"):
        DuplicateDetector(1, buffer_bytes=-8)


def test_buffer_override_only_changes_size_not_buffer_count() -> None:
    """覆盖的是"每个缓冲区多大"，缓冲区个数仍由档位决定。"""
    detector = DuplicateDetector(3, buffer_bytes=1 << 10)
    assert detector.buffer_count == 4  # 档位 3 = 4 个缓冲区
    assert detector.buffer_bytes == 1 << 10
    assert detector.offset_mask == 4 * 512 - 1


# ---------------------------------------------------------------------------
# 哈希表
# ---------------------------------------------------------------------------


def test_hash_values_follow_upstream_table() -> None:
    assert SEQ_HASH_VALUE[ord("A")] == 7
    assert SEQ_HASH_VALUE[ord("C")] == 74
    assert SEQ_HASH_VALUE[ord("G")] == 31
    assert SEQ_HASH_VALUE[ord("T")] == 222
    # 其余字符（含小写与 N）一律 13——上游对未知字符不做区分。
    for code in range(256):
        if chr(code) not in "ACGT":
            assert SEQ_HASH_VALUE[code] == 13, f"字符 {code} 的权重不对"


def test_small_letters_collide_with_unknown_characters() -> None:
    """小写碱基与 N 的权重同为 13，因此它们在哈希下不可区分。

    这是**上游的真实后果**：一段 ``a`` 与一段 ``N``（等长）会被判为互相重复。
    如实照抄，并在这里固定住——将来若有人想"顺手改对"，会先看到这条测试。
    """
    detector = small_detector()
    assert detector.check_read(b"aaaa") is False
    assert detector.check_read(b"NNNN") is True


# ---------------------------------------------------------------------------
# 位置向量的交叉验证
# ---------------------------------------------------------------------------


def _naive_positions(
    detector: DuplicateDetector,
    sequence: bytes,
    pos_offset: int = 0,
    positions: list[int] | None = None,
) -> list[int]:
    """朴素版：用 Python 任意精度累加，最后统一取模。

    与逐位取模的版本**等价**（模运算对加法与乘法同态），但写法完全不同，
    因此能抓住"取模取错位数/漏取模"这类错误。
    """
    values = [0] * detector.buffer_count if positions is None else list(positions)
    count = detector.buffer_count
    mask = detector.offset_mask
    for index, code in enumerate(sequence):
        for slot in range(count):
            table_index = ((index + pos_offset) * count + slot) & mask
            values[slot] += detector.prime_table[table_index] * (
                SEQ_HASH_VALUE[code] + index + pos_offset
            )
    return [value & _U64_MASK for value in values]


def test_positions_match_naive_accumulation_on_random_data() -> None:
    rng = random.Random(20260922)
    detector = small_detector()
    for _ in range(200):
        length = rng.randint(0, 200)
        sequence = bytes(rng.choice(b"ACGTN") for _ in range(length))
        offset = rng.randint(0, 50)
        assert detector.sequence_positions(sequence, offset) == _naive_positions(
            detector, sequence, offset
        )


def test_positions_wrap_at_64_bits() -> None:
    """累加必须按 uint64 回绕，不能留 Python 的无限精度。

    **为什么要替换素数表**：正常数据下累加值离 $2^{64}$ 还很远——要自然溢出
    得有几百万个碱基的序列，那样测试跑不动。把素数表换成 $\approx 2^{62}$ 的量级后，
    第一次累加就越界，因此这条测试真正测的是"有没有取模"，
    而不是"值恰好很大"。
    """
    detector = small_detector()
    huge = (1 << 62) + 1
    detector.prime_table = tuple(huge for _ in range(len(detector.prime_table)))

    positions = detector.sequence_positions(b"ACGTACGT")

    assert all(0 <= value <= _U64_MASK for value in positions)
    assert positions == _naive_positions(detector, b"ACGTACGT")


def test_pair_accumulates_into_the_same_positions() -> None:
    """双端 = R1 与 R2 首尾相接成一条虚序列，位置从 len(R1) 起算。"""
    detector = small_detector()
    left = detector.sequence_positions(b"ACGTACGTAC", 0)
    detector.sequence_positions(b"TTTTGGGGCC", 10, left)
    assert left == detector.sequence_positions(b"ACGTACGTACTTTTGGGGCC")


# ---------------------------------------------------------------------------
# 判重行为
# ---------------------------------------------------------------------------


def test_first_occurrence_is_not_a_duplicate() -> None:
    detector = small_detector()
    assert detector.check_read(b"ACGTACGTACGTACGT") is False
    assert detector.total_reads == 1
    assert detector.duplicate_reads == 0
    assert detector.duplicate_rate == 0.0


def test_repeated_sequence_is_a_duplicate() -> None:
    detector = small_detector()
    detector.check_read(b"ACGTACGTACGTACGT")
    assert detector.check_read(b"ACGTACGTACGTACGT") is True
    assert (detector.total_reads, detector.duplicate_reads) == (2, 1)
    assert detector.duplicate_rate == 0.5


def test_different_sequences_do_not_collide() -> None:
    detector = small_detector()
    assert detector.check_read(b"ACGTACGTACGTACGT") is False
    assert detector.check_read(b"TTTTGGGGCCCCAAAA") is False
    assert detector.check_read(b"GATTACAGATTACA") is False
    assert detector.duplicate_reads == 0


def test_nearby_sequences_do_not_collide() -> None:
    """只差一个碱基也必须判成不同——哈希是按整条序列累加的。"""
    detector = small_detector()
    assert detector.check_read(b"ACGTACGTACGTACGT") is False
    assert detector.check_read(b"ACGTACGTACGTACGA") is False
    assert detector.check_read(b"ACGTACGTACGTACGG") is False
    assert detector.duplicate_reads == 0


def test_position_of_bases_matters() -> None:
    """碱基相同、顺序不同 → 不同序列（乘的是 hash + 位置，不是只有 hash）。"""
    detector = small_detector()
    assert detector.check_read(b"AAC") is False
    assert detector.check_read(b"ACA") is False
    assert detector.check_read(b"CAA") is False
    assert detector.duplicate_reads == 0


def test_empty_sequence_follows_the_same_rule() -> None:
    """空序列也走同一条路径：第一次不算重复，第二次算。"""
    detector = small_detector()
    assert detector.check_read(b"") is False
    assert detector.check_read(b"") is True


def test_pair_is_the_concatenation_of_both_reads() -> None:
    detector = small_detector()
    assert detector.check_pair(b"ACGTACGT", b"TTTTGGGG") is False
    # 同一组位置：单端喂入 R1+R2 时判为重复。
    assert detector.check_read(b"ACGTACGTTTTTGGGG") is True


def test_pair_order_matters() -> None:
    """调换 R1/R2 不算同一对（位置不同）。"""
    detector = small_detector()
    assert detector.check_pair(b"ACGTACGT", b"TTTTGGGG") is False
    assert detector.check_pair(b"TTTTGGGG", b"ACGTACGT") is False
    assert detector.duplicate_reads == 0


def test_duplicate_rate_is_zero_before_any_input() -> None:
    assert small_detector().duplicate_rate == 0.0


def test_bitmap_is_not_shared_between_detectors() -> None:
    """两次运行之间不能串味：新实例必须从空位图开始。"""
    first = small_detector()
    first.check_read(b"ACGTACGTACGT")
    second = small_detector()
    assert second.check_read(b"ACGTACGTACGT") is False
