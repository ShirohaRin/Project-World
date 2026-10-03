"""缺失区检测的定点用例：种子要求、向两侧扩展、边界取值、从 SAM 到缺失区的端到端。"""

from __future__ import annotations

from modules.bio_analysis_function.common.alignment_io import SamRecord, parse_cigar
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.missing_coverage import (
    CoverageProfile,
    MissingCoverageSettings,
    analyze_missing_coverage,
    find_missing_coverage,
)


def _profile(*depths: int) -> CoverageProfile:
    return CoverageProfile(seq_id="chr", depths=depths)


def _record(name: str, position: int, cigar: str, *, length: int) -> SamRecord:
    return SamRecord(
        query_name=name,
        flag=0,
        reference_name="chr",
        position=position,
        mapping_quality=60,
        cigar=parse_cigar(cigar),
        next_reference_name="*",
        next_position=0,
        template_length=0,
        sequence="A" * length,
        qualities="I" * length,
    )


# ---------------------------------------------------------------------------
# 种子与扩展
# ---------------------------------------------------------------------------


def test_a_zero_run_inside_normal_coverage_is_reported_with_its_margins() -> None:
    profile = _profile(*([100] * 20 + [0] * 5 + [100] * 25))
    analysis = find_missing_coverage(profile)
    assert analysis.distribution is not None
    assert analysis.cutoff is not None
    assert 0 <= analysis.cutoff < 100

    assert len(analysis.regions) == 1
    region = analysis.regions[0]
    assert (region.seq_id, region.start, region.end, region.length) == ("chr", 20, 24, 5)
    assert (region.left_outside_cov, region.left_inside_cov) == (100, 0)
    assert (region.right_inside_cov, region.right_outside_cov) == (0, 100)


def test_low_but_nonzero_coverage_without_a_seed_is_not_reported() -> None:
    # 中间 4 个位置只有 1× 覆盖：低于阈值，但没有"一条 read 都没有"的种子 → 不报
    profile = _profile(*([100] * 10 + [1] * 4 + [100] * 10))
    analysis = find_missing_coverage(profile)
    assert analysis.cutoff is not None and analysis.cutoff >= 1
    assert analysis.regions == ()


def test_extension_swallows_neighbouring_low_coverage() -> None:
    profile = _profile(*([100] * 10 + [0] * 3 + [1] * 4 + [100] * 10))
    analysis = find_missing_coverage(profile)
    assert analysis.cutoff is not None and analysis.cutoff >= 1

    assert len(analysis.regions) == 1
    region = analysis.regions[0]
    assert (region.start, region.end, region.length) == (10, 16, 7)
    assert (region.left_outside_cov, region.left_inside_cov) == (100, 0)
    assert (region.right_inside_cov, region.right_outside_cov) == (1, 100)


def test_regions_touching_the_ends_have_no_outside_margin() -> None:
    left = find_missing_coverage(_profile(*([0] * 5 + [100] * 20))).regions
    assert len(left) == 1
    assert (left[0].start, left[0].end) == (0, 4)
    assert left[0].left_outside_cov is None
    assert left[0].right_outside_cov == 100

    right = find_missing_coverage(_profile(*([100] * 20 + [0] * 5))).regions
    assert len(right) == 1
    assert (right[0].start, right[0].end) == (20, 24)
    assert right[0].left_outside_cov == 100
    assert right[0].right_outside_cov is None


def test_a_sequence_without_any_coverage_is_not_analyzed() -> None:
    analysis = find_missing_coverage(_profile(*([0] * 100)))
    assert analysis.distribution is None
    assert analysis.cutoff is None
    assert analysis.regions == ()


# ---------------------------------------------------------------------------
# 从 SAM 到缺失区
# ---------------------------------------------------------------------------


def _tiling_reads(reference_length: int, window: tuple[int, int], read_length: int = 6):
    """用整齐铺满的 read 造出"除 window 外处处均匀覆盖"的读集（0-based 半开区间）。"""
    start, end = window
    records = []
    for position in range(reference_length - read_length + 1):
        if position <= end and position + read_length > start:
            continue  # 这条 read 会盖住窗口，跳过
        records.append(
            _record(
                f"read-{position}",
                position + 1,  # SAM 的 POS 是 1-based
                f"{read_length}M",
                length=read_length,
            )
        )
    return records


def test_end_to_end_finds_the_removed_window() -> None:
    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence="ACGT" * 15)])
    records = _tiling_reads(reference_length=60, window=(24, 30))

    analyses = list(analyze_missing_coverage(reference, records))
    assert len(analyses) == 1
    analysis = analyses[0]
    assert analysis.seq_id == "chr"

    # 覆盖剖面上的缺口比 window 宽一位：6 bp 的 read 里，起点 18 的那条盖到 23 为止、
    # 起点 31 的那条从 31 开始，于是 24..30 这 7 个位置一条 read 都没有。
    # 两侧紧邻位置的深度都只有 1（只有那一条 read 的边缘盖到了）。
    assert analysis.cutoff == 0
    assert [region.length for region in analysis.regions] == [7]
    region = analysis.regions[0]
    assert (region.start, region.end) == (24, 30)
    assert (region.left_outside_cov, region.left_inside_cov) == (1, 0)
    assert (region.right_inside_cov, region.right_outside_cov) == (0, 1)


def test_settings_control_the_coverage_filter() -> None:
    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence="ACGT" * 15)])
    # 把所有 read 改成 MAPQ 0（多命中）：默认口径下它们不计入覆盖，整条序列都没有覆盖
    records = [
        SamRecord(
            query_name=record.query_name,
            flag=0,
            reference_name=record.reference_name,
            position=record.position,
            mapping_quality=0,
            cigar=record.cigar,
            next_reference_name="*",
            next_position=0,
            template_length=0,
            sequence=record.sequence,
            qualities=record.qualities,
        )
        for record in _tiling_reads(reference_length=60, window=(24, 30))
    ]
    strict = list(analyze_missing_coverage(reference, records))
    loose = list(
        analyze_missing_coverage(
            reference, records, settings=MissingCoverageSettings(min_mapping_quality=0)
        )
    )
    # 默认口径：一条 unique read 都没有 → 分布无从拟合
    assert strict[0].distribution is None
    # 放开过滤后又看到了平整覆盖，窗口仍是唯一缺失区
    assert [region.length for region in loose[0].regions] == [7]
