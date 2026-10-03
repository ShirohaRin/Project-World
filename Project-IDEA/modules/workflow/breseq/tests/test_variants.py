"""证据 → 突变的规则层用例：坐标换算、去重排序、两种 `.gd` 行的写法。"""

from __future__ import annotations

from modules.bio_analysis_function.common.genome_diff import (
    assemble_diff,
    format_genome_diff,
)
from modules.bio_analysis_function.submodules.consensus_calling import (
    ConsensusCall,
    PolymorphismCall,
)
from modules.workflow.breseq import (
    Variant,
    merge_variants,
    mutation_entries,
    to_diff_record,
    to_evidence_record,
    variants_from_calls,
)


def _consensus(position: int = 149, call_base: str = "G") -> ConsensusCall:
    return ConsensusCall(
        seq_id="chr",
        position=position,
        reference_base="A",
        call_base=call_base,
        likelihood_ratio=14.0,
        consensus_score=11.5,
        frequency=1.0,
        total_reads=30,
        variant_reads=30,
    )


def _polymorphism(position: int = 199, call_base: str = "T") -> PolymorphismCall:
    return PolymorphismCall(
        seq_id="chr",
        position=position,
        reference_base="C",
        call_base=call_base,
        frequency=0.4,
        score=6.7,
        likelihood_ratio=9.2,
        total_reads=20,
        variant_reads=8,
    )


# ---------------------------------------------------------------------------
# 调用 → 突变
# ---------------------------------------------------------------------------


def test_consensus_call_becomes_a_one_based_variant() -> None:
    (variant,) = variants_from_calls([_consensus(position=149)])
    assert (variant.type, variant.seq_id, variant.position) == ("SNP", "chr", 150)
    assert (variant.reference_base, variant.call_base) == ("A", "G")
    assert variant.prediction == "consensus"
    # 共识档的打分就是上游那个 consensus_score
    assert variant.score == 11.5
    assert variant.frequency == 1.0


def test_polymorphism_call_keeps_its_own_score() -> None:
    (variant,) = variants_from_calls([_polymorphism(position=199)])
    assert (variant.position, variant.prediction) == (200, "polymorphism")
    assert variant.score == 6.7  # 混合模型打分（不是上游的 polymorphism_score）


def test_variants_are_sorted_and_deduplicated() -> None:
    first = variants_from_calls([_consensus(position=149)])
    second = variants_from_calls([_polymorphism(position=199)])
    merged = merge_variants(second, first, first)
    assert [(item.seq_id, item.position) for item in merged] == [("chr", 150), ("chr", 200)]


def test_same_position_different_base_is_a_different_variant() -> None:
    left = Variant("SNP", "chr", 10, "A", "G", 1.0, "consensus", 12.0)
    right = Variant("SNP", "chr", 10, "A", "T", 0.9, "consensus", 11.0)
    assert len(merge_variants([left, right])) == 2


# ---------------------------------------------------------------------------
# 突变 → `.gd`
# ---------------------------------------------------------------------------


def test_consensus_evidence_row_carries_the_upstream_consensus_score() -> None:
    (variant,) = variants_from_calls([_consensus()])
    record = to_evidence_record(variant)
    assert record.type == "RA"
    assert record.position == 150
    assert dict(record.attributes)["prediction"] == "consensus"
    assert dict(record.attributes)["consensus_score"] == "11.5"
    assert dict(record.attributes)["frequency"] == "1"


def test_polymorphism_evidence_row_uses_our_own_score_key() -> None:
    (variant,) = variants_from_calls([_polymorphism()])
    attributes = dict(to_evidence_record(variant).attributes)
    assert attributes["prediction"] == "polymorphism"
    assert attributes["mixed_model_score"] == "6.7"
    # 不去占用上游那个键名：我们没复刻它的统计检验
    assert "consensus_score" not in attributes
    assert "polymorphism_score" not in attributes


def test_diff_record_and_assembly_wire_the_evidence() -> None:
    variants = variants_from_calls([_consensus(), _polymorphism()])
    entries = mutation_entries(
        variants, attributes_of=lambda variant: (("annotation", "geneA missense"),)
    )
    text = format_genome_diff(assemble_diff(entries))
    lines = text.splitlines()
    # 突变 1..N、证据 N+1..M，先突变后证据
    assert lines[0] == "#=GENOME_DIFF\t1.0"
    assert lines[1].startswith("SNP\t1\t3\tchr\t150\tG")
    assert "annotation=geneA missense" in lines[1]
    assert lines[2].startswith("SNP\t2\t4\tchr\t200\tT")
    assert lines[3].startswith("RA\t3\t.\tchr\t150")
    assert lines[4].startswith("RA\t4\t.\tchr\t200")


def test_deletion_record_needs_a_length() -> None:
    short = Variant("DEL", "chr", 10, ".", ".", 1.0, "consensus", 9.0)
    try:
        to_diff_record(short)
    except ValueError as error:
        assert "缺失长度" in str(error)
    else:  # pragma: no cover - 上面必须抛
        raise AssertionError("长度缺失时必须报错")

    sized = Variant("DEL", "chr", 10, ".", ".", 1.0, "consensus", 9.0, length=120)
    assert to_diff_record(sized).fields == ("120",)
