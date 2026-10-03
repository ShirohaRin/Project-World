from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.umi_processing import (
    UmiConfig,
    process_umi_fastq,
)

UPSTREAM_NAME = "NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA"


def write_fastq(path: Path, records: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as handle:
        for name, sequence in records:
            handle.write(b"@" + name.encode() + b"\n")
            handle.write(sequence + b"\n+\n")
            handle.write(b"I" * len(sequence) + b"\n")


def test_single_end_extracts_and_writes(tmp_path: Path) -> None:
    source = tmp_path / "r1.fq"
    output = tmp_path / "out.fq"
    write_fastq(source, [("read1", b"ACGTACGT" + b"GGGG"), ("read2", b"TTTTTTTTGG")])

    summary = process_umi_fastq(
        source, output, config=UmiConfig(location="read1", length=8)
    )

    assert summary.total_reads == 2
    assert summary.reads_with_umi == 2
    assert summary.trimmed_bases == 16
    # 两条 read 分别 12 bp 与 10 bp；各剪掉 8 bp。
    assert summary.input_bases == 22
    assert summary.output_bases == 6
    records = list(read_fastq(output))
    assert [item.name for item in records] == ["read1:ACGTACGT", "read2:TTTTTTTT"]
    assert [item.sequence for item in records] == [b"GGGG", b"GG"]


def test_paired_end_extracts_from_index_and_writes_both(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"
    write_fastq(source1, [(UPSTREAM_NAME, b"ACGTACGT"), (UPSTREAM_NAME, b"AAAACCCC")])
    write_fastq(source2, [(UPSTREAM_NAME, b"TGCATGCA"), (UPSTREAM_NAME, b"GGGGTTTT")])

    summary = process_umi_fastq(
        source1, output1,
        read2_path=source2, output2_path=output2,
        config=UmiConfig(location="index1"),
    )

    assert summary.total_reads == 4  # 双端按 read 条数计
    assert summary.reads_with_umi == 4
    assert summary.trimmed_bases == 0  # index 模式不剪序列
    records1 = list(read_fastq(output1))
    records2 = list(read_fastq(output2))
    assert all(item.name.startswith(
        "NS500713:64:HFKJJBGXY:1:11101:20469:1097:TATAGCCT "
    ) for item in records1 + records2)
    assert [item.sequence for item in records1] == [b"ACGTACGT", b"AAAACCCC"]


def test_paired_end_follows_gzip(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    plain2 = tmp_path / "tmp.fq"
    source2 = tmp_path / "r2.fq.gz"
    write_fastq(source1, [("r1", b"ACGTACGTGGGG")])
    write_fastq(plain2, [("r2", b"TGCATGCATTTT")])
    with gzip.open(source2, "wb") as handle:
        handle.write(plain2.read_bytes())
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"

    summary = process_umi_fastq(
        source1, output1,
        read2_path=source2, output2_path=output2,
        config=UmiConfig(location="per_read", length=8),
    )

    assert summary.total_reads == 2
    assert summary.trimmed_bases == 16
    assert output1.read_bytes()[:2] == b"\x1f\x8b"
    assert output2.read_bytes()[:2] == b"\x1f\x8b"


def test_supports_chinese_paths(tmp_path: Path) -> None:
    folder = tmp_path / "输入" / "结果"
    folder.mkdir(parents=True)
    source = folder / "一.fq"
    write_fastq(source, [("中文", b"ACGTACGTGGGG")])

    summary = process_umi_fastq(
        source, folder / "一.umi.fq", config=UmiConfig(location="read1", length=8)
    )

    assert summary.reads_with_umi == 1
    assert list(read_fastq(folder / "一.umi.fq"))[0].name == "中文:ACGTACGT"


def test_deletes_partial_outputs_when_pair_counts_differ(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    source2 = tmp_path / "r2.fq"
    output1 = tmp_path / "o1.fq"
    output2 = tmp_path / "o2.fq"
    write_fastq(source1, [("a", b"ACGTACGT"), ("b", b"ACGTACGT")])
    write_fastq(source2, [("a", b"TGCATGCA")])

    with pytest.raises(ValueError, match="记录数不一致"):
        process_umi_fastq(source1, output1, read2_path=source2, output2_path=output2)

    assert not output1.exists()
    assert not output2.exists()


def test_rejects_half_given_paired_paths(tmp_path: Path) -> None:
    source1 = tmp_path / "r1.fq"
    write_fastq(source1, [("a", b"ACGTACGT")])

    with pytest.raises(ValueError, match="必须同时给出"):
        process_umi_fastq(source1, tmp_path / "o1.fq", read2_path=tmp_path / "r2.fq")


def test_reports_missing_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="文件不存在"):
        process_umi_fastq(tmp_path / "missing.fq", tmp_path / "o.fq")
