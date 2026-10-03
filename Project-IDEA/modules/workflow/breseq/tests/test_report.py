"""报告层用例：自包含、可复现、内容对得上；JSON 能序列化且键齐全。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from modules.workflow.breseq import (
    BRESEQ_LOG_NAME,
    BreseqConfig,
    BreseqOutcome,
    BreseqScanReport,
    Variant,
    build_breseq_json,
    render_breseq_html,
    run_breseq_workflow,
)


def _outcome() -> BreseqOutcome:
    """手工造一个结果对象——报告层只吃 `BreseqOutcome`，与真实数据无关。"""
    config = BreseqConfig(
        reference=(Path("ref.gbk"),),
        read1=Path("reads.fq"),
        output_dir=Path("out"),
    )
    scan = BreseqScanReport(
        seq_ids=("chr",),
        reference_length=300,
        feature_count=2,
        reads=271,
        read_pairs=False,
        shortest_read=30,
        longest_read=30,
        mean_read_length=30.0,
        bases=8130,
    )
    variants = (
        Variant(
            "SNP",
            "chr",
            151,
            "A",
            "G",
            1.0,
            "consensus",
            47.5,
            annotation="geneA missense F5L (TTT→TTG)",
        ),
        Variant("SNP", "chr", 200, "C", "T", 0.4, "polymorphism", 6.7),
    )
    return BreseqOutcome(
        config=config,
        scan=scan,
        mapped_reads=266,
        unmapped_reads=5,
        unique_reads=260,
        repeat_reads=6,
        variants=variants,
        outputs=(Path("out/mapped.sam"), Path("out/output.gd")),
    )


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_html_is_self_contained() -> None:
    html = render_breseq_html(_outcome())
    assert "<script" not in html.lower()
    assert "http://" not in html and "https://" not in html
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_html_is_byte_for_byte_reproducible() -> None:
    assert render_breseq_html(_outcome()) == render_breseq_html(_outcome())


def test_html_lists_the_variants_and_their_annotations() -> None:
    html = render_breseq_html(_outcome())
    assert "151" in html and "A→G" in html
    assert "geneA missense F5L (TTT→TTG)" in html
    assert "共识档（纯合）" in html and "多态档（混合）" in html
    # 概览卡片里数得出来
    assert "比对率" in html and "98.2%" in html


def test_html_without_variants_says_so() -> None:
    empty = _outcome()
    html = render_breseq_html(
        BreseqOutcome(
            config=empty.config,
            scan=empty.scan,
            mapped_reads=0,
            unmapped_reads=0,
            unique_reads=0,
            repeat_reads=0,
            variants=(),
            outputs=(),
        )
    )
    assert "没有检出与参考不一致的突变" in html
    assert "没有突变，画不出频率分布" in html


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_is_serialisable_and_has_the_key_numbers() -> None:
    payload = build_breseq_json(_outcome())
    # 第一要求：能直接 dumps（日志要落盘）——注意元组会被转成数组，这是 JSON 的规矩
    restored = json.loads(json.dumps(payload, ensure_ascii=False))
    assert isinstance(restored["scan"]["seq_ids"], list)

    assert payload["workflow"] == "breseq"
    assert payload["counts"] == {"variants": 2, "consensus": 1, "polymorphism": 1}
    assert payload["mapping"]["mapped"] == 266
    assert payload["scan"]["reference_length"] == 300
    assert payload["settings"]["polymorphism"] is not None
    assert [item["position"] for item in payload["variants"]] == [151, 200]
    assert payload["variants"][0]["annotation"].startswith("geneA")
    assert payload["outputs"] == [str(Path("out/mapped.sam")), str(Path("out/output.gd"))]


# ---------------------------------------------------------------------------
# 写盘与接线
# ---------------------------------------------------------------------------


def _tiny_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """一份能跑通的小输入：120 bp 伪随机参考 + 3 条读段。"""
    generator = random.Random(7)
    sequence = "".join(generator.choice("ACGT") for _ in range(120))
    reference = tmp_path / "ref.fasta"
    reference.write_text(f">chr\n{sequence}\n", encoding="utf-8")
    reads = tmp_path / "reads.fq"
    reads.write_text(
        "".join(f"@r{start}\n{sequence[start:start + 30]}\n+\n{'I' * 30}\n" for start in (0, 30, 60)),
        encoding="utf-8",
    )
    return reference, reads


def test_reports_are_written_when_paths_are_given(tmp_path: Path) -> None:
    reference, reads = _tiny_inputs(tmp_path)
    outcome = run_breseq_workflow(
        BreseqConfig(reference=(reference,), read1=reads, output_dir=tmp_path / "out"),
        html_path=tmp_path / "report.html",
        log_dir=tmp_path / "logs",
    )
    assert outcome.html_path == tmp_path / "report.html"
    assert outcome.log_path == tmp_path / "logs" / BRESEQ_LOG_NAME
    assert outcome.html_path.exists() and outcome.log_path.exists()
    assert "<!DOCTYPE html>" in outcome.html_path.read_text(encoding="utf-8")
    payload = json.loads(outcome.log_path.read_text(encoding="utf-8"))
    assert payload["workflow"] == "breseq"


def test_reports_are_skipped_without_paths(tmp_path: Path) -> None:
    reference, reads = _tiny_inputs(tmp_path)
    outcome = run_breseq_workflow(
        BreseqConfig(reference=(reference,), read1=reads, output_dir=tmp_path / "out")
    )
    assert outcome.html_path is None and outcome.log_path is None
    assert not (tmp_path / "report.html").exists()
