"""reads 规范化的测试。

这一步的"对错"全在几个写死的常数与判断上，所以测试集中在：

1. **重编码的下界被钉在 33**（``max(33, q-31)``）——低于 ``'@'`` 的字符不能变成负数；
2. **探测是单向的**：能确定是 33 就给 33，看不出来就给 ``None``，**绝不猜 64**；
3. **不改动时返回原对象**（不然白复制一遍）；
4. **MGI 修复只认 ``/1``、``/2``**，其余名字原样保留。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import FastqRecord, read_fastq
from modules.bio_analysis_function.submodules.read_normalization import (
    NormalizeConfig,
    convert_phred64_to_phred33,
    detect_phred_offset,
    detect_quality_offset,
    normalize_fastq,
    normalize_record,
)

# ---------------------------------------------------------------------------
# 质量编码
# ---------------------------------------------------------------------------


def test_phred64_conversion_shifts_by_31() -> None:
    # '@'(64) 是 Phred+64 的 Q0 → 33（'!'）
    assert convert_phred64_to_phred33(b"@") == b"!"
    # 'A'(65) 是 Q1 → 34（'"'）
    assert convert_phred64_to_phred33(b"A") == b'"'
    # '~'(126) 是 Q62 → 95（'_'）
    assert convert_phred64_to_phred33(b"~") == b"_"
    # 整串一起转
    assert convert_phred64_to_phred33(b"@AB") == b"!\"#"


def test_phred64_conversion_clamps_at_33() -> None:
    """低于 ``'@'`` 的字符减 31 会小于 33，必须被夹回 33（上游如此）。"""
    assert convert_phred64_to_phred33(b"!") == b"!"
    assert convert_phred64_to_phred33(b" ") == b"!"
    assert convert_phred64_to_phred33(bytes([0])) == b"!"


def test_phred64_conversion_keeps_length() -> None:
    assert len(convert_phred64_to_phred33(b"@AAB~")) == 5


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


def test_detect_returns_33_when_a_low_character_appears() -> None:
    """出现 ASCII < 64 的字符 → 只能是 Phred+33。"""
    assert detect_phred_offset([b"IIII", b"!!II"]) == 33


def test_detect_returns_none_when_ambiguous() -> None:
    """全落在重叠区间时看不出来——**不能猜 64**。"""
    assert detect_phred_offset([b"IIII", b"~~~~"]) is None
    assert detect_phred_offset([]) is None


# ---------------------------------------------------------------------------
# 记录级
# ---------------------------------------------------------------------------


def _record(name: str = "read1", sequence: str = "ACGT", quality: str = "IIII"):
    return FastqRecord(
        name=name,
        sequence=sequence.encode("ascii"),
        quality=quality.encode("ascii"),
    )


def test_unchanged_record_is_returned_as_is() -> None:
    """什么都不改时必须是**同一个对象**，不该白复制。"""
    record = _record()

    outcome = normalize_record(record, NormalizeConfig())

    assert outcome.record is record
    assert outcome.renamed is False
    assert outcome.requantified is False


def test_requantify_only_when_asked() -> None:
    record = _record(quality="AAAA")  # 'A'=65 → Q1 → 34 = '"'

    outcome = normalize_record(record, NormalizeConfig(input_phred=64))

    assert outcome.requantified is True
    assert outcome.record.quality == b'"' * 4
    assert outcome.record.name == record.name  # 名字没动
    assert outcome.record.sequence == record.sequence


def test_fix_mgi_only_when_asked() -> None:
    record = _record(name="MGI-123/1")

    assert normalize_record(record, NormalizeConfig()).record.name == "MGI-123/1"
    outcome = normalize_record(record, NormalizeConfig(fix_mgi=True))
    assert outcome.renamed is True
    assert outcome.record.name == "MGI-123 /1"


def test_fix_mgi_leaves_other_names_alone() -> None:
    for name in ("read1", "read/3", "read/", "read 1:N:0:ACGT"):
        outcome = normalize_record(_record(name=name), NormalizeConfig(fix_mgi=True))
        assert outcome.renamed is False, name
        assert outcome.record.name == name


def test_fix_mgi_handles_a_bare_slash_number() -> None:
    """名字就是 ``/1`` 这种极端形状时也照规则走（上游只看最后两个字符）。"""
    outcome = normalize_record(_record(name="/1"), NormalizeConfig(fix_mgi=True))

    assert outcome.renamed is True
    assert outcome.record.name == " /1"


def test_both_transformations_at_once() -> None:
    record = _record(name="x/2", quality="AAAA")

    outcome = normalize_record(
        record, NormalizeConfig(input_phred=64, fix_mgi=True)
    )

    assert outcome.record.name == "x /2"
    assert outcome.record.quality == b'"' * 4
    assert outcome.renamed and outcome.requantified


def test_invalid_input_phred_rejected() -> None:
    with pytest.raises(ValueError, match="input_phred"):
        NormalizeConfig(input_phred=64 - 1)
    with pytest.raises(ValueError, match="input_phred"):
        NormalizeConfig(input_phred=0)


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------


def write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for name, sequence, quality in records:
            handle.write(f"@{name}\n{sequence}\n+\n{quality}\n")


def test_file_level_requantify(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    write_fastq(source, [("r1", "ACGT", "AAAA"), ("r2", "TTTT", "BBBB")])

    summary = normalize_fastq(
        source, output, config=NormalizeConfig(input_phred=64)
    )

    assert summary.total_reads == 2
    assert summary.requantified_reads == 2
    assert summary.renamed_reads == 0
    assert summary.input_bases == summary.output_bases == 8
    assert [record.quality for record in read_fastq(output)] == [
        b'"' * 4,
        b"#" * 4,
    ]


def test_file_level_leaves_sequences_and_names_untouched_by_default(
    tmp_path: Path,
) -> None:
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    records = [("a/1", "ACGT", "IIII"), ("b/2", "TTTT", "JJJJ")]
    write_fastq(source, records)

    normalize_fastq(source, output)

    assert output.read_bytes() == source.read_bytes()


def test_file_level_fix_mgi(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    output = tmp_path / "out.fq"
    write_fastq(source, [("a/1", "ACGT", "IIII"), ("b/2", "TTTT", "JJJJ")])

    summary = normalize_fastq(source, output, config=NormalizeConfig(fix_mgi=True))

    assert summary.renamed_reads == 2
    assert [record.name for record in read_fastq(output)] == ["a /1", "b /2"]


def test_file_level_paired(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, [("a/1", "ACGT", "AAAA"), ("b/1", "TTTT", "BBBB")])
    write_fastq(read2, [("a/2", "GGGG", "AAAA"), ("b/2", "CCCC", "BBBB")])
    out1 = tmp_path / "clean_R1.fq"
    out2 = tmp_path / "clean_R2.fq"

    summary = normalize_fastq(
        read1,
        out1,
        read2_path=read2,
        output2_path=out2,
        config=NormalizeConfig(input_phred=64, fix_mgi=True),
    )

    assert summary.total_reads == 4  # 双端按 read 条数计
    assert summary.renamed_reads == 4
    assert summary.requantified_reads == 4
    assert [record.name for record in read_fastq(out1)] == ["a /1", "b /1"]
    assert [record.name for record in read_fastq(out2)] == ["a /2", "b /2"]


def test_file_level_paired_needs_both_outputs(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, [("a/1", "ACGT", "IIII")])
    write_fastq(read2, [("a/2", "GGGG", "IIII")])

    with pytest.raises(ValueError, match="两份输出路径"):
        normalize_fastq(read1, tmp_path / "out.fq", read2_path=read2)


def test_second_output_without_second_input_rejected(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, [("a/1", "ACGT", "IIII")])

    with pytest.raises(ValueError, match="第二份输出路径"):
        normalize_fastq(source, tmp_path / "out.fq", output2_path=tmp_path / "o2.fq")


def test_file_level_mismatched_counts(tmp_path: Path) -> None:
    read1 = tmp_path / "R1.fq"
    read2 = tmp_path / "R2.fq"
    write_fastq(read1, [("a/1", "ACGT", "IIII"), ("b/1", "TTTT", "IIII")])
    write_fastq(read2, [("a/2", "GGGG", "IIII")])
    out1 = tmp_path / "clean_R1.fq"
    out2 = tmp_path / "clean_R2.fq"

    with pytest.raises(ValueError, match="记录数不一致"):
        normalize_fastq(read1, out1, read2_path=read2, output2_path=out2)

    assert not out1.exists() and not out2.exists()


def test_file_level_removes_partial_output(tmp_path: Path) -> None:
    broken = tmp_path / "broken.fq"
    broken.write_bytes(b"@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\n")
    output = tmp_path / "out.fq"

    with pytest.raises(ValueError, match="记录不完整"):
        normalize_fastq(broken, output)

    assert not output.exists()


def test_file_level_follows_input_compression(tmp_path: Path) -> None:
    plain = tmp_path / "in.fq"
    write_fastq(plain, [("a/1", "ACGT", "IIII")])
    source = tmp_path / "in.fq.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(plain.read_bytes())
    output = tmp_path / "out.fq"

    normalize_fastq(source, output)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert [record.name for record in read_fastq(output)] == ["a/1"]


def test_file_level_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        normalize_fastq(tmp_path / "absent.fq", tmp_path / "out.fq")


def test_detect_quality_offset_on_file(tmp_path: Path) -> None:
    source = tmp_path / "in.fq"
    write_fastq(source, [("a", "ACGT", "!III")])

    assert detect_quality_offset(source) == 33

    source2 = tmp_path / "in2.fq"
    write_fastq(source2, [("a", "ACGT", "IIII")])
    assert detect_quality_offset(source2) is None
