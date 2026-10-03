"""检测算法本体的测试。

这个算法的行为有几处"反直觉但必须守住"的地方，测试主要围绕它们：

1. **两段式**：先查已知接头表，命中就直接返回表中那条完整序列；没命中才做
   k-mer 富集。两段的判定阈值完全不同。
2. **第二段在样本充分时常常给不出结果**。原因不是实现有错：拼出来的序列
   末尾会缺 1 个碱基（扫描时排除末尾，见 `shift_tail`），又因为各条 read 的
   插入片段长短不一，前缀树在接头 3' 端会提前停住，于是拼出来的序列**短于**
   已知表里的条目，吸附不上，只能返回"未检测到"。样本很小时反而会返回
   一个种子 k-mer（退化结果）。这两条都是上游的真实行为。
3. **并列怎么取舍**：已知表那一段用严格大于比较、且按字典序遍历，
   所以两个接头命中数相同时返回字典序更小的那条。

为了让纯 Python 版跑得动，测试里的已知表是**缩表**（几条而不是 234 条），
样本也控制在 2000 条 read 上下；这与算法逻辑无关，只是把"表长 × read 数"
这个乘法压下来（默认表下的成本见 README 的性能说明）。
"""

from __future__ import annotations

import random

import pytest

from modules.bio_analysis_function.common.adapter_detection.algorithm import (
    AdapterDetectionConfig,
    _key_at,
    _sequence_of_key,
    check_known_adapters,
    detect_adapter,
    detect_from_kmers,
    match_known_adapter,
)
from modules.bio_analysis_function.common.known_adapters import (
    KNOWN_ADAPTERS,
)

BASES = "ACGT"
TRUSEQ = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
#: 缩表：只留几条以 TruSeq 开头的接头，用于第一段的测试。
SMALL_TABLE = tuple(
    adapter for adapter in KNOWN_ADAPTERS if adapter.startswith("AGATCGGAAGAGCACACGTCTGAACTCCAGTC")
)


def make_reads(
    count: int,
    adapter: str | None,
    adapter_reads: int,
    *,
    seed: int = 20260917,
    length: int = 151,
) -> list[bytes]:
    """造一批 read：前 ``adapter_reads`` 条带接头（插入片段长度随机），其余是纯基因组序列。"""
    rng = random.Random(seed)
    reads: list[bytes] = []
    for index in range(count):
        if adapter is not None and index < adapter_reads:
            insert_length = rng.randint(50, 130)
            insert = "".join(rng.choices(BASES, k=insert_length))
            reads.append((insert + adapter)[:length].encode("ascii"))
        else:
            reads.append("".join(rng.choices(BASES, k=length)).encode("ascii"))
    return reads


# --------------------------------------------------------------------------
# 已知接头表（第一段）
# --------------------------------------------------------------------------


def test_known_table_hit_returns_full_sequence() -> None:
    """命中已知表时，返回的是表里那条**完整**序列，而不是数据里的一小段。"""
    reads = make_reads(2000, TRUSEQ, 400)
    config = AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)

    result = detect_adapter(reads, config)

    assert result.detected
    assert result.source == "known"
    assert result.adapter in SMALL_TABLE
    assert result.adapter == TRUSEQ


def test_known_table_miss_when_too_few_reads_carry_it() -> None:
    """接头只在极少数 read 里出现时，第一段不该命中。"""
    reads = make_reads(2000, TRUSEQ, 4)  # 0.2%，低于 1/200 的判定线
    config = AdapterDetectionConfig(min_reads=1, adapters=SMALL_TABLE)

    known, checked_reads, hits = check_known_adapters(reads, config)

    assert known == ""
    assert hits == 0
    assert checked_reads == 2000


def test_known_table_tie_returns_lexicographically_smaller() -> None:
    """两条接头命中数相同时，返回字典序更小的那条（上游 std::map 顺序 + 严格大于）。"""
    first = "AGATCGGAAGAGC"
    second = "AGATCGGAAGAGCACAC"
    assert first < second
    # 两条都是这批 read 里接头的前缀，因此命中数完全相同
    reads = make_reads(2000, TRUSEQ, 400)
    config = AdapterDetectionConfig(min_reads=1, adapters=(second, first))

    known, checked_reads, hits = check_known_adapters(reads, config)

    assert known == first
    assert hits > 0


