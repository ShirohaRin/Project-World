"""breseq 工作流的端到端用例：合成参考 + 合成 reads → SAM / `.gd` / 注释表 / 摘要表。

数据全部现造（不落盘外部数据）：参考是固定种子的 300 bp 伪随机序列，读段是整齐铺开的 30 bp
read，在**一个位置上**全部改成另一个碱基——于是"应该只报这一条替换"是可以手写期望值的。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.genome_diff import read_genome_diff
from modules.workflow.breseq import BreseqConfig, load_reference, run_breseq_workflow

_SEED = 20260929
_LENGTH = 300
_READ_LENGTH = 30
#: 被改掉的那个位置（0-based）——所有盖住它的 read 都带变异碱基，于是频率是 1.0。
_VARIANT_POSITION = 150
_SWAP = {"A": "C", "C": "A", "G": "T", "T": "G"}


def _sequence() -> str:
    """固定种子的伪随机序列：前缀是起始密码子、结尾是终止密码子，便于注释落成 coding。"""
    generator = random.Random(_SEED)
    middle = "".join(generator.choice("ACGT") for _ in range(_LENGTH - 6))
    return "ATG" + middle + "TAA"


def _origin_block(sequence: str, width: int = 60) -> str:
    return "\n".join(
        f"{start + 1:>9} {sequence[start:start + width].lower()}"
        for start in range(0, len(sequence), width)
    )


def _write_reference(path: Path, sequence: str) -> None:
    head = [
        f"LOCUS       {'chr':<22}{len(sequence):>6} bp    DNA     linear   SYN 01-JAN-2026",
        "DEFINITION  synthetic reference for the breseq workflow test.",
        "ACCESSION   chr",
        f"VERSION     chr.1",
        "FEATURES             Location/Qualifiers",
        f"     source          1..{len(sequence)}",
        '                     /organism="synthetic construct"',
        f"     CDS             1..{len(sequence)}",
        '                     /gene="geneA"',
        '                     /product="protein A"',
    ]
    path.write_text(
        "\n".join(head) + f"\nORIGIN\n{_origin_block(sequence)}\n//\n", encoding="utf-8"
    )


def _write_reads(path: Path, sequence: str) -> None:
    """每个起点一条 30 bp 的完全匹配 read；盖住变异位点的那批换成变异碱基。"""
    records = []
    for start in range(0, _LENGTH - _READ_LENGTH + 1):
        bases = list(sequence[start : start + _READ_LENGTH])
        if start <= _VARIANT_POSITION < start + _READ_LENGTH:
            bases[_VARIANT_POSITION - start] = _SWAP[sequence[_VARIANT_POSITION]]
        records.append(f"@read{start}\n{''.join(bases)}\n+\n{'I' * _READ_LENGTH}")
    path.write_text("\n".join(records) + "\n", encoding="utf-8")


def _inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    sequence = _sequence()
    reference_path = tmp_path / "ref.gbk"
    reads_path = tmp_path / "reads.fq"
    _write_reference(reference_path, sequence)
    _write_reads(reads_path, sequence)
    return reference_path, reads_path, sequence


def test_end_to_end_reports_the_injected_substitution(tmp_path: Path) -> None:
    reference_path, reads_path, sequence = _inputs(tmp_path)
    outcome = run_breseq_workflow(
        BreseqConfig(
            reference=(reference_path,),
            read1=reads_path,
            output_dir=tmp_path / "out",
        )
    )

    # 阶段 A：参考与读段都读对了
    assert outcome.scan.seq_ids == ("chr",)
    assert outcome.scan.reference_length == _LENGTH
    assert outcome.scan.feature_count == 2  # source + CDS
    assert outcome.scan.reads == _LENGTH - _READ_LENGTH + 1
    assert outcome.scan.bases == outcome.scan.reads * _READ_LENGTH
    assert (outcome.scan.shortest_read, outcome.scan.longest_read) == (_READ_LENGTH,) * 2
    assert not outcome.scan.cropped
    assert not outcome.scan.read_pairs

    # 阶段 B：只报一条替换。
    # 27 条**带变异的** read 里有 5 条比不上：比对器用 step=4 的种子，那个错配落在 read 中段时
    # 会把可用种子全部打断（起点 133..137）——这是 `read_mapping` 的已知种子行为，钉在这里。
    assert outcome.unmapped_reads == 5
    assert outcome.mapped_reads == outcome.scan.reads - 5
    assert len(outcome.variants) == 1
    variant = outcome.variants[0]
    assert (variant.seq_id, variant.position, variant.type) == ("chr", 151, "SNP")
    assert variant.call_base == _SWAP[sequence[_VARIANT_POSITION]]
    assert variant.reference_base == sequence[_VARIANT_POSITION]
    assert variant.prediction == "consensus"
    assert variant.frequency == pytest.approx(1.0)
    assert outcome.consensus_variants == outcome.variants
    assert outcome.polymorphism_variants == ()
    assert outcome.mapped_fraction == pytest.approx(266 / 271)


def test_end_to_end_writes_gd_annotation_and_summary(tmp_path: Path) -> None:
    reference_path, reads_path, sequence = _inputs(tmp_path)
    config = BreseqConfig(
        reference=(reference_path,), read1=reads_path, output_dir=tmp_path / "out"
    )
    outcome = run_breseq_workflow(config)

    for path in (config.sam_path, config.diff_path, config.annotation_path, config.summary_path):
        assert path.exists(), path
    assert outcome.outputs == (
        config.sam_path,
        config.diff_path,
        config.annotation_path,
        config.summary_path,
    )

    diff = read_genome_diff(config.diff_path)
    assert diff.header.value_of("PROGRAM") is not None
    assert diff.header.value_of("REFSEQ") == str(reference_path)
    assert diff.header.value_of("READSEQ") == str(reads_path)

    mutations = diff.mutations()
    assert len(mutations) == 1
    record = mutations[0]
    assert (record.type, record.position) == ("SNP", 151)
    assert record.fields == (_SWAP[sequence[_VARIANT_POSITION]],)
    assert record.frequency == pytest.approx(1.0)
    # 注释写进了属性（键名由本层定：`annotation`）
    assert "geneA" in (record.attribute("annotation") or "")

    # 突发行引用的那条证据是 RA，且带 consensus_score
    assert len(record.evidence) == 1
    evidence = diff.by_id(record.evidence[0])
    assert evidence is not None
    assert evidence.type == "RA"
    assert evidence.attribute("prediction") == "consensus"
    assert evidence.attribute("consensus_score") is not None

    # 注释表：一个效应一行，表头 + 至少一行
    rows = config.annotation_path.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("seq_id\tposition\t")
    assert len(rows) >= 2
    assert "geneA" in "\n".join(rows[1:])

    # 摘要表：一条突变一行
    summary = config.summary_path.read_text(encoding="utf-8").splitlines()
    assert summary[0].startswith("seq_id\tposition\ttype\t")
    assert len(summary) == 2
    assert summary[1].split("\t")[1] == "151"


def test_max_reads_crops_the_input(tmp_path: Path) -> None:
    reference_path, reads_path, _sequence_text = _inputs(tmp_path)
    outcome = run_breseq_workflow(
        BreseqConfig(
            reference=(reference_path,),
            read1=reads_path,
            output_dir=tmp_path / "out",
            max_reads=50,
        )
    )
    assert outcome.scan.reads == 50
    assert outcome.scan.cropped
    assert outcome.mapped_reads == 50


def test_fasta_reference_is_accepted_and_yields_no_annotation(tmp_path: Path) -> None:
    sequence = _sequence()
    fasta = tmp_path / "ref.fasta"
    fasta.write_text(f">chr\n{sequence}\n", encoding="utf-8")
    reads_path = tmp_path / "reads.fq"
    _write_reads(reads_path, sequence)

    outcome = run_breseq_workflow(
        BreseqConfig(reference=(fasta,), read1=reads_path, output_dir=tmp_path / "out")
    )
    assert outcome.scan.seq_ids == ("chr",)
    assert outcome.scan.feature_count == 0  # FASTA 没有特征表
    assert len(outcome.variants) == 1


def test_unknown_reference_suffix_is_rejected(tmp_path: Path) -> None:
    unknown = tmp_path / "ref.xyz"
    unknown.write_text("nothing useful\n", encoding="utf-8")
    with pytest.raises(ValueError, match="认不出参考文件的格式"):
        load_reference([unknown])


def test_missing_read_file_is_reported(tmp_path: Path) -> None:
    reference_path, _reads_path, _text = _inputs(tmp_path)
    with pytest.raises(FileNotFoundError, match="读段文件不存在"):
        run_breseq_workflow(
            BreseqConfig(
                reference=(reference_path,),
                read1=tmp_path / "nope.fq",
                output_dir=tmp_path / "out",
            )
        )


def test_config_validation(tmp_path: Path) -> None:
    reference = tmp_path / "ref.fa"
    reference.write_text(">chr\nACGT\n", encoding="utf-8")
    reads = tmp_path / "reads.fq"
    reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")

    with pytest.raises(ValueError, match="至少要给一条参考"):
        BreseqConfig(reference=(), read1=reads)
    with pytest.raises(ValueError, match="不能是同一个"):
        BreseqConfig(reference=(reference,), read1=reads, read2=reads)
    with pytest.raises(ValueError, match="读入上限"):
        BreseqConfig(reference=(reference,), read1=reads, max_reads=0)
