"""breseq 工作流的编排入口：阶段 A（预扫描）+ 阶段 B（比对 → 调用 → 产物）。

**两段式的分界与 fastp 工作流同一位置（语言/职责边界），但这条线的阶段 B 不是"单遍"**：
比对一次落盘成 SAM，后面的步骤都从 SAM 走。理由两条：

1. **规模**：细菌基因组 100× 覆盖在 Python 单线程里是小时级时间与 GB 级内存，把整条链塞进
   一次读入只会同时爆掉内存与耐心；分阶段至少让每步可以单独重跑、单独对拍。
2. **契约**：算法清单 5.5.3 第 1 条要求"接受外部 SAM/BAM"。以 SAM 为交接之后，
   用户拿 bowtie2 的结果也能从后半段（错误率 → 调用 → 注释 → `.gd`）接进来，
   这也正是与上游做阶段级对拍的唯一手段。

阶段与对应实现：

| 阶段 | 做什么 | 落在哪 |
| --- | --- | --- |
| A | 读参考（GenBank / FASTA） | `common.reference_io` |
| A | 扫一遍 FASTQ：读长、条数、碱基数 | `common.fastq.read_fastq` |
| B | 建索引 + 逐条比对 + 写 SAM | `submodules.read_mapping` |
| B | read 端裁剪表（可选） | `submodules.consensus_calling.build_trimming` |
| B | 错误率重校准 → 共识档 + 多态档调用 | `submodules.consensus_calling` |
| B | 证据 → 突变（RA 线） | `breseq/variants.py` |
| B | 突变注释 → `.gd` 属性与注释表 | `submodules.mutation_annotation` |
| B | 写 `.gd` / 注释表 / 摘要表 | `common.genome_diff` + 本文件 |

**本版范围**：只接 **RA 线**（碱基替换；共识档 + 多态档）。MC / JC 两条证据线与
`RA → 短 INS/DEL`、`MC+JC → DEL` 那些规则还没进来，见 `breseq_workflow.md` 的「已知边界」。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from ...bio_analysis_function.common.alignment_io import SamHeader, SamWriter
from ...bio_analysis_function.common.fastq import read_fastq
from ...bio_analysis_function.common.genome_diff import (
    assemble_diff,
    write_genome_diff,
)
from ...bio_analysis_function.common.reference_io import (
    ReferenceSet,
    read_fasta,
    read_genbank,
)
from ...bio_analysis_function.submodules.consensus_calling import (
    build_error_rates,
    build_trimming,
    call_variants,
)
from ...bio_analysis_function.submodules.mutation_annotation import (
    Substitution,
    annotate_variants,
    iter_annotation_rows,
    write_annotation_table,
)
from ...bio_analysis_function.submodules.read_mapping import (
    Mapper,
    Read,
    ReferenceIndex,
    to_sam_record,
)
from .config import BreseqConfig, BreseqOutcome, BreseqScanReport, SUMMARY_COLUMNS
from .report import write_breseq_reports
from .variants import BASE_SUBSTITUTION, Variant, mutation_entries, variants_from_calls

__all__ = [
    "SUMMARY_COLUMNS",
    "load_reference",
    "run_breseq_workflow",
]

_GENBANK_SUFFIXES = (".gb", ".gbk", ".genbank", ".gbff")
_FASTA_SUFFIXES = (".fa", ".fasta", ".fna", ".fas", ".ffn", ".frn")


def load_reference(paths: Iterable[str | Path]) -> ReferenceSet:
    """按后缀读参考：GenBank 或 FASTA；多个文件按给定顺序拼成一个参考集合。"""
    sequences = []
    for item in paths:
        path = Path(item)
        suffix = path.suffix.lower()
        if suffix in _GENBANK_SUFFIXES:
            loaded = read_genbank(path)
        elif suffix in _FASTA_SUFFIXES:
            loaded = read_fasta(path)
        else:
            raise ValueError(
                f"认不出参考文件的格式（按后缀判断）：{path.name}。"
                f"GenBank 给 {_GENBANK_SUFFIXES}，FASTA 给 {_FASTA_SUFFIXES}。"
            )
        sequences.extend(loaded)
    if not sequences:
        raise ValueError("参考文件里没有读到任何序列。")
    return ReferenceSet.of(sequences)


def _iter_reads(
    path: Path, *, is_read2: bool, limit: int | None
) -> Iterator[Read]:
    """把 FASTQ 逐条转成比对器的读段（序列统一大写，质量按 SAM 的 ASCII 口径带下去）。"""
    for index, record in enumerate(read_fastq(path, limit=limit)):
        sequence = record.sequence.decode("ascii").upper()
        if not sequence:
            continue
        # 名字是 str；序列与质量是 bytes（按 ASCII 解出来就是 SAM 的 SEQ / QUAL 口径）。
        name = record.name.strip() or f"read{index + 1}"
        yield Read(
            name=name,
            sequence=sequence,
            qualities=record.quality.decode("ascii"),
            is_read2=is_read2,
        )


def _input_files(config: BreseqConfig) -> tuple[tuple[Path, bool], ...]:
    """(文件, 是否 read2) 的列表；单端只有一项。"""
    if config.read2 is None:
        return ((config.read1, False),)
    return ((config.read1, False), (config.read2, True))


@dataclass(frozen=True, slots=True)
class _ReadStats:
    """阶段 A 的后半：读进来多少条、多长。"""

    reads: int
    shortest: int
    longest: int
    bases: int
    cropped: bool


def _scan_reads(config: BreseqConfig) -> _ReadStats:
    """流式扫一遍 FASTQ，数条数、读长与碱基数。"""
    total = bases = 0
    shortest = 0
    longest = 0
    cropped = False
    for path, _is_read2 in _input_files(config):
        if not path.exists():
            raise FileNotFoundError(f"读段文件不存在：{path}")
        count = 0
        for record in read_fastq(path, limit=config.max_reads):
            length = len(record.sequence)
            total += 1
            count += 1
            bases += length
            shortest = length if shortest == 0 else min(shortest, length)
            longest = max(longest, length)
        if config.max_reads is not None and count >= config.max_reads:
            cropped = True
    return _ReadStats(
        reads=total, shortest=shortest, longest=longest, bases=bases, cropped=cropped
    )


@dataclass(frozen=True, slots=True)
class _MappingStats:
    """比对阶段的计数（写 SAM 的同时数出来）。"""

    mapped: int = 0
    unmapped: int = 0
    unique: int = 0
    repeat: int = 0
    aligned_bases: int = 0


def _map_reads(config: BreseqConfig, reference: ReferenceSet, mapper: Mapper) -> _MappingStats:
    """逐条比对并写出 SAM（只写比对上的记录）。"""
    header = SamHeader.from_sequences(
        (sequence.seq_id, sequence.length) for sequence in reference
    )
    mapped = unmapped = unique = repeat = aligned_bases = 0
    config.sam_path.parent.mkdir(parents=True, exist_ok=True)
    with SamWriter(config.sam_path, header) as writer:
        for path, is_read2 in _input_files(config):
            for read in _iter_reads(path, is_read2=is_read2, limit=config.max_reads):
                alignment = mapper.map_read(read)
                if alignment is None:
                    unmapped += 1
                    continue
                writer.write(
                    to_sam_record(read, alignment, reference, paired=config.paired)
                )
                mapped += 1
                aligned_bases += alignment.aligned_length
                if alignment.mapq == 60:
                    unique += 1
                elif alignment.mapq == 0:
                    repeat += 1
    return _MappingStats(
        mapped=mapped,
        unmapped=unmapped,
        unique=unique,
        repeat=repeat,
        aligned_bases=aligned_bases,
    )


def _annotation_text(row: dict[str, str]) -> str:
    """把注释表的一行压成一句人读的话（写进 `.gd` 的 `annotation` 属性）。"""
    piece = " ".join(
        part for part in (row.get("gene"), row.get("effect"), row.get("description")) if part
    )
    return piece or row.get("kind", "")


def _call_variants(
    config: BreseqConfig, reference: ReferenceSet
) -> tuple[tuple[Variant, ...], tuple]:
    """从 SAM 调出突变、注释它们，并把注释回填进突变（`Variant.annotation`）。"""
    trimming = build_trimming(reference) if config.trim_read_ends else None
    rates = build_error_rates(
        reference,
        config.sam_path,
        default_quality=config.default_quality,
        trimming=trimming,
    )
    calls = call_variants(
        reference,
        config.sam_path,
        rates=rates,
        consensus=config.consensus,
        polymorphism=config.polymorphism,
        default_quality=config.default_quality,
        trimming=trimming,
    )
    variants = variants_from_calls(calls)

    substitutions = tuple(
        Substitution(
            seq_id=variant.seq_id,
            position=variant.position,
            reference_base=variant.reference_base,
            call_base=variant.call_base,
        )
        for variant in variants
        if variant.type == BASE_SUBSTITUTION
    )
    annotations = annotate_variants(reference, substitutions) if substitutions else ()

    texts: dict[tuple[str, int], list[str]] = {}
    for row in iter_annotation_rows(annotations):
        key = (row["seq_id"], int(row["position"]))
        texts.setdefault(key, []).append(_annotation_text(row))

    enriched = tuple(
        replace(variant, annotation="; ".join(texts.get((variant.seq_id, variant.position), ())))
        for variant in variants
    )
    return enriched, annotations


def _write_summary(path: Path, variants: Iterable[Variant]) -> int:
    """写摘要表（一条突变一行）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(SUMMARY_COLUMNS)
        for variant in variants:
            writer.writerow(
                (
                    variant.seq_id,
                    variant.position,
                    variant.type,
                    variant.reference_base,
                    variant.call_base,
                    f"{variant.frequency:.6g}",
                    variant.prediction,
                    f"{variant.score:.6g}",
                    variant.annotation,
                )
            )
            rows += 1
    return rows


