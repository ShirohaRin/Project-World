"""无缺口比对的确定性用例 + 一份"真实参考 + 模拟 reads"的对拍。

对拍的口径来自 `read_mapping.md` 的分片计划：**从已知参考合成 reads，真值（位置、
链方向、错配数）是已知的**，因此每一条都能逐项断言，而不是"看着差不多就对"。

模拟 read 时替换碱基一律换成**与原来不同**的碱基，否则"换成同一个碱基"会让实际错配数
小于预期，测试会变成假通过。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
    read_genbank,
)
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.read_mapping import (
    DEFAULT_MAX_CLIP,
    Alignment,
    Mapper,
    MappingParams,
    Read,
    ReferenceIndex,
)

_SEED = 20260923
_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _revcomp(text: str) -> str:
    """与实现独立的字符串反向互补，用于构造真值与核对。"""
    return text.translate(_COMPLEMENT)[::-1]


def _random_sequence(length: int, seed: int = _SEED) -> str:
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT") for _ in range(length))


def _substitute(text: str, offsets: dict[int, str]) -> str:
    """按偏移把碱基换成**另一个**碱基（保证真的产生错配）。"""
    characters = list(text)
    for offset, base in offsets.items():
        assert characters[offset] != base
        characters[offset] = base
    return "".join(characters)


def _mapper(
    sequence: str,
    *,
    k: int = 12,
    step: int = 1,
    max_mismatches: int = 4,
    max_clip: int = DEFAULT_MAX_CLIP,
    seq_id: str = "chr",
) -> Mapper:
    reference = ReferenceSet.of([ReferenceSequence(seq_id=seq_id, sequence=sequence)])
    index = ReferenceIndex.build(reference, k=k, step=step)
    return Mapper(
        reference=reference,
        index=index,
        params=MappingParams(
            seed_length=k,
            step=step,
            max_mismatches=max_mismatches,
            max_clip=max_clip,
        ),
    )


def _read_from(
    sequence: str,
    start: int,
    length: int = 100,
    *,
    strand: int = 1,
    mismatches: dict[int, str] | None = None,
    name: str = "read",
) -> Read:
    """从参考上"切"出一条 read；负链取反向互补，错配按 read 自身坐标施加。"""
    piece = sequence[start : start + length]
    if strand == -1:
        piece = _revcomp(piece)
    if mismatches:
        piece = _substitute(piece, mismatches)
    return Read(name=name, sequence=piece)


# ---------------------------------------------------------------------------
# 基本定位
# ---------------------------------------------------------------------------


def test_exact_forward_read_is_recovered() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    alignment = mapper.map_read(_read_from(sequence, 500))
    assert alignment is not None
    assert alignment.seq_id == "chr"
    assert alignment.strand == 1
    assert alignment.reference_start == 500
    assert alignment.reference_end == 599
    assert alignment.reference_length == 100
    assert alignment.query_length == 100
    assert alignment.mismatches == 0
    assert alignment.insertions == 0 and alignment.deletions == 0
    assert str(alignment.cigar) == "100M"
    assert alignment.identity == 1.0
    assert alignment.seed_hits > 0
    assert alignment.candidates >= 1


def test_exact_reverse_read_is_recovered() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    read = _read_from(sequence, 500, strand=-1)
    assert read.sequence == _revcomp(sequence[500:600])
    alignment = mapper.map_read(read)
    assert alignment is not None
    assert alignment.strand == -1
    assert alignment.reference_start == 500
    assert alignment.mismatches == 0


def test_read_with_mismatches_reports_their_count() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    piece = sequence[500:600]
    offsets = {10: "A" if piece[10] != "A" else "C", 50: "G" if piece[50] != "G" else "T"}
    alignment = mapper.map_read(_read_from(sequence, 500, mismatches=offsets))
    assert alignment is not None
    assert alignment.reference_start == 500
    assert alignment.mismatches == 2
    assert alignment.identity == pytest.approx(0.98)


def test_read_over_the_mismatch_cap_is_unmapped() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence, max_mismatches=4)
    piece = sequence[500:600]
    offsets = {offset: ("A" if piece[offset] != "A" else "C") for offset in (5, 15, 25, 35, 45, 55)}
    assert mapper.map_read(_read_from(sequence, 500, mismatches=offsets)) is None


def test_exact_only_mode_rejects_single_mismatch() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence, max_mismatches=0)
    piece = sequence[500:600]
    offsets = {10: "A" if piece[10] != "A" else "C"}
    assert mapper.map_read(_read_from(sequence, 500, mismatches=offsets)) is None
    assert mapper.map_read(_read_from(sequence, 500)) is not None


def test_foreign_read_is_unmapped() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    foreign = _random_sequence(100, seed=999)
    assert mapper.map_read(Read(name="foreign", sequence=foreign)) is None


def test_read_shorter_than_seed_is_unmapped() -> None:
    sequence = _random_sequence(500)
    mapper = _mapper(sequence, k=12)
    assert mapper.map_read(_read_from(sequence, 100, length=5)) is None


def test_read_with_n_still_maps_but_counts_as_mismatch() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    read = _read_from(sequence, 500)
    broken = f"{read.sequence[:20]}N{read.sequence[21:]}"
    alignment = mapper.map_read(Read(name="with_n", sequence=broken))
    assert alignment is not None
    assert alignment.reference_start == 500
    assert alignment.mismatches >= 1


# ---------------------------------------------------------------------------
# 边界与多重命中
# ---------------------------------------------------------------------------


def test_reads_at_reference_boundaries() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    assert mapper.map_read(_read_from(sequence, 0)).reference_start == 0
    assert mapper.map_read(_read_from(sequence, len(sequence) - 100)).reference_start == len(
        sequence
    ) - 100


def test_read_through_prefix_is_clipped_and_start_is_recovered() -> None:
    """末端读通：开头 5 个参考上没有的碱基被剪掉，起点回到真正对得上的位置。

    这是分片 D 与分片 C 的关键差别：不剪裁时那 5 个碱基只能被吸收，起点会被拖着左移；
    打开末端处理之后，剪裁**参与**比对目标，于是得到"5S + 95M、起点 1905"这个正确答案。
    """
    sequence = _random_sequence(2000)
    read = Read(
        name="read_through",
        sequence=_random_sequence(5, seed=13) + sequence[-95:],
    )
    mapper = _mapper(sequence, max_mismatches=4)
    alignment = mapper.map_read(read)
    assert alignment is not None
    assert alignment.reference_start == len(sequence) - 95
    assert alignment.reference_length == 95
    assert alignment.soft_clipped == 5
    assert str(alignment.cigar) == "5S95M"
    assert alignment.mismatches == 0
    assert alignment.identity == 1.0

    # 关掉末端处理（max_clip=0）时只能被吸收：起点会被拖着左移，且一定带错配。
    absorbed = _mapper(sequence, max_mismatches=4, max_clip=0).map_read(read)
    assert absorbed is not None
    assert absorbed.soft_clipped == 0
    assert absorbed.reference_start < len(sequence) - 95


def test_duplicated_region_reports_multiple_candidates() -> None:
    base = _random_sequence(2000)
    sequence = base[:600] + base[100:300] + base[600:]
    mapper = _mapper(sequence)
    alignment = mapper.map_read(_read_from(sequence, 100, length=100))
    assert alignment is not None
    assert alignment.candidates >= 2
    assert alignment.reference_start == 100  # 同分时取更靠前的位置，结果稳定


def test_best_alignment_wins_across_strands() -> None:
    """正向有 3 个错配、负向完全匹配时，必须挑负向那条。"""
    base = _random_sequence(2000)
    read = _read_from(base, 500, strand=1)
    piece = base[500:600]
    offsets = {10: "A" if piece[10] != "A" else "C", 40: "G" if piece[40] != "G" else "T", 70: "T" if piece[70] != "T" else "A"}
    read = _read_from(base, 500, mismatches=offsets)
    sequence = base[:1500] + _revcomp(read.sequence) + base[1500:]
    mapper = _mapper(sequence)
    alignment = mapper.map_read(read)
    assert alignment is not None
    assert alignment.strand == -1
    assert alignment.reference_start == 1500
    assert alignment.mismatches == 0


# ---------------------------------------------------------------------------
# 映射质量（分片 E）
# ---------------------------------------------------------------------------


def test_unique_placement_reports_high_mapq() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    alignment = mapper.map_read(_read_from(sequence, 700))
    assert alignment is not None
    assert alignment.mapq == 60
    assert alignment.is_unique is True


def test_duplicated_region_reports_zero_mapq() -> None:
    """两处一样好：不是"唯一命中"，映射质量必须给 0。"""
    base = _random_sequence(2000)
    sequence = base[:600] + base[100:300] + base[600:]
    mapper = _mapper(sequence)
    alignment = mapper.map_read(_read_from(sequence, 100, length=100))
    assert alignment is not None
    assert alignment.mapq == 0
    assert alignment.is_unique is False
    assert alignment.candidates >= 2


def test_near_duplicate_reports_intermediate_mapq() -> None:
    """另有一处可行但更差（多一个错配）：给中间档，而不是"唯一"或"无法区分"。"""
    base = _random_sequence(2000)
    piece = base[500:600]
    copy = piece[:50] + ("A" if piece[50] != "A" else "C") + piece[51:]
    sequence = base[:1200] + copy + base[1200:]
    mapper = _mapper(sequence)
    alignment = mapper.map_read(_read_from(sequence, 500, length=100))
    assert alignment is not None
    assert alignment.reference_start == 500
    assert alignment.mismatches == 0
    assert alignment.mapq == 20
    assert alignment.is_unique is False


# ---------------------------------------------------------------------------
# 有缺口比对（分片 C）
# ---------------------------------------------------------------------------


def test_read_with_extra_base_is_aligned_with_insertion() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    piece = sequence[500:600]
    read = Read(name="ins", sequence=piece[:50] + "A" + piece[50:])
    alignment = mapper.map_read(read)
    assert alignment is not None
    assert alignment.reference_start == 500
    assert alignment.insertions == 1
    assert alignment.deletions == 0
    assert alignment.mismatches == 0
    assert alignment.query_length == 101
    assert alignment.reference_length == 100
    assert str(alignment.cigar) == "50M1I50M"
    assert alignment.is_gapped is True


def test_read_missing_a_base_is_aligned_with_deletion() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    piece = sequence[500:600]
    read = Read(name="del", sequence=piece[:50] + piece[51:])
    alignment = mapper.map_read(read)
    assert alignment is not None
    assert alignment.reference_start == 500
    assert alignment.deletions == 1
    assert alignment.insertions == 0
    assert alignment.mismatches == 0
    assert alignment.query_length == 99
    assert alignment.reference_length == 100
    assert str(alignment.cigar) == "50M1D49M"


def test_gap_at_the_read_start_just_shifts_the_start() -> None:
    """"缺失"正好落在 read 最开头时，等价于起点后移一位、比对本身没有缺口。"""
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    alignment = mapper.map_read(Read(name="shift", sequence=sequence[501:601]))
    assert alignment is not None
    assert alignment.reference_start == 501
    assert alignment.deletions == 0
    assert alignment.insertions == 0
    assert str(alignment.cigar) == "100M"


def test_indel_beyond_the_band_is_rejected() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    piece = sequence[500:600]
    read = Read(name="ins4", sequence=piece[:50] + "ACGT" + piece[50:])
    assert mapper.map_read(read) is None


def test_adapter_read_through_is_soft_clipped() -> None:
    """接头读通：read 开头那段"参考上没有"的碱基被软剪裁，起点回到真正对得上的位置。

    前缀是**逐个挑出来**的（每个都与参考对应位置不同），所以那一段无论记成错配
    还是插入，代价都一样；打开末端处理后必须被剪掉——这正是分片 D 要的效果。
    """
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    start = 500
    piece = sequence[start : start + 100]
    prefix = "".join(
        next(base for base in "ACGT" if base != sequence[start - 8 + offset])
        for offset in range(8)
    )
    alignment = mapper.map_read(Read(name="read_through", sequence=prefix + piece))
    assert alignment is not None
    assert alignment.reference_start == start  # 找回真来源，而不是被前缀拖着左移
    assert alignment.reference_length == 100
    assert alignment.soft_clipped == 8
    assert str(alignment.cigar) == "8S100M"
    assert alignment.mismatches == 0
    assert alignment.aligned_length == 100
    assert alignment.identity == 1.0


def test_soft_clipping_can_be_turned_off() -> None:
    """关掉 ``max_clip`` 后，开头那 8 个"参考上没有"的碱基就没法安放了。

    没开剪裁时 read 必须被完整消费：那 8 个碱基只能记成错配（超过错配上限）或插入
    （超过 indel 上限），两条路都被上限拦下，于是整条 read **比不上**。
    这正是软剪裁存在的意义——对照之下，打开剪裁后它得到 ``8S100M``、起点回到真来源。
    """
    sequence = _random_sequence(2000)
    read_sequence = (
        "".join(
            next(base for base in "ACGT" if base != sequence[500 - 8 + offset])
            for offset in range(8)
        )
        + sequence[500:600]
    )
    with_clip = _mapper(sequence).map_read(Read(name="a", sequence=read_sequence))
    assert with_clip is not None
    assert with_clip.soft_clipped == 8
    assert with_clip.reference_start == 500
    assert str(with_clip.cigar) == "8S100M"

    without_clip = _mapper(sequence, max_clip=0).map_read(
        Read(name="a", sequence=read_sequence)
    )
    assert without_clip is None


# ---------------------------------------------------------------------------
# 契约与确定性
# ---------------------------------------------------------------------------


def test_map_reads_preserves_order_and_placeholders() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    reads = [
        _read_from(sequence, 300, name="a"),
        Read(name="b", sequence=_random_sequence(100, seed=4242)),
        _read_from(sequence, 1200, name="c", strand=-1),
    ]
    results = list(mapper.map_reads(reads))
    assert len(results) == 3
    assert results[0] is not None and results[0].query_name == "a"
    assert results[1] is None
    assert results[2] is not None and results[2].query_name == "c"


def test_mapping_is_repeatable() -> None:
    sequence = _random_sequence(2000)
    mapper = _mapper(sequence)
    read = _read_from(sequence, 700)
    assert mapper.map_read(read) == mapper.map_read(read)


def test_mapper_rejects_mismatched_index_parameters() -> None:
    sequence = _random_sequence(2000)
    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence=sequence)])
    index = ReferenceIndex.build(reference, k=12, step=1)
    with pytest.raises(ValueError, match="不一致"):
        Mapper(reference=reference, index=index, params=MappingParams(seed_length=16, step=1))


def test_mapper_rejects_index_for_another_reference() -> None:
    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence=_random_sequence(2000))])
    other = ReferenceSet.of(
        [ReferenceSequence(seq_id="other", sequence=_random_sequence(2000, seed=7))]
    )
    index = ReferenceIndex.build(other, k=12, step=1)
    with pytest.raises((KeyError, ValueError)):
        Mapper(reference=reference, index=index, params=MappingParams(seed_length=12, step=1))


def test_read_validation() -> None:
    with pytest.raises(ValueError):
        Read(name="", sequence="ACGT")
    with pytest.raises(ValueError):
        Read(name="x", sequence="")
    with pytest.raises(ValueError):
        Read(name="x", sequence="acgt")
    with pytest.raises(ValueError):
        Read(name="x", sequence="AC GT")
    with pytest.raises(ValueError):
        Read(name="x", sequence="ACGT", qualities="III")


def test_mapping_params_validation() -> None:
    with pytest.raises(ValueError):
        MappingParams(seed_length=0)
    with pytest.raises(ValueError):
        MappingParams(step=0)
    with pytest.raises(ValueError):
        MappingParams(max_mismatches=-1)
    with pytest.raises(ValueError):
        MappingParams(max_candidates=0)


def test_alignment_identity_handles_zero_length() -> None:
    from modules.bio_analysis_function.common.alignment_io import Cigar

    alignment = Alignment(
        query_name="x",
        seq_id="chr",
        strand=1,
        reference_start=0,
        reference_length=0,
        query_length=0,
        mismatches=0,
        insertions=0,
        deletions=0,
        cigar=Cigar(),
        seed_hits=0,
        candidates=0,
    )
    assert alignment.identity == 0.0
    assert alignment.edit_distance == 0
    assert alignment.is_gapped is False


# ---------------------------------------------------------------------------
# 真实参考 + 模拟 reads 对拍
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_simulated_reads_on_real_reference_are_recovered_exactly() -> None:
    """phiX174 真实参考 + 200 条模拟 read（正负链各半、错配 0~2），逐条核对真值。"""
    reference = read_genbank(_REAL_GENBANK)
    target = reference.get("NC_001422")
    sequence = target.sequence
    index = ReferenceIndex.build(reference, k=16, step=4)
    mapper = Mapper(
        reference=reference,
        index=index,
        params=MappingParams(seed_length=16, step=4, max_mismatches=4),
    )

    read_length = 100
    reads: list[Read] = []
    truth: list[tuple[str, int, int, int]] = []
    for order in range(200):
        start = (order * 26) % (len(sequence) - read_length - 1)
        strand = 1 if order % 2 == 0 else -1
        count = order % 3
        piece = sequence[start : start + read_length]
        if strand == -1:
            piece = _revcomp(piece)
        offsets: dict[int, str] = {}
        for step in range(count):
            offset = 10 + step * 30
            offsets[offset] = "A" if piece[offset] != "A" else "C"
        if offsets:
            piece = _substitute(piece, offsets)
        name = f"sim-{order}"
        reads.append(Read(name=name, sequence=piece, is_read2=order % 4 == 1))
        truth.append((name, start, strand, count))

    recovered = 0
    for read, (name, start, strand, count) in zip(reads, truth):
        alignment = mapper.map_read(read)
        assert alignment is not None, f"{name} 没比对"
        assert alignment.query_name == name
        assert alignment.seq_id == "NC_001422"
        assert alignment.reference_start == start, f"{name} 起点错"
        assert alignment.strand == strand, f"{name} 链方向错"
        assert alignment.mismatches == count, f"{name} 错配数错"
        assert alignment.is_read2 == (read.is_read2)
        recovered += 1
    assert recovered == len(reads)
