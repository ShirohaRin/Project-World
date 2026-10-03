"""端到端注释表的定点用例 + 真实参考全位置扫描。

合成参考的每一段都能手算（两个重叠 CDS、一个 tRNA、一个不参与边界的 misc_feature），
真实数据那组则把 phiX174 的**每个位置**都过一遍，检查分类自洽、并核对几项由数据本身
决定的统计量（编码密度、重叠位置数）——这些数字不依赖任何外部资料，只依赖参考文件自己的
特征表。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    Feature,
    Location,
    Part,
    ReferenceSequence,
    ReferenceSet,
    read_genbank,
)
from modules.bio_analysis_function.submodules.mutation_annotation import (
    ANNOTATION_COLUMNS,
    Substitution,
    annotate_variant,
    annotate_variants,
    iter_annotation_rows,
    write_annotation_table,
)

_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)
_SEQ_ID = "chr"


def _cds(start: int, end: int, *, strand: int = 1, **qualifiers: str) -> Feature:
    return Feature(
        kind="CDS",
        location=Location(parts=(Part(start, end, strand=strand),), operator="single"),
        qualifiers={key: (value,) for key, value in qualifiers.items()},
    )


def _build_sequence(length: int, pieces: dict[int, str]) -> str:
    """按 ``ACGT`` 周期铺底，再把 ``pieces``（1-based 起点 → 片段）盖上去。"""
    chars = list("ACGT" * (length // 4) + "ACGT"[: length % 4])
    for start, piece in pieces.items():
        for offset, base in enumerate(piece):
            chars[start - 1 + offset] = base
    return "".join(chars)


#: 80 bp 的合成参考：
#:   alpha CDS 10..18 = ATG GCT ATG（第三位被 beta 的起始密码子共用）
#:   beta  CDS 16..24 = ATG AAA TTT（与 alpha 在 16..18 重叠）
#:   tRNA      40..47 = GGGG CCCC
#:   misc_feature 60..64（默认不算"基因"，不参与基因间边界）
_SEQUENCE = _build_sequence(
    80,
    {10: "ATGGCT", 16: "ATGAAATTT", 40: "GGGGCCCC", 60: "TTTTT"},
)
_REFERENCE = ReferenceSequence(
    seq_id=_SEQ_ID,
    sequence=_SEQUENCE,
    features=(
        _cds(10, 18, gene="alpha", product="alpha protein"),
        _cds(16, 24, gene="beta", product="beta protein"),
        Feature(
            kind="tRNA",
            location=Location(parts=(Part(40, 47, strand=1),), operator="single"),
            qualifiers={"gene": ("trnaA",)},
        ),
        Feature(
            kind="misc_feature",
            location=Location(parts=(Part(60, 64, strand=1),), operator="single"),
        ),
    ),
)
_REFERENCES = ReferenceSet.of((_REFERENCE,))


def _substitution(position: int, call_base: str, *, seq_id: str = _SEQ_ID) -> Substitution:
    """按参考序列里真实的碱基构造替换（参考碱基不用手填，避免抄错）。"""
    return Substitution(seq_id, position, _SEQUENCE[position - 1], call_base)


# ---------------------------------------------------------------------------
# 四类分类口径
# ---------------------------------------------------------------------------


def test_coding_variant_inside_a_single_gene() -> None:
    # 位置 12 是 alpha 起始密码子的第 3 位（G）→ 换成 A：ATG → ATA
    annotation = annotate_variant(_REFERENCES, _substitution(12, "A"))

    assert annotation.kind == "coding"
    assert annotation.genes == ("alpha",)
    assert annotation.effect == "start_codon_change"
    assert annotation.description == "M1M (ATG→ATA)"
    assert len(annotation.coding_effects) == 1
    assert annotation.coding_effects[0].product == "alpha protein"


def test_coding_variant_in_overlapping_genes_gives_one_effect_per_gene() -> None:
    # 位置 17（参考碱基 T）在两个 CDS 里意义不同：对 alpha 是第三个密码子的中位、
    # 对 beta 是起始密码子的中位。同一个碱基，一个错义、一个起始子丢失。
    annotation = annotate_variant(_REFERENCES, _substitution(17, "C"))

    assert annotation.kind == "coding"
    assert annotation.genes == ("alpha", "beta")
    effects = {effect.gene: effect for effect in annotation.coding_effects}
    assert effects["alpha"].reference_codon == "ATG"
    assert effects["alpha"].call_codon == "ACG"
    assert effects["alpha"].effect == "nonsynonymous"
    assert effects["alpha"].description == "M3T (ATG→ACG)"
    assert effects["beta"].reference_codon == "ATG"
    assert effects["beta"].call_codon == "ACG"
    assert effects["beta"].effect == "start_lost"
    assert effects["beta"].description == "M1T (ATG→ACG)"
    # 主列取第一条（alpha 在文件里靠前）
    assert annotation.description == "M3T (ATG→ACG)"


def test_variant_inside_a_non_coding_gene_feature() -> None:
    # tRNA 在 40..47，位置 43 是它取出的序列里的第 4 位
    annotation = annotate_variant(_REFERENCES, _substitution(43, "A"))

    assert annotation.kind == "noncoding"
    assert annotation.noncoding is not None
    assert annotation.noncoding.feature_kind == "tRNA"
    assert annotation.noncoding.gene == "trnaA"
    assert (annotation.noncoding.nucleotide_index, annotation.noncoding.length) == (4, 8)
    assert annotation.description == "noncoding (4/8 nt)"
    assert annotation.genes == ("trnaA",)


def test_variant_between_two_genes() -> None:
    # 位置 30 夹在 beta（结束于 24）与 tRNA（起始于 40）之间
    annotation = annotate_variant(_REFERENCES, _substitution(30, "A"))

    assert annotation.kind == "intergenic"
    assert annotation.intergenic is not None
    assert (annotation.intergenic.left_gene, annotation.intergenic.right_gene) == (
        "beta",
        "trnaA",
    )
    assert (annotation.intergenic.left_distance, annotation.intergenic.right_distance) == (6, 10)
    assert annotation.description == "intergenic (+6/-10)"
    assert annotation.genes == ("beta", "trnaA")


def test_misc_feature_does_not_split_the_intergenic_region() -> None:
    # 位置 62 落在 misc_feature(60..64) 里，但默认它不算"基因"，所以仍算基因间
    annotation = annotate_variant(_REFERENCES, _substitution(62, "A"))

    assert annotation.kind == "intergenic"
    assert annotation.intergenic is not None
    assert annotation.intergenic.left_gene == "trnaA"
    assert annotation.intergenic.right_gene is None
    assert annotation.description == "intergenic (+15/.)"


def test_narrowing_kinds_turns_a_trna_position_into_intergenic() -> None:
    """把 tRNA 排除出"基因"之后，同一个位置就从 noncoding 变成基因间。"""
    annotation = annotate_variant(_REFERENCES, _substitution(43, "A"), kinds=("CDS",))

    assert annotation.kind == "intergenic"
    assert annotation.description == "intergenic (+19/.)"


def test_position_on_a_reference_without_any_annotation() -> None:
    plain = ReferenceSequence(seq_id=_SEQ_ID, sequence="ACGT" * 20)
    annotation = annotate_variant(ReferenceSet.of((plain,)), Substitution(_SEQ_ID, 7, "G", "A"))

    assert annotation.kind == "unannotated"
    assert annotation.description == "unannotated"
    assert annotation.genes == ()


def test_unknown_sequence_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="没有 SEQ_ID"):
        annotate_variant(_REFERENCES, Substitution("nope", 7, "G", "A"))


# ---------------------------------------------------------------------------
# 表格展开与写出
# ---------------------------------------------------------------------------


def _five_variants() -> list[Substitution]:
    return [
        _substitution(12, "A"),  # coding（alpha，1 条效应）
        _substitution(17, "C"),  # coding（alpha + beta，2 条效应）
        _substitution(43, "A"),  # noncoding（tRNA）
        _substitution(30, "A"),  # intergenic（两侧都有）
        _substitution(62, "A"),  # intergenic（只有左侧）
    ]


def test_batch_annotation_keeps_the_input_order() -> None:
    annotations = annotate_variants(_REFERENCES, _five_variants())

    assert [item.position for item in annotations] == [12, 17, 43, 30, 62]
    assert [item.kind for item in annotations] == [
        "coding",
        "coding",
        "noncoding",
        "intergenic",
        "intergenic",
    ]


def test_rows_expand_one_line_per_effect() -> None:
    annotations = annotate_variants(_REFERENCES, _five_variants())
    rows = list(iter_annotation_rows(annotations))

    assert len(rows) == 6  # 1 + 2 + 1 + 1 + 1
    assert [row["description"] for row in rows] == [
        "M1M (ATG→ATA)",
        "M3T (ATG→ACG)",
        "M1T (ATG→ACG)",
        "noncoding (4/8 nt)",
        "intergenic (+6/-10)",
        "intergenic (+15/.)",
    ]
    assert [row["gene"] for row in rows] == [
        "alpha",
        "alpha",
        "beta",
        "trnaA",
        "beta/trnaA",
        "trnaA",
    ]
    assert all(set(row) == set(ANNOTATION_COLUMNS) for row in rows)


def test_write_annotation_table_round_trips(tmp_path: Path) -> None:
    annotations = annotate_variants(_REFERENCES, _five_variants())
    target = tmp_path / "nested" / "annotations.tsv"

    assert write_annotation_table(target, annotations) == 6
    lines = target.read_text(encoding="utf-8").splitlines()

    assert lines[0].split("\t") == list(ANNOTATION_COLUMNS)
    assert len(lines) == 7
    cells = {tuple(line.split("\t")) for line in lines[1:]}
    assert len(cells) == 6  # 每行都不一样
    header = list(ANNOTATION_COLUMNS)
    row = next(line.split("\t") for line in lines[1:] if line.split("\t")[1] == "17")
    assert row[header.index("description")] == "M3T (ATG→ACG)"
    assert row[header.index("kind")] == "coding"


# ---------------------------------------------------------------------------
# 真实参考：全位置扫描
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_real_phix174_every_position_is_classified_consistently() -> None:
    """phiX174 的每个位置都过一遍：分类自洽、描述非空，并核对两项统计量。

    统计量由参考文件自己的特征表决定（编码密度、重叠位置数），不依赖外部资料。
    """
    references = read_genbank(_REAL_GENBANK)
    target = references.get("NC_001422")

    substitutions = []
    for position in range(1, target.length + 1):
        reference_base = target.sequence[position - 1]
        if reference_base not in "ACGT":
            continue
        call_base = next(base for base in "ACGT" if base != reference_base)
        substitutions.append(Substitution(target.seq_id, position, reference_base, call_base))

    annotations = annotate_variants(references, substitutions)

    assert len(annotations) == target.length  # 每个位置都有分类，没有漏网的
    assert all(item.description for item in annotations)
    for item in annotations:
        assert item.seq_id == target.seq_id
        if item.kind == "coding":
            assert item.coding_effects, "coding 却没有效应"
            assert all(effect.description for effect in item.coding_effects)
        elif item.kind == "intergenic":
            assert not item.coding_effects
            assert item.intergenic is not None
            assert item.description.startswith("intergenic (")
        elif item.kind == "noncoding":
            assert item.noncoding is not None and not item.coding_effects
        else:
            assert item.kind == "unannotated"

    counts = Counter(item.kind for item in annotations)
    # phiX174 是出了名的"基因挤在一起"：绝大多数位置都是编码区，重叠基因很常见
    assert counts["coding"] / target.length > 0.95
    assert counts["intergenic"] > 0
    multi_gene = sum(1 for item in annotations if len(item.coding_effects) > 1)
    assert multi_gene > 1000
