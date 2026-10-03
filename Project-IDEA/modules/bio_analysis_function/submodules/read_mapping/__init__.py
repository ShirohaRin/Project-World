"""参考比对（read mapping）：把 reads 比到参考序列上。

这是参考比对与变异检测线里**第一个真正的算法**，也是最难的一块：breseq 整条链的入口
就是它（上游用的是 bowtie2）。我们的目标不是复刻 bowtie2 的比对选择，而是给出
**足够好且行为可解释**的比对，让下游的变异调用能在合理容差内复现上游结论
（验收口径见算法清单 5.5.3）。

分片推进（每一步都带自己的测试，做完一片再进下一片）：

| 分片 | 内容 | 状态 |
| --- | --- | --- |
| A | **k-mer 种子索引**（`index.py`） | 已完成 |
| B | **种子投票与候选对角线**（`mapper.py`） | 已完成 |
| C | **带内比对（允许短 indel）与 CIGAR**（`dp.py` + `mapper.py`） | 已完成 |
| D | **软剪裁与末端处理**（`dp.py` 的局部比对 + `mapper.py` 的 `max_clip`） | 已完成 |
| E | **多重命中、映射质量（MAPQ）与 SAM 输出**（`mapper.py` + `sam_output.py`） | 已完成 |
| F | **端到端对拍与性能实测**（`tests/test_mapper_accuracy.py` + `tools/benchmark.py`） | 已完成 |
"""

from __future__ import annotations

from .dp import BandedAlignment, align_banded
from .index import (
    DEFAULT_MAX_HITS,
    DEFAULT_SEED_LENGTH,
    DEFAULT_STEP,
    ReferenceIndex,
    SeedIndex,
    decode_kmer,
    encode_kmer,
    reverse_complement_kmer,
)
from .mapper import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_CLIP,
    DEFAULT_MAX_INDEL,
    DEFAULT_MAX_MISMATCHES,
    Alignment,
    Mapper,
    MappingParams,
    Read,
)
from .sam_output import (
    build_md_tag,
    iter_sam_records,
    to_sam_record,
    write_mapped_sam,
)

__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_CLIP",
    "DEFAULT_MAX_HITS",
    "DEFAULT_MAX_INDEL",
    "DEFAULT_MAX_MISMATCHES",
    "DEFAULT_SEED_LENGTH",
    "DEFAULT_STEP",
    "Alignment",
    "BandedAlignment",
    "Mapper",
    "MappingParams",
    "Read",
    "ReferenceIndex",
    "SeedIndex",
    "align_banded",
    "build_md_tag",
    "decode_kmer",
    "encode_kmer",
    "iter_sam_records",
    "reverse_complement_kmer",
    "to_sam_record",
    "write_mapped_sam",
]
