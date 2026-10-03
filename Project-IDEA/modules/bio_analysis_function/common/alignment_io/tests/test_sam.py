"""SAM 记录的解析、写出与往返，以及各类坏记录的报错路径。

测试用的 SAM 文本是**按 SAM v1 规范手写的**，不是从别处抓的数据：本项要验的是
"格式读得对不对、往返是否逐字节一致"，而不是某个真实数据集的内容。
真实数据的 SAM 用例要等比对器与上游对拍时一起补（见 `alignment_io.md` 待办）。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.alignment_io import (
    AlignmentFormatError,
    SamHeader,
    SamRecord,
    SamSequence,
    open_sam,
    parse_sam_record,
    read_sam,
    write_sam,
)

_SEQUENCE = "ACGT" * 25  # 100 bp
_QUALITIES = "I" * 100

_HEADER_LINES = [
    "@HD\tVN:1.6\tSO:coordinate",
    "@SQ\tSN:NC_001422\tLN:5386",
    "@RG\tID:demo\tSM:sample",
]

_RECORD_LINES = [
    # read1 正向：前 10 个碱基软剪裁，参考跨度 90
    f"read1\t99\tNC_001422\t100\t60\t10S90M\t=\t300\t290\t{_SEQUENCE}\t{_QUALITIES}\tNM:i:2\tMD:Z:88A1",
    # read1 的对端：反向、read2
    f"read1\t147\tNC_001422\t300\t60\t100M\t=\t100\t-290\t{_SEQUENCE}\t{_QUALITIES}\tNM:i:1",
    # 一条带缺失的独立比对：50M5D50M → query 100、参考跨度 105
    f"read3\t16\tNC_001422\t1000\t42\t50M5D50M\t*\t0\t0\t{_SEQUENCE}\t{_QUALITIES}",
    # 未比对
    "read4\t4\t*\t0\t0\t*\t*\t0\t0\t*\t*",
]

_SAM_TEXT = "\n".join(_HEADER_LINES + _RECORD_LINES) + "\n"
_RECORDS_ONLY = "\n".join(_RECORD_LINES) + "\n"


@pytest.fixture()
def sam_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo.sam"
    path.write_text(_SAM_TEXT, encoding="utf-8")
    return path


def test_header_parses_sequences_and_keeps_lines(sam_file: Path) -> None:
    with open_sam(sam_file) as reader:
        header = reader.header
    assert header.sequences == (SamSequence(name="NC_001422", length=5386),)
    assert header.reference_length("NC_001422") == 5386
    assert header.reference_length("nope") is None
    assert header.to_lines()[0] == "@HD\tVN:1.6\tSO:coordinate"
    assert "@RG\tID:demo\tSM:sample" in header.to_lines()


def test_header_from_sequences_builds_hd_and_sq() -> None:
    header = SamHeader.from_sequences([("chr", 1000), ("plasmid", 200)], sort_order="unsorted")
    assert header.to_lines() == (
        "@HD\tVN:1.6\tSO:unsorted",
        "@SQ\tSN:chr\tLN:1000",
        "@SQ\tSN:plasmid\tLN:200",
    )
    assert header.reference_length("plasmid") == 200


def test_reader_streams_all_records(sam_file: Path) -> None:
    header, records = read_sam(sam_file)
    assert len(header.sequences) == 1
    assert [record.query_name for record in records] == ["read1", "read1", "read3", "read4"]


def test_first_record_fields_and_flags(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    record = records[0]
    assert record.flag == 99
    assert record.is_paired is True
    assert record.is_proper_pair is True
    assert record.is_read1 is True
    assert record.is_read2 is False
    assert record.is_reverse is False
    assert record.mapping_quality == 60
    assert record.cigar.has_soft_clip is True
    assert record.cigar.query_length == len(record.sequence)
    assert record.reference_end == 189  # 100 + 90 - 1
    assert record.next_reference_name == "="
    assert record.template_length == 290


def test_second_record_is_reverse_read2(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    record = records[1]
    assert record.flag == 147
    assert record.is_read2 is True
    assert record.is_read1 is False
    assert record.is_reverse is True
    assert record.template_length == -290


def test_missing_base_record_spans_reference_not_query(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    record = records[2]
    assert record.cigar.has_indel is True
    assert record.cigar.query_length == 100
    assert record.cigar.reference_length == 105
    assert record.reference_end == 1104  # 1000 + 105 - 1
    assert record.tags == ()


def test_unmapped_record(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    record = records[3]
    assert record.is_unmapped is True
    assert record.cigar.is_empty is True
    assert record.sequence == "*"
    assert record.qualities == "*"
    assert record.reference_end == 0


def test_tag_accessors(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    record = records[0]
    assert record.tag("NM") == "2"
    assert record.tag_int("NM") == 2
    assert record.tag("MD") == "88A1"
    assert record.tag("AS") is None
    assert record.tag_int("AS") is None
    assert record.tag_int("MD") is None


def test_record_round_trip_through_text(sam_file: Path) -> None:
    _, records = read_sam(sam_file)
    for record in records:
        assert parse_sam_record(record.to_line()) == record


def test_write_then_read_is_byte_stable(tmp_path: Path, sam_file: Path) -> None:
    header, records = read_sam(sam_file)
    out = tmp_path / "out.sam"
    count = write_sam(out, header, records)
    assert count == 4
    assert out.read_text(encoding="utf-8") == _SAM_TEXT


def test_write_and_read_gzip(tmp_path: Path, sam_file: Path) -> None:
    header, records = read_sam(sam_file)
    out = tmp_path / "out.sam.gz"
    write_sam(out, header, records, compress=True)
    with gzip.open(out, "rt", encoding="utf-8") as handle:
        assert handle.readline().startswith("@HD")
    _, again = read_sam(out)
    assert [record.query_name for record in again] == ["read1", "read1", "read3", "read4"]


def test_headerless_sam_is_read(tmp_path: Path) -> None:
    path = tmp_path / "no_header.sam"
    path.write_text(_RECORDS_ONLY, encoding="utf-8")
    header, records = read_sam(path)
    assert header.sequences == ()
    assert len(records) == 4


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "blank.sam"
    path.write_text("@SQ\tSN:c\tLN:10\n\n\n" + _RECORDS_ONLY, encoding="utf-8")
    _, records = read_sam(path)
    assert len(records) == 4


def test_reader_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AlignmentFormatError, match="不存在"):
        open_sam(tmp_path / "nope.sam")


# ---------------------------------------------------------------------------
# 报错路径
# ---------------------------------------------------------------------------


def _line(**overrides: object) -> str:
    fields = {
        "qname": "read",
        "flag": "0",
        "rname": "NC_001422",
        "pos": "100",
        "mapq": "60",
        "cigar": "100M",
        "rnext": "*",
        "pnext": "0",
        "tlen": "0",
        "seq": _SEQUENCE,
        "qual": _QUALITIES,
    }
    fields.update(overrides)
    return "\t".join(str(value) for value in fields.values())


def test_too_few_fields_is_rejected() -> None:
    with pytest.raises(AlignmentFormatError, match="11"):
        parse_sam_record("read\t0\tNC_001422\t100\t60")


def test_non_integer_mandatory_field_is_rejected() -> None:
    with pytest.raises(AlignmentFormatError, match="MAPQ"):
        parse_sam_record(_line(mapq="六十"))


def test_broken_tag_is_rejected() -> None:
    with pytest.raises(AlignmentFormatError, match="name:type:value"):
        parse_sam_record(_line() + "\tNM:2")


def test_sequence_length_must_match_cigar_query() -> None:
    with pytest.raises(AlignmentFormatError, match="SEQ 长度"):
        parse_sam_record(_line(cigar="10S80M"))  # 只消费 90 个 query，而 SEQ 是 100


def test_quality_length_must_match_sequence() -> None:
    with pytest.raises(AlignmentFormatError, match="QUAL 长度"):
        parse_sam_record(_line(qual="I" * 99))


def test_star_sequence_requires_star_quality() -> None:
    with pytest.raises(AlignmentFormatError, match="QUAL"):
        parse_sam_record(_line(seq="*", qual="I" * 10))

    record = parse_sam_record(_line(flag="4", rname="*", pos="0", cigar="*", seq="*", qual="*"))
    assert record.is_unmapped is True


def test_mapped_record_requires_reference_and_position() -> None:
    with pytest.raises(AlignmentFormatError, match="没有参考名"):
        parse_sam_record(_line(rname="*"))
    with pytest.raises(AlignmentFormatError, match="1-based"):
        parse_sam_record(_line(pos="0"))


def test_construction_rejects_bad_values() -> None:
    good = parse_sam_record(_line())
    with pytest.raises(AlignmentFormatError):
        SamRecord(
            query_name="x",
            flag=0,
            reference_name="NC_001422",
            position=100,
            mapping_quality=999,
            cigar=good.cigar,
            next_reference_name="*",
            next_position=0,
            template_length=0,
            sequence=_SEQUENCE,
            qualities=_QUALITIES,
        )
    with pytest.raises(AlignmentFormatError):
        SamRecord(
            query_name="x",
            flag=0,
            reference_name="NC_001422",
            position=100,
            mapping_quality=60,
            cigar=good.cigar,
            next_reference_name="*",
            next_position=0,
            template_length=0,
            sequence=_SEQUENCE.lower(),
            qualities=_QUALITIES,
        )
