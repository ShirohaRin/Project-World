"""SAM 落盘的确定性用例 + 一份真实参考上的端到端验收。

分两组：

- **MD / 记录转换**：期望值能手算的用例（一个错配、一个缺失、一个剪裁+插入），
  以及正负链的 ``SEQ`` / ``QUAL`` 朝向——这是 SAM 规范里最容易写反的地方；
- **端到端**：在 phiX174 真实参考上模拟 reads，比对 → 写 SAM → 再用公共层的读取器
  读回来逐条核对（坐标、链方向、CIGAR、NM、MAPQ）。走一遍公共层契约，等于顺带验了
  "写出来的东西别人读得懂"。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from modules.bio_analysis_function.common.alignment_io import read_sam
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
    read_genbank,
)
from modules.bio_analysis_function.common.sequences import reverse_complement
from modules.bio_analysis_function.submodules.read_mapping import (
    Alignment,
    Mapper,
    MappingParams,
    Read,
    ReferenceIndex,
)
from modules.bio_analysis_function.submodules.read_mapping.sam_output import (
    build_md_tag,
    to_sam_record,
    write_mapped_sam,
)

_SEED = 20260923
_REAL_GENBANK = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "phiX174_NC_001422.1.gbk"
)
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _revcomp(text: str) -> str:
    return text.translate(_COMPLEMENT)[::-1]


def _random_sequence(length: int, seed: int = _SEED) -> str:
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT") for _ in range(length))


def _reference_of(sequence: str, seq_id: str = "chr") -> ReferenceSet:
    return ReferenceSet.of([ReferenceSequence(seq_id=seq_id, sequence=sequence)])


def _alignment(
    cigar: str,
    reference_start: int,
    *,
    query_length: int,
    reference_length: int,
    mismatches: int = 0,
    insertions: int = 0,
    deletions: int = 0,
    soft_clipped: int = 0,
    strand: int = 1,
) -> Alignment:
    from modules.bio_analysis_function.common.alignment_io import parse_cigar

    return Alignment(
        query_name="r",
        seq_id="chr",
        strand=strand,
        reference_start=reference_start,
        reference_length=reference_length,
        query_length=query_length,
        mismatches=mismatches,
        insertions=insertions,
        deletions=deletions,
        cigar=parse_cigar(cigar),
        seed_hits=1,
        candidates=1,
        soft_clipped=soft_clipped,
        mapq=60,
    )


# ---------------------------------------------------------------------------
# MD 串
# ---------------------------------------------------------------------------


def test_md_tag_records_a_mismatch() -> None:
    """第 4 个碱基由参考的 T 变成 read 的 A：MD 写成"3 个匹配 + 参考碱基 + 6 个匹配"。"""
    reference = "ACGTACGTAC"
    query = "ACGAACGTAC"
    alignment = _alignment(
        "10M", 0, query_length=10, reference_length=10, mismatches=1
    )
    assert build_md_tag(alignment, query, reference) == "3T6"


def test_md_tag_records_a_deletion_with_caret() -> None:
    """read 少了参考上的那个 T：MD 用 ``^`` 加被删的参考碱基。"""
    reference = "ACGTACGT"
    query = "ACGACGT"
    alignment = _alignment(
        "3M1D4M", 0, query_length=7, reference_length=8, deletions=1
    )
    assert build_md_tag(alignment, query, reference) == "3^T4"


def test_md_tag_ignores_insertions_and_soft_clips() -> None:
    """插入与剪裁都不占参考位置，因此不进 MD。"""
    reference = "AAACCCGGG"
    query = "TT" + "AAA" + "C" + "GCC"  # 2S + 3M + 1I + 3M
    alignment = _alignment(
        "2S3M1I3M",
        0,
        query_length=9,
        reference_length=6,
        mismatches=1,
        insertions=1,
        soft_clipped=2,
    )
    assert build_md_tag(alignment, query, reference) == "3C2"


# ---------------------------------------------------------------------------
# 记录转换
# ---------------------------------------------------------------------------


def test_sam_record_on_positive_strand() -> None:
    reference = _reference_of("AAAA" + "ACGTACGTAC" + "AAAA")
    alignment = _alignment("10M", 4, query_length=10, reference_length=10)
    read = Read(name="r1", sequence="ACGTACGTAC", qualities="I" * 10)
    record = to_sam_record(read, alignment, reference)
    assert record.query_name == "r1"
    assert record.flag == 0
    assert record.reference_name == "chr"
    assert record.position == 5  # 0-based 4 → SAM 的 1-based 5
    assert record.cigar == alignment.cigar
    assert record.sequence == "ACGTACGTAC"
    assert record.qualities == "I" * 10
    assert record.tag("NM") == "0"
    assert record.tag("MD") == "10"


def test_sam_record_on_negative_strand_flips_sequence_and_quality() -> None:
    """负链记录的 ``SEQ`` 必须是与参考同向的那条（反向互补），质量串随之反向。"""
    reference = _reference_of("ACGTACGTAC")
    read = Read(name="r2", sequence="GTACGTACGT", qualities="ABCDEFGHIJ")
    alignment = _alignment(
        "10M", 0, query_length=10, reference_length=10, strand=-1
    )
    record = to_sam_record(read, alignment, reference)
    assert record.flag & 0x10  # 反向标记
    assert record.sequence == _revcomp(read.sequence)
    assert record.qualities == read.qualities[::-1]


def test_sam_record_marks_read2_when_paired() -> None:
    reference = _reference_of("ACGTACGTAC")
    alignment = _alignment("10M", 0, query_length=10, reference_length=10)
    read = Read(name="r3", sequence="ACGTACGTAC", is_read2=True)
    record = to_sam_record(read, alignment, reference, paired=True)
    assert record.flag & 0x1  # 双端
    assert record.flag & 0x80  # read2
    assert not record.flag & 0x40


def test_sam_record_without_quality_uses_star() -> None:
    reference = _reference_of("ACGTACGTAC")
    alignment = _alignment("10M", 0, query_length=10, reference_length=10)
    record = to_sam_record(Read(name="r4", sequence="ACGTACGTAC"), alignment, reference)
    assert record.qualities == "*"


# ---------------------------------------------------------------------------
# 落盘与读回
# ---------------------------------------------------------------------------


def _small_mapper(sequence: str, *, seq_id: str = "chr", k: int = 12) -> Mapper:
    reference = _reference_of(sequence, seq_id)
    index = ReferenceIndex.build(reference, k=k, step=1)
    return Mapper(
        reference=reference,
        index=index,
        params=MappingParams(seed_length=k, step=1),
    )


def test_write_and_read_back_keeps_the_alignment(tmp_path: Path) -> None:
    sequence = _random_sequence(2000)
    mapper = _small_mapper(sequence)
    reads = [
        Read(name="a", sequence=sequence[500:600], qualities="I" * 100),
        Read(name="b", sequence=sequence[900:1000], qualities="J" * 100),
    ]
    alignments = list(mapper.map_reads(reads))
    path = tmp_path / "out.sam"
    written = write_mapped_sam(path, mapper.reference, reads, alignments)
    assert written == 2

    header, records = read_sam(path)
    assert header.reference_length("chr") == 2000
    assert [record.query_name for record in records] == ["a", "b"]
    expected_starts = {"a": 500, "b": 900}
    for record in records:
        assert record.position - 1 == expected_starts[record.query_name]
        assert str(record.cigar) == "100M"
        assert record.sequence == sequence[record.position - 1 : record.position - 1 + 100]
        assert record.tag("NM") == "0"
        assert record.tag("MD") == "100"


def test_unmapped_reads_are_not_written(tmp_path: Path) -> None:
    sequence = _random_sequence(2000)
    mapper = _small_mapper(sequence)
    reads = [
        Read(name="mapped", sequence=sequence[500:600]),
        Read(name="foreign", sequence=_random_sequence(100, seed=999)),
    ]
    alignments = list(mapper.map_reads(reads))
    assert alignments[1] is None
    path = tmp_path / "out.sam"
    assert write_mapped_sam(path, mapper.reference, reads, alignments) == 1
    _, records = read_sam(path)
    assert [record.query_name for record in records] == ["mapped"]


# ---------------------------------------------------------------------------
# 端到端：真实参考 + 模拟 reads → SAM
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _REAL_GENBANK.exists(), reason="真实公开参考数据未随仓库提供")
def test_end_to_end_sam_on_real_reference(tmp_path: Path) -> None:
    """phiX174 上模拟三类 read（正向、负向带 1 错配、接头读通），比对后写 SAM 再读回核对。"""
    reference = read_genbank(_REAL_GENBANK)
    sequence = reference.get("NC_001422").sequence
    index = ReferenceIndex.build(reference, k=16, step=4)
    mapper = Mapper(
        reference=reference,
        index=index,
        params=MappingParams(seed_length=16, step=4),
    )

    read_length = 100
    reads: list[Read] = []
    truth: list[tuple[int, int, int, int]] = []  # (起点, 链, 错配数, 软剪裁数)
    for order in range(60):
        group = order % 3
        start = 10 + (order * 61) % (len(sequence) - read_length - 20)
        piece = sequence[start : start + read_length]
        if group == 1:
            piece = _revcomp(piece)
            offset = 40
            piece = piece[:offset] + (
                "A" if piece[offset] != "A" else "C"
            ) + piece[offset + 1 :]
            reads.append(Read(name=f"sim-{order}", sequence=piece))
            truth.append((start, -1, 1, 0))
        elif group == 2:
            prefix = "".join(
                next(base for base in "ACGT" if base != sequence[start - 8 + offset])
                for offset in range(8)
            )
            reads.append(Read(name=f"sim-{order}", sequence=prefix + piece))
            truth.append((start, 1, 0, 8))
        else:
            reads.append(Read(name=f"sim-{order}", sequence=piece))
            truth.append((start, 1, 0, 0))

    alignments = list(mapper.map_reads(reads))
    path = tmp_path / "phiX174.sam"
    assert write_mapped_sam(path, reference, reads, alignments) == len(reads)

    header, records = read_sam(path)
    assert header.reference_length("NC_001422") == 5386
    assert len(records) == len(reads)
    for record, read, (start, strand, mismatches, clipped) in zip(records, reads, truth):
        assert record.query_name == read.name
        assert record.reference_name == "NC_001422"
        assert record.position - 1 == start, f"{read.name} 起点错"
        assert bool(record.flag & 0x10) is (strand == -1), f"{read.name} 链方向错"
        assert record.cigar.query_length == len(read.sequence)
        assert record.tag("NM") == str(mismatches), f"{read.name} NM 错"
        # 端到端的取序列方向也要对：负链存的是反向互补
        assert record.sequence == (
            _revcomp(read.sequence) if strand == -1 else read.sequence
        )
        assert record.tag("MD") is not None
        if clipped:
            assert record.cigar.ops[0].op == "S"
            assert record.cigar.ops[0].length == clipped
