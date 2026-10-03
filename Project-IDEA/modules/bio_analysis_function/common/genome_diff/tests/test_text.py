"""解析与写回的用例。

**真实样例**取自一份公开的 breseq 产物（breseq 0.33.1，ALE 数据库 aledb.org 里 pgi 进化实验的
`.../breseq/15-3-0-1/output/output.gd`），下面按原样抄了头 12 行与 9 条不同类型的记录。
选它是因为它同时含四种突变类型（DEL / SNP / INS / MOB）与四种证据类型（RA / MC / JC），
还带上了"多证据引用""``frequency=NA``""长属性串""跨参考的连接记录"这些边角。
`test_real_sample_round_trips_byte_for_byte` 是这一片最硬的一条：**逐字节往返**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.genome_diff import (
    format_genome_diff,
    parse_genome_diff,
    read_genome_diff,
    write_genome_diff,
)

_REAL_LINES = (
    "#=GENOME_DIFF\t1.0",
    "#=CREATED\t13:24:43 25 Feb 2020",
    "#=PROGRAM\tbreseq 0.33.1 revision 8505477f25b3",
    "#=COMMAND\t/usr/bin/breseq -p -j 1 -o breseq_processes/43 -r /var/data/NC_000913_3.gb"
    "\t/var/alemutpipe-outputs/good/pgi-15-3_S31_L001_R1_001.good.fq",
    "#=REFSEQ\t/var/data/NC_000913_3.gb",
    "#=READSEQ\t/var/alemutpipe-outputs/good/pgi-15-3_S31_L001_R1_001.good.fq",
    "#=READSEQ\t/var/alemutpipe-outputs/good/pgi-15-3_S31_L001_R2_001.good.fq",
    "#=CONVERTED-BASES\t446432193",
    "#=CONVERTED-READS\t1638068",
    "#=INPUT-BASES\t446432193",
    "#=INPUT-READS\t1638068",
    "#=MAPPED-BASES\t434491437",
    "#=MAPPED-READS\t1598390",
    "DEL\t2\t35\tNC_000913\t701810\t1\tfrequency=1.16257668e-01",
    "INS\t4\t74\tNC_000913\t1159282\tAGT\tfrequency=8.36314739e-02",
    "SNP\t5\t37\tNC_000913\t1269450\tT\tfrequency=1.04031563e-01",
    "MOB\t7\t72,77\tNC_000913\t1293032\tIS1\t-1\t8\tfrequency=1",
    "RA\t35\t.\tNC_000913\t701810\t0\tA\t.\tbias_e_value=4497810\tbias_p_value=0.96901"
    "\tconsensus_score=280.3\tfrequency=1.16257668e-01\tks_quality_p_value=0.761603"
    "\tmajor_base=A\tmajor_cov=32/42\tmajor_frequency=8.83742332e-01\tminor_base=."
    "\tminor_cov=4/6\tnew_cov=4/6\tpolymorphism_frequency=1.16257668e-01"
    "\tpolymorphism_score=27.3\tprediction=polymorphism\tref_cov=32/42\ttotal_cov=37/49",
    "RA\t37\t.\tNC_000913\t1269450\t0\tC\tT\tconsensus_score=133.2\tfrequency=1.04031563e-01"
    "\tmajor_base=C\tmajor_cov=21/22\tmajor_frequency=8.95968437e-01\tminor_base=T"
    "\tminor_cov=2/3\tnew_cov=2/3\tpolymorphism_frequency=1.04031563e-01"
    "\tpolymorphism_score=6.4\tprediction=polymorphism\tref_cov=21/22\ttotal_cov=23/25",
    "RA\t51\t.\tNC_000913\t3560455\t1\t.\tG\tconsensus_score=283.5\tfrequency=1"
    "\tmajor_base=G\tmajor_frequency=1.00000000e+00\tminor_base=N"
    "\tpolymorphism_reject=FREQUENCY_CUTOFF,VARIANT_STRAND_COVERAGE"
    "\tpolymorphism_score=NA\tprediction=consensus\ttotal_cov=38/41",
    "MC\t62\t.\tNC_000913\t1299499\t1300697\t1198\t0\tleft_inside_cov=0\tleft_outside_cov=55"
    "\tright_inside_cov=0\tright_outside_cov=55",
    "JC\t69\t.\tNC_000913\t1\t1\tNC_000913\t4641652\t-1\t0\talignment_overlap=0"
    "\tcircular_chromosome=1\tcoverage_minus=48\tcoverage_plus=39\tflanking_left=289"
    "\tflanking_right=289\tfrequency=1\tjunction_possible_overlap_registers=261"
    "\tkey=NC_000913__1__1__NC_000913__4641652__-1__0____289__289__0__0\tmax_left=276"
    "\tmax_pos_hash_score=524\tneg_log10_pos_hash_p_value=NT\tnew_junction_coverage=0.97"
    "\tnew_junction_read_count=87\tpolymorphism_frequency=1.00000000e+00\tpos_hash_score=70"
    "\tprediction=consensus\tside_1_annotate_key=gene\tside_2_annotate_key=gene"
    "\ttotal_non_overlap_reads=87",
)
_REAL_SAMPLE = "\n".join(_REAL_LINES) + "\n"


# ---------------------------------------------------------------------------
# 真实样例
# ---------------------------------------------------------------------------


def test_real_sample_round_trips_byte_for_byte() -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)

    assert format_genome_diff(diff) == _REAL_SAMPLE


def test_real_sample_header_is_parsed() -> None:
    header = parse_genome_diff(_REAL_SAMPLE).header

    assert header.version == "1.0"
    assert header.value_of("PROGRAM") == "breseq 0.33.1 revision 8505477f25b3"
    assert header.value_of("CREATED") == "13:24:43 25 Feb 2020"
    # READSEQ 出现两次（双端），要都留住
    assert len(header.values_of("READSEQ")) == 2
    assert header.values_of("READSEQ")[1].endswith("R2_001.good.fq")
    assert header.value_of("MAPPED-BASES") == "434491437"
    assert header.value_of("GENOME_DIFF") is None  # 版本行单独放，不混进 entries


def test_real_sample_record_columns_per_type() -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)
    by_id = {record.id: record for record in diff.records}

    deletion = by_id["2"]
    assert (deletion.type, deletion.seq_id, deletion.position) == ("DEL", "NC_000913", 701810)
    assert deletion.evidence == ("35",)
    assert deletion.fields == ("1",)  # 缺失长度
    assert deletion.frequency == pytest.approx(1.16257668e-01)

    insertion = by_id["4"]
    assert insertion.fields == ("AGT",)  # 插入的碱基
    assert insertion.evidence == ("74",)

    substitution = by_id["5"]
    assert substitution.fields == ("T",)  # 新碱基（html 里那一行是 C→T）

    mobile = by_id["7"]
    assert mobile.evidence == ("72", "77")  # 两条证据
    assert mobile.fields == ("IS1", "-1", "8")  # 元件名、链、长度


def test_real_sample_evidence_records() -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)
    by_id = {record.id: record for record in diff.records}

    # 证据行的"证据编号"列是 `.`，解析成空元组
    assert by_id["35"].evidence == ()
    assert by_id["35"].type == "RA"
    assert by_id["35"].fields == ("0", "A", ".")  # 偏移、参考碱基、判定碱基（. 表示缺失）

    assert by_id["37"].fields == ("0", "C", "T")

    missing_coverage = by_id["62"]
    assert (missing_coverage.type, missing_coverage.fields) == ("MC", ("1300697", "1198", "0"))

    junction = by_id["69"]
    assert junction.type == "JC"
    # 连接记录的第二侧坐标与参考名放在类型特有字段里
    assert junction.fields[:5] == ("1", "NC_000913", "4641652", "-1", "0")
    assert junction.attribute("circular_chromosome") == "1"


def test_real_sample_split_of_mutations_and_evidence() -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)

    assert [record.type for record in diff.mutations()] == ["DEL", "INS", "SNP", "MOB"]
    assert [record.type for record in diff.evidence()] == ["RA", "RA", "RA", "MC", "JC"]
    # 突发行能顺着编号找回它的证据
    deletion = diff.by_id("2")
    assert deletion is not None
    assert [record.id for record in diff.evidence_of(deletion)] == ["35"]


def test_real_sample_attributes_keep_order_and_unknown_keys() -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)
    record = diff.by_id("35")
    assert record is not None

    keys = list(record.attributes)
    assert keys[0] == "bias_e_value"
    assert keys[-1] == "total_cov"
    assert record.attribute("major_base") == "A"
    assert record.attribute("minor_base") == "."
    assert record.attribute("prediction") == "polymorphism"
    # 属性值里可以带逗号、NA、科学计数法
    assert diff.by_id("51").attribute("polymorphism_reject") == (
        "FREQUENCY_CUTOFF,VARIANT_STRAND_COVERAGE"
    )
    assert diff.by_id("51").attribute("polymorphism_score") == "NA"


# ---------------------------------------------------------------------------
# 边角与错误
# ---------------------------------------------------------------------------


def test_unknown_type_and_attributes_survive_a_round_trip() -> None:
    text = (
        "#=GENOME_DIFF\t1.0\n"
        "XXX\t9\t.\tchr\t100\tfoo\tbar=baz\twhatever=1.5\n"  # 类型与属性都不认识
        "SNP\t10\t9\tchr\t200\tA\tfrequency=1\n"
    )
    diff = parse_genome_diff(text)

    unknown = diff.records[0]
    assert unknown.type == "XXX"
    assert unknown.fields == ("foo",)
    assert dict(unknown.attributes) == {"bar": "baz", "whatever": "1.5"}
    assert unknown.is_evidence is False  # 不认识的类型不归类，但不丢
    assert format_genome_diff(diff) == text


def test_comment_and_blank_lines_are_kept_or_skipped() -> None:
    text = (
        "#=GENOME_DIFF\t1.0\r\n"
        "# 手工整理的注释，不是 #= 元信息\r\n"
        "\r\n"
        "SNP\t1\t.\tchr\t10\tA\tfrequency=1\r\n"
    )
    diff = parse_genome_diff(text)

    assert diff.header.entries == (("#", ("# 手工整理的注释，不是 #= 元信息",)),)
    assert diff.header.values_of("#") == ("# 手工整理的注释，不是 #= 元信息",)
    # 写回时注释原样保留、CRLF 归一成 LF、空行丢掉
    assert format_genome_diff(diff) == (
        "#=GENOME_DIFF\t1.0\n"
        "# 手工整理的注释，不是 #= 元信息\n"
        "SNP\t1\t.\tchr\t10\tA\tfrequency=1\n"
    )


def test_bare_token_after_the_attribute_section_becomes_an_empty_value() -> None:
    diff = parse_genome_diff("#=GENOME_DIFF\t1.0\nSNP\t1\t.\tchr\t10\tA\tfrequency=1\todd\n")

    assert diff.records[0].fields == ("A",)
    assert dict(diff.records[0].attributes) == {"frequency": "1", "odd": ""}
    assert format_genome_diff(diff).splitlines()[1].endswith("\tfrequency=1\todd=")


def test_bare_tokens_without_any_equals_sign_are_read_as_fields() -> None:
    """一个 ``=`` 都没出现时，无从判断"裸词"是字段还是属性——按字段处理（格式本身的歧义）。

    真实产物不会走到这一步（类型特有字段后面总跟着 ``key=value``），但把这条行为钉住，
    免得以后有人以为它是 bug。
    """
    diff = parse_genome_diff("#=GENOME_DIFF\t1.0\nSNP\t1\t.\tchr\t10\tA\todd\n")

    assert diff.records[0].fields == ("A", "odd")
    assert dict(diff.records[0].attributes) == {}


def test_missing_version_line_falls_back_to_the_default() -> None:
    diff = parse_genome_diff("SNP\t1\t.\tchr\t10\tA\tfrequency=1\n")

    assert diff.header.version == "1.0"
    # 写回时会补上版本行（这是格式要求的第一行）
    assert format_genome_diff(diff).startswith("#=GENOME_DIFF\t1.0\n")


def test_short_lines_are_rejected_with_the_line_number() -> None:
    with pytest.raises(ValueError, match="第 2 行"):
        parse_genome_diff("#=GENOME_DIFF\t1.0\nSNP\t1\t.\n")


def test_non_integer_positions_are_rejected_with_the_line_number() -> None:
    with pytest.raises(ValueError, match="位置不是整数"):
        parse_genome_diff("#=GENOME_DIFF\t1.0\nSNP\t1\t.\tchr\t12x\tA\n")


def test_write_and_read_back_through_a_file(tmp_path: Path) -> None:
    diff = parse_genome_diff(_REAL_SAMPLE)
    target = tmp_path / "nested" / "output.gd"
    expected_records = sum(1 for line in _REAL_LINES if not line.startswith("#"))

    assert write_genome_diff(target, diff) == expected_records
    assert read_genome_diff(target) == diff
    assert target.read_text(encoding="utf-8") == _REAL_SAMPLE