def test_known_table_ignores_adapters_longer_than_read() -> None:
    """接头比 read 还长时直接跳过（上游 `if(alen >= rlen) continue;`）。"""
    reads = [b"ACGTACGTAC"] * 100
    config = AdapterDetectionConfig(min_reads=1, adapters=("ACGTACGTACGTACGTACGTACGT",))

    known, _, _ = check_known_adapters(reads, config)

    assert known == ""


def test_known_table_allows_one_mismatch_per_16_bases() -> None:
    """容错规则：比对长度每 16 个碱基允许 1 个错配。

    这里造 100 条 read，每条在接头的第 5 个碱基处有一个错配；
    比对长度 ~33 时允许 2 个错配，因此应当全部命中。
    """
    rng = random.Random(7)
    reads: list[bytes] = []
    for _ in range(100):
        mutated = list(TRUSEQ)
        mutated[4] = rng.choice([b for b in BASES if b != mutated[4]])
        reads.append(("".join(rng.choices(BASES, k=60)) + "".join(mutated)).encode("ascii"))
    config = AdapterDetectionConfig(min_reads=1, adapters=(TRUSEQ,))

    known, _, hits = check_known_adapters(reads, config)

    assert known == TRUSEQ
    assert hits == 100


def test_known_table_empty_disables_first_stage() -> None:
    """传空表等于跳过第一段。"""
    reads = make_reads(300, TRUSEQ, 60)
    known, checked_reads, hits = check_known_adapters(
        reads, AdapterDetectionConfig(min_reads=1, adapters=())
    )

    assert known == ""
    assert checked_reads == 0
    assert hits == 0


# --------------------------------------------------------------------------
# 匹配辅助函数
# --------------------------------------------------------------------------


def test_match_known_adapter_requires_prefix_of_table_entry() -> None:
    adapters = ("AGATCGGAAGAGC", "TTTTGGGGCCCC")

    # 表里的条目是给定序列的前缀 → 返回表里那一条
    assert match_known_adapter("AGATCGGAAGAGCACACGT", adapters) == "AGATCGGAAGAGC"
    # 序列比表里的条目短 → 认不出来
    assert match_known_adapter("AGATCGGAAGAG", adapters) == ""
    # 有一个错配就不认（这一段不允许错配）
    assert match_known_adapter("AGATCGGAAGAGTACACGT", adapters) == ""
    # 完全不相干
    assert match_known_adapter("CCCCCCCCCCCCCCCC", adapters) == ""


# --------------------------------------------------------------------------
# k-mer 编解码
# --------------------------------------------------------------------------


def test_key_encoding_round_trip() -> None:
    rng = random.Random(11)
    for _ in range(200):
        length = rng.randint(4, 20)
        sequence = bytes(rng.choice(b"ACGT") for _ in range(length))
        key_length = rng.randint(4, min(10, length))
        position = rng.randint(0, length - key_length)

        key = _key_at(sequence, position, key_length, -1)

        assert key >= 0
        assert _sequence_of_key(key, key_length) == sequence[
            position : position + key_length
        ].decode("ascii")


def test_rolling_key_matches_full_recomputation() -> None:
    """滚动取值必须与"每个窗口重新算一遍"完全一致（含 N 打断后的重算）。"""
    rng = random.Random(13)
    for _ in range(200):
        length = rng.randint(30, 120)
        sequence = bytes(rng.choice(b"ACGTN") for _ in range(length))
        key_length = rng.randint(4, 10)

        rolling = -1
        for position in range(0, length - key_length + 1):
            rolling = _key_at(sequence, position, key_length, rolling)
            window = sequence[position : position + key_length]
            if b"N" in window:
                assert rolling == -1
                continue
            expected = 0
            for base in window:
                expected = (expected << 2) + b"ATCG".index(base)
            assert rolling == expected


def test_key_of_n_containing_window_is_invalid() -> None:
    """含 N 的窗口没有有效编码；N 滑出窗口后重新有效。"""
    assert _key_at(b"ACGTNACGT", 0, 5, -1) == -1  # "ACGTN"
    assert _key_at(b"ACGTNACGT", 1, 4, -1) == -1  # "CGTN"
    # N 已经滑出窗口（从位置 5 起的 "ACGT"）——上一个键是 -1，于是走完整重算
    assert _key_at(b"ACGTNACGT", 5, 4, -1) >= 0