def run_breseq_workflow(
    config: BreseqConfig,
    *,
    html_path: str | Path | None = None,
    log_dir: str | Path | None = None,
) -> BreseqOutcome:
    """跑一次完整的参考比对与变异检测（本版只含 RA 线与注释，见模块文档）。

    ``html_path`` 给用户看的那份报告；``log_dir`` 里写 JSON 日志（`breseq_workflow.json`）。
    **不给就不写**——与 fastp 工作流同一约定。
    """
    reference = load_reference(config.reference)
    reads = _scan_reads(config)
    scan = BreseqScanReport(
        seq_ids=reference.ids,
        reference_length=reference.total_length,
        feature_count=sum(len(sequence.features) for sequence in reference),
        reads=reads.reads,
        read_pairs=config.paired,
        shortest_read=reads.shortest,
        longest_read=reads.longest,
        mean_read_length=(reads.bases / reads.reads) if reads.reads else 0.0,
        bases=reads.bases,
        cropped=reads.cropped,
    )

    index = ReferenceIndex.build(
        reference,
        k=config.mapping.seed_length,
        step=config.mapping.step,
        max_hits=config.mapping.max_hits,
    )
    mapper = Mapper(reference=reference, index=index, params=config.mapping)
    stats = _map_reads(config, reference, mapper)

    variants, annotations = _call_variants(config, reference)
    header_entries: list[tuple[str, str]] = [("PROGRAM", config.program)]
    header_entries.extend(("REFSEQ", str(path)) for path in config.reference)
    header_entries.extend(
        ("READSEQ", str(path)) for path, _is_read2 in _input_files(config)
    )
    diff = assemble_diff(mutation_entries(variants), header_entries=header_entries)
    write_genome_diff(config.diff_path, diff)
    write_annotation_table(config.annotation_path, annotations)
    _write_summary(config.summary_path, variants)

    outcome = BreseqOutcome(
        config=config,
        scan=scan,
        mapped_reads=stats.mapped,
        unmapped_reads=stats.unmapped,
        unique_reads=stats.unique,
        repeat_reads=stats.repeat,
        variants=variants,
        outputs=(
            config.sam_path,
            config.diff_path,
            config.annotation_path,
            config.summary_path,
        ),
    )
    if html_path is None and log_dir is None:
        return outcome
    written_html, written_log = write_breseq_reports(
        outcome, html_path=html_path, log_dir=log_dir
    )
    return replace(outcome, html_path=written_html, log_path=written_log)
