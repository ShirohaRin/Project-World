"""种子索引的确定性用例。

两条硬性质贯穿全篇：

1. **位置必须是真的**——对索引里每一条 k-mer，用它记下的每个位置回切参考，
   切出来的窗口编码必须正好等于那个 k-mer；
2. **位置表必须完整**——对每个保留的 k-mer，把参考上所有 ``≡ 0 (mod step)``
   的窗口独立算一遍，和索引里的位置**逐个相等**（多一个、少一个、被截断都要被发现）。

第 2 条是专门冲着"重复 k-mer 只留前几个"这类看似能用、实则有系统性偏差的写法去的。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.submodules.read_mapping import (
    ReferenceIndex,
    SeedIndex,
    decode_kmer,
    encode_kmer,
    reverse_complement_kmer,
)

_SEED = 20260923
_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _random_sequence(length: int, seed: int = _SEED) -> str:
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT") for _ in range(length))


def _expected_index(sequence: str, k: int, step: int) -> dict[int, list[int]]:
    """独立地把"参考上每个 k-mer 落在 step 网格上的全部位置"算一遍。"""
    grouped: dict[int, list[int]] = {}
    for start in range(0, len(sequence) - k + 1, step):
        code = encode_kmer(sequence[start : start + k])
        if code is None:
            continue
        grouped.setdefault(code, []).append(start)
    return grouped


def _assert_index_is_truthful(sequence: str, index: SeedIndex, max_hits: int) -> None:
    """位置既真又全：每条保留的 k-mer，其位置表必须与独立算出的完全一致。"""
    expected = _expected_index(sequence, index.k, index.step)
    for code, positions in index.positions.items():
        for start in positions:
            assert encode_kmer(sequence[start : start + index.k]) == code
        assert sorted(positions) == expected[code]
    for code, positions in expected.items():
        if code not in index.positions:
            # 只允许一种缺席原因：命中数超过上限、整条被剔除。
            assert len(positions) > max_hits
    assert index.kmer_count == len(expected) - index.pruned_kmers
    assert index.window_count == sum(len(bucket) for bucket in expected.values())


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("k", [1, 2, 3, 4, 8])
def test_encode_decode_round_trip_over_all_kmers(k: int) -> None:
    for code in range(1 << (2 * k)):
        text = decode_kmer(code, k)
        assert len(text) == k
        assert encode_kmer(text) == code


def test_encode_accepts_lowercase_and_rejects_non_acgt() -> None:
    assert encode_kmer("acgt") == encode_kmer("ACGT")
    assert encode_kmer("ACGN") is None
    assert encode_kmer("ACGTX") is None
    assert encode_kmer("") is None
    assert encode_kmer("A C") is None


def test_decode_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        decode_kmer(0, 0)
    with pytest.raises(ValueError):
        decode_kmer(1 << 6, 3)


def test_reverse_complement_kmer_matches_string_version() -> None:
    """与另写一份的字符串反向互补对拍（3-mer 全枚举）。"""
    for code in range(1 << 6):
        text = decode_kmer(code, 3)
        expected = text.translate(_COMPLEMENT)[::-1]
        assert decode_kmer(reverse_complement_kmer(code, 3), 3) == expected


def test_reverse_complement_kmer_rejects_bad_k() -> None:
    with pytest.raises(ValueError):
        reverse_complement_kmer(0, 0)


# ---------------------------------------------------------------------------
# 建索引
# ---------------------------------------------------------------------------


def test_build_dense_index_covers_every_window() -> None:
    sequence = _random_sequence(200)
    index = SeedIndex.build("chr", sequence, k=12, step=1)
    assert index.window_count == len(sequence) - 12 + 1
    assert index.skipped_windows == 0
    assert index.pruned_kmers == 0
    assert index.length == len(sequence)
    _assert_index_is_truthful(sequence, index, max_hits=64)


def test_sparse_index_only_places_windows_on_the_step_grid() -> None:
    sequence = _random_sequence(200)
    index = SeedIndex.build("chr", sequence, k=12, step=7)
    assert all(start % 7 == 0 for positions in index.positions.values() for start in positions)
    _assert_index_is_truthful(sequence, index, max_hits=64)


def test_windows_containing_n_are_skipped() -> None:
    sequence = _random_sequence(120)
    broken = f"{sequence[:40]}N{sequence[41:80]}NN{sequence[82:]}"
    assert len(broken) == 120
    index = SeedIndex.build("chr", broken, k=10, step=1)
    # 起始位置落在 [31,40] 或 [71,81] 的窗口会盖住那三个 N，逐位建时共 21 个。
    assert index.skipped_windows == 21
    assert index.window_count == 111 - 21
    _assert_index_is_truthful(broken, index, max_hits=64)


def test_repetitive_kmer_is_pruned_entirely() -> None:
    """重复序列的 k-mer 要**整条剔除**，不能只留前几个位置。"""
    sequence = "A" * 100
    index = SeedIndex.build("chr", sequence, k=8, step=1, max_hits=5)
    assert index.pruned_kmers == 1
    assert index.lookup(encode_kmer("AAAAAAAA")) == ()
    assert index.kmer_count == 0


def test_non_repetitive_neighbour_survives_pruning() -> None:
    sequence = "A" * 60 + "ACGTACGTACGTACGT" + "A" * 40
    index = SeedIndex.build("chr", sequence, k=8, step=1, max_hits=4)
    assert index.pruned_kmers == 1
    assert index.lookup_text("ACGTACGT") != ()
    _assert_index_is_truthful(sequence, index, max_hits=4)


def test_sequence_shorter_than_seed_yields_empty_index() -> None:
    index = SeedIndex.build("tiny", "ACGTA", k=16, step=1)
    assert index.window_count == 0
    assert index.kmer_count == 0
    with pytest.raises(ValueError):
        index.lookup_text("ACG")  # 长度不等于 k，直接报错


def test_lookup_unknown_kmer_is_empty() -> None:
    sequence = _random_sequence(120)
    index = SeedIndex.build("chr", sequence, k=12, step=1)
    absent = encode_kmer("AAAAAAAAAAAA")
    assert absent is not None
    assert index.lookup(absent) == ()


def test_lookup_text_checks_length() -> None:
    index = SeedIndex.build("chr", _random_sequence(80), k=12, step=1)
    with pytest.raises(ValueError):
        index.lookup_text("ACGT")


def test_lookup_text_finds_true_position() -> None:
    sequence = _random_sequence(200)
    index = SeedIndex.build("chr", sequence, k=12, step=1)
    window = sequence[64:76]
    assert 64 in index.lookup_text(window)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"k": 0}, "种子长度"),
        ({"step": 0}, "步长"),
        ({"max_hits": 0}, "命中上限"),
    ],
)
def test_build_rejects_bad_parameters(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SeedIndex.build("chr", "ACGTACGTACGT", **kwargs)


def test_stats_reports_the_expected_shape() -> None:
    index = SeedIndex.build("chr", _random_sequence(200), k=16, step=4)
    stats = index.stats()
    assert stats["seq_id"] == "chr"
    assert stats["k"] == 16
    assert stats["step"] == 4
    assert stats["positions"] == sum(len(bucket) for bucket in index.positions.values())
    assert stats["mean_hits"] >= 1.0


# ---------------------------------------------------------------------------
# 多序列索引
# ---------------------------------------------------------------------------


def test_reference_index_keeps_sequences_separate() -> None:
    from modules.bio_analysis_function.common.reference_io import (
        ReferenceSequence,
        ReferenceSet,
    )

    first = _random_sequence(150, seed=1)
    second = _random_sequence(150, seed=2)
    reference = ReferenceSet.of(
        [
            ReferenceSequence(seq_id="chr", sequence=first),
            ReferenceSequence(seq_id="plasmid", sequence=second),
        ]
    )
    index = ReferenceIndex.build(reference, k=12, step=1)
    assert index.seq_ids == ("chr", "plasmid")
    window = first[30:42]
    code = encode_kmer(window)
    assert code is not None
    assert 30 in index.lookup("chr", code)
    # 另一条序列上的同一位点不应被当成同一回事
    assert index.lookup("plasmid", code) == index.get("plasmid").lookup(code)
    with pytest.raises(KeyError, match="没有参考序列"):
        index.get("missing")
    assert len(list(index.stats())) == 2


# ---------------------------------------------------------------------------
# 真实数据
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_reference_index_finds_true_positions() -> None:
    """在 phiX174 真实序列上建索引：位置既真又全（对整条索引逐条核对）。"""
    from modules.bio_analysis_function.common.reference_io import read_genbank

    reference = read_genbank(_REAL_GENBANK)
    index = ReferenceIndex.build(reference, k=16, step=4).get("NC_001422")
    sequence = reference.get("NC_001422").sequence
    assert index.length == 5386
    assert index.kmer_count > 0
    _assert_index_is_truthful(sequence, index, max_hits=64)

    # 取基因组里一段确定的 16-mer：起点 4000 落在 step=4 的网格上，必须能找回自己。
    probe = sequence[4000:4016]
    assert encode_kmer(probe) is not None
    assert 4000 in index.lookup_text(probe)
