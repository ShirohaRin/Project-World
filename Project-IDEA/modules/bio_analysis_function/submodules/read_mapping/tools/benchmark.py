"""参考比对的性能实测：建索引 + 比对吞吐 + 内存（Python 实现）。

用法（在 Project-IDEA 目录下）：

    python modules/bio_analysis_function/submodules/read_mapping/tools/benchmark.py
    python modules/bio_analysis_function/submodules/read_mapping/tools/benchmark.py --reference-size 4600000 --reads 5000
    python modules/bio_analysis_function/submodules/read_mapping/tools/benchmark.py --step 1   # 逐位建索引的对照

为什么要用合成数据：仓库里固定的真实参考只有 phiX174（5.4 kb），而本线要回答的性能问题
全在"细菌级参考（几 Mbp）+ 短读"这个区间——参考大小直接决定索引的内存与建表时间，
小参考上量不出任何东西。合成参考用固定种子生成，因此每次跑的数字可以直接对比。

给出的数字有三类：

1. **建索引**：耗时、窗口数、保留的 k-mer 数、被剔除的重复 k-mer 数，以及
   tracemalloc 量到的**保留内存**与**峰值内存**（前者是索引本身，后者含建表过程的临时量）；
2. **比对**：总耗时、每条 read 的平均耗时（毫秒）、每秒处理的 read 数；
3. **正确性**：模拟 read 的真值（起点、链方向、错配数）已知，顺带报召回、起点正确率与
   MAPQ 分布——性能必须在"还能比对得对"的前提下才有意义。起点正确率分两档，
   因为落在 read 两端的错配会被**合法地剪掉**（见 `dp.py` 的打分说明），
   那种情形下"起点前移一位 + ``1S``"才是最优解，不该当成错误。

只测 Python 实现。原生实现尚未做，见 `read_mapping.md` 的"已知限制与待办"。
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from modules.bio_analysis_function.common.reference_io import (  # noqa: E402
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.read_mapping import (  # noqa: E402
    Mapper,
    MappingParams,
    Read,
    ReferenceIndex,
)

_BASES = "ACGT"
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _revcomp(text: str) -> str:
    return text.translate(_COMPLEMENT)[::-1]


def _different_base(base: str, rng: random.Random) -> str:
    return rng.choice([candidate for candidate in _BASES if candidate != base])


def make_reference(size: int, seed: int) -> str:
    """合成参考：固定种子的随机序列。"""
    return "".join(random.Random(seed).choices(_BASES, k=size))


def make_reads(
    reference: str, count: int, length: int, mismatches: int, seed: int
) -> tuple[list[Read], list[tuple[int, int, int]]]:
    """从参考上切 reads 并施加已知错配；真值 = (起点, 链方向, 错配数)。

    替换一律换成**另一个**碱基，保证实际错配数与真值一致。
    """
    rng = random.Random(seed)
    reads: list[Read] = []
    truths: list[tuple[int, int, int]] = []
    for index in range(count):
        start = rng.randrange(0, len(reference) - length)
        strand = 1 if index % 2 == 0 else -1
        piece = reference[start : start + length]
        if strand == -1:
            piece = _revcomp(piece)
        if mismatches:
            characters = list(piece)
            for offset in rng.sample(range(length), mismatches):
                characters[offset] = _different_base(characters[offset], rng)
            piece = "".join(characters)
        reads.append(Read(name=f"sim-{index}", sequence=piece))
        truths.append((start, strand, mismatches))
    return reads, truths


def format_seconds(value: float) -> str:
    return f"{value:.2f}s" if value >= 1 else f"{value * 1000:.0f}ms"


def format_megabytes(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description="read_mapping（Python 实现）的性能实测")
    parser.add_argument("--reference-size", type=int, default=1_000_000, help="参考长度，默认 1 Mbp")
    parser.add_argument("--reads", type=int, default=1000, help="模拟 read 条数，默认 1000")
    parser.add_argument("--read-length", type=int, default=150, help="读长，默认 150")
    parser.add_argument("--mismatches", type=int, default=1, help="每条 read 的错配数，默认 1")
    parser.add_argument("--k", type=int, default=16, help="种子长度，默认 16")
    parser.add_argument("--step", type=int, default=4, help="建索引步长，默认 4（1 = 逐位建）")
    parser.add_argument("--seed", type=int, default=20260928, help="合成数据种子")
    args = parser.parse_args()

    print(
        f"参数：参考 {args.reference_size} bp，reads {args.reads} 条 × {args.read_length} bp，"
        f"每条 {args.mismatches} 个错配，k={args.k}，step={args.step}"
    )

    started = time.perf_counter()
    sequence = make_reference(args.reference_size, args.seed)
    print(f"生成合成参考：{format_seconds(time.perf_counter() - started)}", flush=True)

    reference = ReferenceSet.of([ReferenceSequence(seq_id="chr", sequence=sequence)])

    # 时间与内存分两遍量：tracemalloc 开着会让执行慢好几倍，混在一起量出来的耗时不能看。
    started = time.perf_counter()
    index = ReferenceIndex.build(reference, k=args.k, step=args.step)
    build_elapsed = time.perf_counter() - started

    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    traced_index = ReferenceIndex.build(reference, k=args.k, step=args.step)
    retained = tracemalloc.get_traced_memory()[0] - baseline
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    stats = next(iter(index.stats()))
    windows = int(stats["window_count"])
    print(f"\n建索引（k={args.k}、step={args.step}）：")
    print(f"  耗时        {format_seconds(build_elapsed)}（未开内存跟踪）")
    print(f"  窗口数      {windows:,}（跳过含 N 的窗口 {stats['skipped_windows']:,}）")
    print(f"  保留 k-mer  {stats['kmer_count']:,}，位置 {stats['positions']:,}")
    print(f"  剔除重复     {stats['pruned_kmers']:,} 条 k-mer（单条位置数 > {MappingParams().max_hits}）")
    print(f"  索引内存    {format_megabytes(retained)}（{retained / windows:.0f} 字节/窗口），"
          f"峰值 {format_megabytes(peak)}")
    assert traced_index == index, "同一份参考两次建索引的结果不一致"

    reads, truths = make_reads(sequence, args.reads, args.read_length, args.mismatches, args.seed + 1)
    mapper = Mapper(reference=reference, index=index, params=MappingParams(seed_length=args.k, step=args.step))

    started = time.perf_counter()
    alignments = [mapper.map_read(read) for read in reads]
    map_elapsed = time.perf_counter() - started

    tracemalloc.start()
    traced_alignments = [mapper.map_read(read) for read in reads]
    map_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert traced_alignments == alignments, "同一批 read 两次比对的结果不一致"

    mapped = [alignment for alignment in alignments if alignment is not None]

    # 真值核对分两档，因为**末端的错配会被合法地剪掉**：DP 的打分里留一个孤立的末端错配
    # 得 -1、剪掉得 0，于是"起点跟着前移一位 + 1S"或"末尾 1S"才是它给出的最优解。
    # 所以 ① 起点与链方向（把前端的剪裁折算进去）应当全部吻合；② 错配数也吻合的那些，
    # 只统计错配不在两端的 read。
    placed = 0
    exact = 0
    for alignment, (start, strand, mismatches) in zip(alignments, truths):
        if alignment is None or alignment.strand != strand:
            continue
        first_op = alignment.cigar.ops[0]
        leading = first_op.length if first_op.op == "S" else 0
        if alignment.reference_start != start + leading:
            continue
        placed += 1
        if alignment.mismatches == mismatches:
            exact += 1

    mapq: dict[int, int] = {}
    for alignment in mapped:
        mapq[alignment.mapq] = mapq.get(alignment.mapq, 0) + 1

    print(f"\n比对 {len(reads):,} 条 read（每条真值 {args.mismatches} 个错配）：")
    print(f"  总耗时      {format_seconds(map_elapsed)}")
    print(f"  单条平均    {map_elapsed / len(reads) * 1000:.2f} ms")
    print(f"  吞吐        {len(reads) / map_elapsed:,.0f} reads/s")
    print(f"  峰值内存    {format_megabytes(map_peak)}（开跟踪单跑一遍；耗时那行未开跟踪）")
    print(f"  比对上的    {len(mapped):,}/{len(reads):,}")
    print(f"  起点/链正确  {placed:,}（{placed / len(reads) * 100:.2f}%，已折算两端剪裁）")
    print(f"  错配也吻合  {exact:,}（{exact / len(reads) * 100:.2f}%，末端错配被剪的不计）")
    print(f"  MAPQ 分布   {dict(sorted(mapq.items(), reverse=True))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