# --------------------------------------------------------------------------
# 从头检测（第二段）
# --------------------------------------------------------------------------


def test_kmer_stage_returns_seed_when_sample_is_tiny() -> None:
    """样本很小时的**退化**行为：前缀树不延伸，只返回种子本身。

    上游前缀树要求节点计数 ≥ 50 才继续走，40 条含接头的 read 达不到这个数，
    于是前后两条路径都是空的，"接头"就等于那个 10-mer 种子。

    计数不写死等于含接头的 read 数：随机序列里也可能碰巧出现同一个种子，
    因此断言"不少于 40"。
    """
    reads = make_reads(300, TRUSEQ, 40)
    config = AdapterDetectionConfig(min_reads=1, adapters=())

    result = detect_adapter(reads, config)

    assert result.detected
    assert result.source == "kmer"
    assert result.adapter is not None
    assert len(result.adapter) == 10
    assert result.adapter == result.seed_sequence
    assert result.seed_count >= 40


def test_kmer_stage_gives_no_result_when_tree_reaches_into_random_insert() -> None:
    """样本充分时拼出的序列吸附不上已知表，于是"未检测到"。

    这不是缺陷：拼出来的序列末尾缺 1 个碱基（扫描排除末尾），各条 read 的插入
    片段长短又不一，前缀树在接头 3' 端提前停住，结果比已知表里的条目短，
    吸附不上。上游的真实行为就是这样，用这条测试固定住。
    """
    reads = make_reads(2000, TRUSEQ, 400)
    config = AdapterDetectionConfig(min_reads=1, adapters=())

    result = detect_from_kmers(reads, config)

    assert not result.detected
    assert result.reason


def test_kmer_stage_finds_nothing_in_clean_data() -> None:
    """干净数据（无接头）不应当报出任何接头。"""
    reads = make_reads(2000, None, 0)
    result = detect_from_kmers(reads, AdapterDetectionConfig(min_reads=1, adapters=()))

    assert not result.detected


def test_kmer_stage_requires_enough_fold_enrichment() -> None:
    """富集倍数不够时不报结果：把接头稀释到极少数 read 里。"""
    reads = make_reads(2000, TRUSEQ, 6)  # 0.3%
    result = detect_from_kmers(reads, AdapterDetectionConfig(min_reads=1, adapters=()))

    assert not result.detected


# --------------------------------------------------------------------------
# 采样门槛与配置校验
# --------------------------------------------------------------------------


def test_sample_size_gate_reports_reason() -> None:
    """样本少于 min_reads 时不检测，并说明卡在哪一步。"""
    reads = make_reads(500, TRUSEQ, 100)

    result = detect_adapter(reads, AdapterDetectionConfig(min_reads=10_000))

    assert not result.detected
    assert "样本不足" in result.reason
    assert result.sampled_reads == 500


def test_default_config_matches_fastp() -> None:
    config = AdapterDetectionConfig()

    assert config.max_reads == 256 * 1024
    assert config.max_bases == 151 * 256 * 1024
    assert config.min_reads == 10_000
    assert config.key_length == 10
    assert config.shift_tail == 1
    assert config.max_adapter_length == 60
    assert config.adapters is KNOWN_ADAPTERS
    assert len(KNOWN_ADAPTERS) == 234


def test_invalid_config_values_raise() -> None:
    with pytest.raises(ValueError, match="min_reads"):
        AdapterDetectionConfig(min_reads=0)
    with pytest.raises(ValueError, match="key_length"):
        AdapterDetectionConfig(key_length=13)
    with pytest.raises(ValueError, match="shift_tail"):
        AdapterDetectionConfig(shift_tail=-1)
    with pytest.raises(ValueError, match="max_adapter_length"):
        AdapterDetectionConfig(max_adapter_length=0)


def test_render_reports_evidence() -> None:
    reads = make_reads(300, TRUSEQ, 40)
    result = detect_adapter(reads, AdapterDetectionConfig(min_reads=1, adapters=()))

    rendered = result.render()

    assert "检测到接头" in rendered
    assert "种子 k-mer" in rendered
    assert f"出现 {result.seed_count} 次" in rendered
    assert "富集" in rendered
