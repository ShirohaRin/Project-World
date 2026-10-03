"""模型本身的用例（手工构造对象，不经过解析）。

解析与写回的用例在 `test_text.py`；这里只钉"便捷方法怎么工作"：头的取值、频率的数值化、
证据归类、按编号取记录。
"""

from __future__ import annotations

from types import MappingProxyType

from modules.bio_analysis_function.common.genome_diff import (
    GD_VERSION,
    GenomeDiff,
    GenomeDiffHeader,
    GenomeDiffRecord,
)


def _record(
    record_type: str,
    record_id: str,
    *,
    evidence: tuple[str, ...] = (),
    position: int = 100,
    fields: tuple[str, ...] = (),
    attributes: dict[str, str] | None = None,
) -> GenomeDiffRecord:
    return GenomeDiffRecord(
        type=record_type,
        id=record_id,
        evidence=evidence,
        seq_id="chr",
        position=position,
        fields=fields,
        attributes=MappingProxyType(attributes or {}),
    )


def test_header_collects_repeated_keys_in_order() -> None:
    header = GenomeDiffHeader(
        version="1.0",
        entries=(
            ("CREATED", ("13:24:43 25 Feb 2020",)),
            ("READSEQ", ("a.fq",)),
            ("READSEQ", ("b.fq",)),
            ("MAPPED-BASES", ("434491437",)),
        ),
    )

    assert header.version == "1.0"
    assert header.values_of("READSEQ") == ("a.fq", "b.fq")
    assert header.value_of("READSEQ") == "a.fq"
    assert header.value_of("MAPPED-BASES") == "434491437"
    assert header.value_of("NOPE") is None
    assert header.keys.count("READSEQ") == 2


def test_default_header_uses_the_current_version() -> None:
    assert GenomeDiffHeader().version == GD_VERSION
    assert GenomeDiff(header=GenomeDiffHeader()).records == ()


def test_frequency_is_read_as_a_number_and_na_is_tolerated() -> None:
    assert _record("SNP", "1", attributes={"frequency": "1.16257668e-01"}).frequency == (
        1.16257668e-01
    )
    assert _record("SNP", "1", attributes={"frequency": "1"}).frequency == 1.0
    assert _record("JC", "1", attributes={"frequency": "NA"}).frequency is None
    assert _record("SNP", "1").frequency is None


def test_attribute_and_evidence_classification() -> None:
    mutation = _record("SNP", "5", evidence=("37",), fields=("T",))
    evidence = _record("RA", "37", fields=("0", "C", "T"))

    assert mutation.is_evidence is False
    assert evidence.is_evidence is True
    assert _record("MC", "62").is_evidence is True
    assert _record("ZZZ", "1").is_evidence is False  # 不认识的类型不算证据，但也不丢


def test_attribute_default_when_missing() -> None:
    record = _record("SNP", "5", attributes={"major_base": "C"})

    assert record.attribute("major_base") == "C"
    assert record.attribute("minor_base") is None
    assert record.attribute("minor_base", "?") == "?"


def test_genome_diff_splits_records_and_resolves_evidence() -> None:
    mutation = _record("DEL", "1", evidence=("61", "71"), fields=("776",))
    del_evidence = _record("MC", "61", position=257908, fields=("258683", "767", "0"))
    junction_evidence = _record("JC", "71", position=1, fields=("1",))
    other = _record("SNP", "2", evidence=("missing",), fields=("A",))
    diff = GenomeDiff(
        header=GenomeDiffHeader(),
        records=(mutation, del_evidence, junction_evidence, other),
    )

    assert [record.id for record in diff.mutations()] == ["1", "2"]
    assert [record.id for record in diff.evidence()] == ["61", "71"]
    assert diff.by_id("71") is junction_evidence
    assert diff.by_id("nope") is None
    # 引用的证据齐全时按顺序取回；引用不存在时跳过而不是报错（文件可能只截了一段）
    assert diff.evidence_of(mutation) == (del_evidence, junction_evidence)
    assert diff.evidence_of(other) == ()
