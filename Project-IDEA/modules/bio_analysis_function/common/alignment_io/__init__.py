"""比对结果读写：SAM（文本比对记录）。

它属于模块公共层的工具：自身不回答生物学问题，只是"比对结果"这件事的对出与对入契约，
因此**没有算法广场入口**。

- **写**：我们的比对器产出 SAM，IGV 等工具能直接看；
- **读**：别人的比对结果（含上游 breseq 用 bowtie2 跑出来的）能读进来，
  用同一套下游做**阶段级对拍**——这是"不承诺与上游逐位一致"之下唯一可靠的验证手段。

BAM（同一套记录的二进制形态）不在本层，见 `alignment_io.md` 的待办。
"""

from __future__ import annotations

from .sam import (
    AlignmentFormatError,
    Cigar,
    CigarOp,
    SamHeader,
    SamReader,
    SamRecord,
    SamSequence,
    SamWriter,
    open_sam,
    parse_cigar,
    parse_sam_record,
    read_sam,
    write_sam,
)

__all__ = [
    "AlignmentFormatError",
    "Cigar",
    "CigarOp",
    "SamHeader",
    "SamReader",
    "SamRecord",
    "SamSequence",
    "SamWriter",
    "open_sam",
    "parse_cigar",
    "parse_sam_record",
    "read_sam",
    "write_sam",
]
