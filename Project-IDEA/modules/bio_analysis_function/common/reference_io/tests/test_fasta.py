"""FASTA 读取的确定性用例。

FASTA 只提供序列，所以这里要守的是"读进来的序列与文件里的一致、拓扑按线状处理、
坏文件要报错"，以及 gzip 按魔数识别这条公共层一贯的口径。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.reference_io import (
    ReferenceFormatError,
    parse_fasta,
    read_fasta,
)


def test_parse_multiple_records() -> None:
    reference = parse_fasta(">chr description here\nACGTACGT\n>plasmid\nTTTT\n")
    assert reference.ids == ("chr", "plasmid")
    assert reference.get("chr").sequence == "ACGTACGT"
    assert reference.get("chr").description == "description here"
    assert reference.get("plasmid").description == ""
    assert reference.total_length == 12


def test_fasta_is_always_linear_and_featureless() -> None:
    reference = parse_fasta(">chr\nACGT\n")
    assert reference.get("chr").circular is False
    assert reference.get("chr").features == ()
    assert reference.feature_count == 0


def test_sequence_is_uppercased_and_whitespace_is_dropped() -> None:
    reference = parse_fasta(">chr\nac gt\n\tAC\nGT\n")
    assert reference.get("chr").sequence == "ACGTACGT"


def test_blank_lines_are_ignored() -> None:
    reference = parse_fasta("\n>chr\n\nACGT\n\n")
    assert reference.get("chr").sequence == "ACGT"


def test_empty_record_is_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_fasta(">chr\n>other\nACGT\n")


def test_sequence_before_header_is_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_fasta("ACGT\n>chr\nACGT\n")


def test_empty_header_is_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_fasta(">\nACGT\n")


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_fasta(">chr\nACGT\n>chr\nTTTT\n")


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ReferenceFormatError):
        parse_fasta("   \n\n")


def test_read_fasta_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReferenceFormatError, match="不存在"):
        read_fasta(tmp_path / "nope.fa")


def test_read_fasta_handles_plain_and_gzip(tmp_path: Path) -> None:
    plain = tmp_path / "ref.fa"
    plain.write_text(">chr\nacgt\n", encoding="utf-8")
    assert read_fasta(plain).get("chr").sequence == "ACGT"

    compressed = tmp_path / "ref.fa.gz"
    with gzip.open(compressed, "wt", encoding="utf-8") as handle:
        handle.write(">chr\nACGTACGT\n")
    assert read_fasta(compressed).get("chr").length == 8
