"""分片 F：端到端对拍——成规模的混合读集，查召回、精确度、误报与 MAPQ 校准。

`test_mapper.py` 里是一件事一个用例的定点测试；这里造的是**成规模的混合读集**，
用**默认参数**（k=16、step=4、错配 ≤4、indel ≤3、剪裁 ≤10）跑一遍，看整体表现：

- **召回**：真值明确、应当比上的 read，一条都不许漏；
- **精确**：起点、链方向、错配/插入/缺失/软剪裁数逐条等于真值；
- **误报**：随机外来序列一条都不许比上（precision）；
- **MAPQ 校准**：mapper 声称"唯一"（``mapq == 60``）的那些 read，用**独立于 mapper 的
  暴力扫描**（逐个窗口数错配）复核。这是分片 F 要盯的风险点——候选被截断或重复 k-mer
  被整条剔除时，MAPQ 60 有可能过于自信（见 `read_mapping.md` 第 4 节第 8 条）。
- **稀疏索引的代价**：把连续完全匹配段压到比 step 还短，量出"只降灵敏度"的那部分灵敏度
  到底降在哪里；**SAM 放量回读**：写出后读回来，用 CIGAR + 参考独立重算 NM / MD 逐条核对，
  补上"与下游对拍"里不依赖外部工具的那一半。

顺带钉住两条**已知限制**（用实测守住当前行为，不假装它们不存在）：高拷贝重复区的 read
会整条比不上（重复 k-mer 被剔除），以及落在 read 两端的错配会被合法地剪掉。

参考序列用**固定种子随机生成**而不是真实基因组：真实细菌参考有几 Mbp、放不进仓库，
而"真值已知"恰恰要求我完全掌握参考的构造，随机序列最容易做到这件事。真实公开参考
（phiX174）上的对拍在 `test_mapper.py` 里另有一组。

模拟 read 时替换碱基一律换成**与原来不同**的碱基，否则"换成同一个碱基"会让实际错配数
小于预期，测试会变成假通过——这条纪律沿用 `test_mapper.py`。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from modules.bio_analysis_function.common.alignment_io import read_sam
from modules.bio_analysis_function.common.reference_io import (
    ReferenceSequence,
    ReferenceSet,
)
from modules.bio_analysis_function.submodules.read_mapping import (
    Alignment,
    Mapper,
    MappingParams,
    Read,
    ReferenceIndex,
    write_mapped_sam,
)

_SEED = 20260928
_REFERENCE_LENGTH = 20_000
_READ_LENGTH = 100
_BASES = "ACGT"
_COMPLEMENT = str.maketrans("ACGT", "TGCA")

#: 施加变异的偏移池：都离 read 两端足够远，末端剪裁不可能"顺手"把变异消掉。
_POOL_ANY = tuple(range(20, 85))
#: 与 indel 混用时，替换只落在 indel 的两侧，避免"替换被当成 indel 的一部分"这种并列情形。
_POOL_LEFT = tuple(range(15, 45))
_POOL_RIGHT = tuple(range(55, 85))


def _revcomp(text: str) -> str:
    """与实现独立的字符串反向互补，用于构造真值与核对。"""
    return text.translate(_COMPLEMENT)[::-1]


def _different_base(base: str, rng: random.Random) -> str:
    """换成一个**不同**的碱基（保证真的产生错配）。"""
    return rng.choice([candidate for candidate in _BASES if candidate != base])


def _random_sequence(length: int, seed: int) -> str:
    return "".join(random.Random(seed).choices(_BASES, k=length))


def _mapper(sequence: str, *, step: int = 4, seq_id: str = "chr") -> Mapper:
    """用**默认比对参数**建比对器（k=16、step 可调，错配 4、indel 3、剪裁 10）。"""
    reference = ReferenceSet.of([ReferenceSequence(seq_id=seq_id, sequence=sequence)])
    index = ReferenceIndex.build(reference, k=16, step=step)
    return Mapper(reference=reference, index=index, params=MappingParams(step=step))


def _ungapped_best(reference: str, query: str) -> tuple[int, int]:
    """独立于 mapper 的暴力扫描：返回 ``(最小错配数, 达到它的窗口个数)``。

    正反两链都扫，只看**无缺口**比对——它与 mapper 的内部实现（种子投票、带状 DP、
    候选截断）没有任何共享逻辑，是一个真正的第三方判据。
    """
    best = -1
    count = 0
    length = len(query)
    for strand_query in (query, _revcomp(query)):
        for start in range(0, len(reference) - length + 1):
            mismatches = sum(
                1
                for left, right in zip(strand_query, reference[start : start + length])
                if left != right
            )
            if best < 0 or mismatches < best:
                best, count = mismatches, 1
            elif mismatches == best:
                count += 1
    return best, count


def _verify_alignment(reference: str, sequence: str, alignment: Alignment) -> dict[int, int]:
    """按 CIGAR 独立复核一次比对，返回"read 下标 → 参考坐标"的对应表。

    这是**不依赖 mapper 内部状态**的第三方检查：拿 CIGAR 去消费两条序列，逐位比较，
    再核对 mapper 报出来的错配/插入/缺失/剪裁数是否与 CIGAR 一致。下游（SAM、变异调用）
    依赖的正是这些数字与 CIGAR 的自洽性，所以它值得单独验一遍。
    """
    query = sequence if alignment.strand == 1 else _revcomp(sequence)
    mapping: dict[int, int] = {}
    row, column = 0, alignment.reference_start
    mismatches = insertions = deletions = clipped = 0
    for op in alignment.cigar.ops:
        length, kind = op.length, op.op
        if kind == "S":
            row += length
            clipped += length
        elif kind == "I":
            row += length
            insertions += length
        elif kind == "D":
            column += length
            deletions += length
        elif kind == "M":
            for _ in range(length):
                if query[row] != reference[column]:
                    mismatches += 1
                mapping[row] = column
                row += 1
                column += 1
        else:  # pragma: no cover - CIGAR 只可能含这几种操作
            raise AssertionError(f"未知的 CIGAR 操作 {kind}")
    assert row == len(sequence), "CIGAR 没有把 read 消费完"
    assert column - alignment.reference_start == alignment.reference_length, "CIGAR 与覆盖长度不符"
    assert (mismatches, insertions, deletions, clipped) == (
        alignment.mismatches,
        alignment.insertions,
        alignment.deletions,
        alignment.soft_clipped,
    ), "mapper 报的明细与 CIGAR 不符"
    return mapping


def _exact_occurrences(reference: str, sequence: str) -> int:
    """用 ``str.find`` 数 read 序列在参考上的**逐字**出现次数（正反两链）。

    与索引、种子投票、候选截断都无关，是最朴素的一条判据：一条无缺口全匹配的 read
    在参考上出现不止一次时，"唯一"这个断言就不可能成立。用它来给成规模的读集把关，
    比逐条暴力扫描便宜得多（C 层扫描），也正好补上"候选被截断"这个风险点。
    """
    probes = (sequence,)
    complement = _revcomp(sequence)
    if complement != sequence:  # 回文序列两链相同，别数两遍
        probes = (sequence, complement)
    count = 0
    for probe in probes:
        start = reference.find(probe)
        while start != -1:
            count += 1
            start = reference.find(probe, start + 1)
    return count


@dataclass(frozen=True)
class Case:
    """一条模拟 read 和它的真值。``start is None`` 表示**期望比不上**。"""

    name: str
    kind: str
    read: Read
    start: int | None = None
    strand: int = 1
    mismatches: int = 0
    insertions: int = 0
    deletions: int = 0
    soft_clipped: int = 0


def _build_cases(reference: str, rng: random.Random) -> list[Case]:
    """生成混合读集：真值（起点、链方向、各类事件数）全部由构造过程决定。"""
    length = _READ_LENGTH
    cases: list[Case] = []

    def piece_at(start: int, strand: int) -> str:
        text = reference[start : start + length]
        return text if strand == 1 else _revcomp(text)

    def orient(aligned: str, strand: int) -> str:
        """把"与参考同向"的一段转成 read 的**测序方向**（负链取反向互补）。"""
        return aligned if strand == 1 else _revcomp(aligned)

    def secure_start() -> int:
        # 避开参考两端，让"起点确实在中间"这件事不成为额外变量（两端另有专门用例）。
        return rng.randrange(300, len(reference) - length - 300)

    def substitute(text: str, count: int, pool: tuple[int, ...] = _POOL_ANY) -> str:
        characters = list(text)
        for offset in rng.sample(pool, count):
            characters[offset] = _different_base(characters[offset], rng)
        return "".join(characters)

    def with_insertion(text: str, count: int) -> str:
        at = 50  # 固定在中间，离两端都远
        return text[:at] + "".join(rng.choices(_BASES, k=count)) + text[at:]

    def with_deletion(text: str, count: int) -> str:
        at = 50
        return text[:at] + text[at + count :]

    def build(kind: str, sequence: str, **truth: object) -> Case:
        name = f"{kind}-{len(cases)}"
        return Case(
            name=name, kind=kind, read=Read(name=name, sequence=sequence), **truth  # type: ignore[arg-type]
        )

    # 1) 精确（正负链各半）
    for order in range(40):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        cases.append(build("exact", piece_at(start, strand), start=start, strand=strand))

    # 2) 替换 1~4 个
    for order in range(80):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 4 + 1
        cases.append(
            build(
                "sub",
                substitute(piece_at(start, strand), count),
                start=start,
                strand=strand,
                mismatches=count,
            )
        )

    # 3) 插入 1~2 个碱基
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 2 + 1
        cases.append(
            build(
                "ins",
                with_insertion(piece_at(start, strand), count),
                start=start,
                strand=strand,
                insertions=count,
            )
        )

    # 4) 缺失 1~2 个碱基
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 2 + 1
        cases.append(
            build(
                "del",
                with_deletion(piece_at(start, strand), count),
                start=start,
                strand=strand,
                deletions=count,
            )
        )

    # 5) 替换 + 插入 / 替换 + 缺失（替换只落在 indel 两侧，见偏移池的说明）
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 3 + 1
        piece = piece_at(start, strand)
        sequence = with_insertion(substitute(piece, count, _POOL_LEFT + _POOL_RIGHT), 1)
        cases.append(
            build(
                "sub+ins",
                sequence,
                start=start,
                strand=strand,
                mismatches=count,
                insertions=1,
            )
        )
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 3 + 1
        piece = piece_at(start, strand)
        sequence = with_deletion(substitute(piece, count, _POOL_LEFT + _POOL_RIGHT), 1)
        cases.append(
            build(
                "sub+del",
                sequence,
                start=start,
                strand=strand,
                mismatches=count,
                deletions=1,
            )
        )

    # 6) 末端读通：这里的"前缀/后缀"指**与参考同向**那条序列上的位置（也就是 CIGAR 里
    #    ``S`` 所在的那一端），read 的测序方向由链决定——负链的前缀在测序方向上是尾巴。
    #    外来碱基逐个挑成与**紧邻的那一位参考**不同：否则它可能恰好与参考对上，DP 就会
    #    合理地"多对齐一位、少剪一个碱基"（并列时它取剪裁更少的，见 dp.py），
    #    那种情形本身不是错误，但真值不再唯一，测的也就不再是剪裁了。
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 10 + 1
        prefix = "".join(
            _different_base(reference[start - count + offset], rng) for offset in range(count)
        )
        aligned = prefix + reference[start : start + length]
        cases.append(
            build(
                "prefix",
                orient(aligned, strand),
                start=start,
                strand=strand,
                soft_clipped=count,
            )
        )
    for order in range(30):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        count = order % 10 + 1
        suffix = "".join(
            _different_base(reference[start + length + offset], rng) for offset in range(count)
        )
        aligned = reference[start : start + length] + suffix
        cases.append(
            build(
                "suffix",
                orient(aligned, strand),
                start=start,
                strand=strand,
                soft_clipped=count,
            )
        )

    # 7) 含一个 N：按错配计（不是"匹配任意碱基"）
    for order in range(20):
        start = secure_start()
        strand = 1 if order % 2 == 0 else -1
        characters = list(piece_at(start, strand))
        characters[50] = "N"
        cases.append(
            build("n", "".join(characters), start=start, strand=strand, mismatches=1)
        )

    # 8) 参考两端
    for start, strand in ((0, 1), (len(reference) - length, -1)):
        cases.append(build("edge", piece_at(start, strand), start=start, strand=strand))

    # 9) 超上限：错配 5 个（> 4）、插入 4 个碱基（> 3）——都应当比不上
    for _ in range(10):
        cases.append(
            build("over-mismatch", substitute(piece_at(secure_start(), 1), 5), start=None)
        )
    for _ in range(10):
        cases.append(build("over-indel", with_insertion(piece_at(secure_start(), 1), 4), start=None))

    # 10) 外来序列（参考上不存在）——一条都不许比上
    for _ in range(20):
        cases.append(build("foreign", "".join(rng.choices(_BASES, k=length)), start=None))

    return cases


def test_synthetic_read_set_recall_and_exactness() -> None:
    """成规模混合读集：可映射的全部找回且逐项精确，越界的与外来的一条都不许比上。"""
    reference = _random_sequence(_REFERENCE_LENGTH, _SEED)
    cases = _build_cases(reference, random.Random(_SEED + 1))
    mapper = _mapper(reference)

    expected_mapped = [case for case in cases if case.start is not None]
    expected_unmapped = [case for case in cases if case.start is None]

    read_through = 0
    for case in expected_mapped:
        alignment = mapper.map_read(case.read)
        assert alignment is not None, f"{case.name}（{case.kind}）没有比对"
        assert alignment.seq_id == "chr", f"{case.name} 参考名错"
        assert alignment.strand == case.strand, f"{case.name}（{case.kind}）链方向错"
        assert alignment.query_length == case.read.length
        mapping = _verify_alignment(reference, case.read.sequence, alignment)

        # 无缺口全匹配的 read：用 str.find 独立数一遍它在参考上的逐字出现次数。
        # 出现不止一次时"唯一"就不可能成立（出现多次的正例在 MAPQ 校准那个用例里）。
        if (
            alignment.soft_clipped == 0
            and not alignment.is_gapped
            and alignment.mismatches == 0
        ):
            occurrences = _exact_occurrences(reference, case.read.sequence)
            assert occurrences >= 1
            assert occurrences == 1 or alignment.mapq != 60, (
                f"{case.name} 在参考上有 {occurrences} 个逐字出现，不该声称唯一"
            )

        if case.kind in ("prefix", "suffix"):
            # 末端读通那一段是**外来碱基**，它们偶然与上游参考对上时，DP 会多解释几个
            # 前缀碱基、付一个空位，换来略高的总分——这不是错误，并列时它也照 dp.py 的规则
            # 取剪裁更少的。这里守住真正要紧的不变量：**真来源的 100 个碱基一个不差地对上**，
            # 外来段最多只允许影响到真起点前 count 个碱基。
            offset_in_aligned = case.soft_clipped if case.kind == "prefix" else 0
            assert alignment.reference_start >= case.start - case.soft_clipped
            assert alignment.soft_clipped <= case.soft_clipped
            for offset in range(_READ_LENGTH):
                assert mapping.get(offset_in_aligned + offset) == case.start + offset, (
                    f"{case.name}（{case.kind}）真来源的第 {offset} 个碱基没对到真位置"
                )
            read_through += 1
        else:
            assert alignment.reference_start == case.start, f"{case.name}（{case.kind}）起点错"
            assert alignment.mismatches == case.mismatches, f"{case.name}（{case.kind}）错配数错"
            assert alignment.insertions == case.insertions, f"{case.name}（{case.kind}）插入数错"
            assert alignment.deletions == case.deletions, f"{case.name}（{case.kind}）缺失数错"
            assert alignment.soft_clipped == case.soft_clipped, f"{case.name}（{case.kind}）剪裁数错"

    for case in expected_unmapped:
        assert mapper.map_read(case.read) is None, f"{case.name}（{case.kind}）不该比上却比上了"

    kinds: dict[str, int] = {}
    for case in expected_mapped:
        kinds[case.kind] = kinds.get(case.kind, 0) + 1
    print(
        f"\n[分片 F 对拍] 参考 {len(reference)} bp、模拟 {len(cases)} 条 read："
        f"可映射 {len(expected_mapped)} 条（末端读通 {read_through} 条）全部找回且逐项吻合，"
        f"越界/外来 {len(expected_unmapped)} 条全部未被映射（0 误报）。"
        f"用例分布：{kinds}"
    )


def test_dense_index_keeps_reads_that_a_very_sparse_index_loses() -> None:
    """稀疏索引的代价面（文档第 3 条约定）：连续完全匹配段短于 step 时，种子不再保证命中。

    造 30 条 read，每条 4 个错配均匀铺在 20/45/70/95 位上——完全匹配段因此只剩 4~24 bp。
    实测（k=16）：step ≤ 32 时这 30 条一条不落（总有一段匹配里的窗口落对位置），
    step=64 时漏掉 16 条。这就是"稀疏只降灵敏度、不伤正确性"里被降掉的那部分灵敏度，
    也正是默认取 step=4 的原因：默认参数下完全匹配段远长于 4，这种漏检不会发生。
    """
    reference = _random_sequence(4000, _SEED + 41)
    rng = random.Random(_SEED + 42)
    reads: list[Read] = []
    for order in range(30):
        start = 200 + order * 100
        characters = list(reference[start : start + _READ_LENGTH])
        for offset in (20, 45, 70, 95):
            characters[offset] = _different_base(characters[offset], rng)
        reads.append(Read(name=f"clustered-{order}", sequence="".join(characters)))

    dense = _mapper(reference, step=1)
    assert all(dense.map_read(read) is not None for read in reads)

    sparse = _mapper(reference, step=64)
    lost = sum(1 for read in reads if sparse.map_read(read) is None)
    assert lost > 0, "step 远大于匹配段时应当出现漏检，否则这条用例没有说明力"


def test_mapq_unique_claims_hold_against_independent_scan() -> None:
    """MAPQ 60 的"唯一"断言，用独立暴力扫描复核。

    参考故意拼成三段式：一段 400 bp 的**完全重复**（两个拷贝）、一段只差 3 个碱基的
    **近重复副本**、以及若干唯一区。逐条 read 核对三件事：

    1. 唯一区的 read 拿到 60，且暴力扫描确认"全参考范围内最小错配的位置只有它一个"；
    2. 落在完全重复区里的 read **不许**声称唯一（暴力扫描能数出两个等好的位置）；
    3. 近重复副本上的 read 也不许声称唯一，并且要挑中真正完全匹配的那一份。

    只看无缺口比对是**有意的**：无缺口错配数是编辑距离的上界，所以"别处有窗口的错配数
    ≤ 当前编辑距离"就意味着确实没有理由说唯一——这个方向的保守性让结论站得住。
    """
    first = _random_sequence(1500, _SEED + 11)
    segment = _random_sequence(400, _SEED + 12)
    middle = _random_sequence(1500, _SEED + 13)
    spacer = _random_sequence(500, _SEED + 14)
    tail = _random_sequence(500, _SEED + 15)
    mutated = list(segment)
    for offset in (50, 150, 250):
        mutated[offset] = _different_base(mutated[offset], random.Random(_SEED + offset))
    near = "".join(mutated)
    reference = first + segment + middle + segment + spacer + near + tail
    near_start = len(first) + len(segment) + len(middle) + len(segment) + len(spacer)
    mapper = _mapper(reference)

    for index, offset in enumerate((300, 700, 1100)):
        sequence = middle[offset : offset + _READ_LENGTH]
        alignment = mapper.map_read(Read(name=f"unique-{index}", sequence=sequence))
        assert alignment is not None
        assert alignment.mapq == 60, f"唯一区的 read 应当拿到 60，实际 {alignment.mapq}"
        best, count = _ungapped_best(reference, sequence)
        assert (best, count) == (0, 1), f"唯一区应当只有一个完全匹配，实际 {(best, count)}"

    # 只取覆盖了 near 副本那 3 个变异位的窗口：另外的窗口与 near 也完全一样，
    # 那样参考上就有 3 个等好位置，测不到"两拷贝"这一条。
    for index, offset in enumerate((100, 200)):
        sequence = segment[offset : offset + _READ_LENGTH]
        alignment = mapper.map_read(Read(name=f"repeat-{index}", sequence=sequence))
        assert alignment is not None
        assert alignment.mapq != 60, "完全重复区里的 read 不许声称唯一"
        best, count = _ungapped_best(reference, sequence)
        assert (best, count) == (0, 2), f"完全重复区应当有两个等好位置，实际 {(best, count)}"

    # 近重复副本上的 read：真来源是 near，参考上另有两个只差 1 个碱基的拷贝。
    for index, offset in enumerate((100, 200)):
        sequence = near[offset : offset + _READ_LENGTH]
        alignment = mapper.map_read(Read(name=f"near-{index}", sequence=sequence))
        assert alignment is not None
        assert alignment.mapq != 60, "存在近重复副本时不许声称唯一"
        assert alignment.reference_start == near_start + offset, "应当挑真正完全匹配的那一份"
        assert alignment.mismatches == 0


def test_many_equal_copies_never_report_uniqueness() -> None:
    """候选短名单被截断也要老实：20 个等好拷贝时，读取必须报告"无法区分"。"""
    segment = _random_sequence(300, _SEED + 21)
    filler = _random_sequence(200, _SEED + 22)
    reference = filler.join([segment] * 20)
    mapper = _mapper(reference)

    for index, offset in enumerate((10, 100, 200)):
        sequence = segment[offset : offset + _READ_LENGTH]
        alignment = mapper.map_read(Read(name=f"copy-{index}", sequence=sequence))
        assert alignment is not None
        assert alignment.mapq == 0, f"20 个等好拷贝时 MAPQ 应当是 0，实际 {alignment.mapq}"
        assert not alignment.is_unique


def _recompute_md(reference: str, query: str, alignment: Alignment) -> str:
    """按 SAM 规范**独立重算** MD 串（不复用写出层的实现，只拿 CIGAR + 参考 + 序列）。"""
    pieces: list[str] = []
    matched = 0
    row, column = 0, alignment.reference_start
    for op in alignment.cigar.ops:
        if op.op in ("S", "I"):
            row += op.length
        elif op.op == "D":
            pieces.append(str(matched))
            pieces.append("^" + reference[column : column + op.length])
            matched = 0
            column += op.length
        else:  # M
            for _ in range(op.length):
                if query[row] == reference[column]:
                    matched += 1
                else:
                    pieces.append(str(matched))
                    pieces.append(reference[column])
                    matched = 0
                row += 1
                column += 1
    pieces.append(str(matched))
    return "".join(pieces)


def test_high_copy_repeat_kmers_are_pruned_and_the_read_is_lost() -> None:
    """已知限制（用实测钉住，不当成优点）：高拷贝重复区里的 read 会整条比不上。

    100 个拷贝 × 300 bp 时，区内的 k-mer 每个都有 ~75 个位置，超过 ``max_hits=64``，
    于是整条 k-mer 被剔除（见 `index.py` 的"重复序列"一节）——read 拿不到任何种子，
    也就没有任何候选。这是"宁可漏、不可偏向某个拷贝"这个取舍的代价，记为已知限制。
    """
    segment = _random_sequence(300, _SEED + 31)
    filler = _random_sequence(200, _SEED + 32)
    reference = filler.join([segment] * 100)
    mapper = _mapper(reference)

    alignment = mapper.map_read(Read(name="in-repeat", sequence=segment[100:200]))
    assert alignment is None
    stats = next(iter(mapper.index.stats()))
    assert stats["pruned_kmers"] > 0, "高拷贝重复区的 k-mer 应当被整条剔除"


def test_scaled_sam_roundtrip_is_self_consistent(tmp_path) -> None:
    """放量写出 SAM 再读回：坐标、朝向、CIGAR、NM、MD 逐条独立重算核对。

    这是分片 F 里"与下游对拍"能做成的那一半。上游的 bowtie2 / samtools / IGV 都不在本机
    环境里，没法和它们真跑一遍；但 SAM 这个**对出契约**可以自己查得一样严：把写出的记录
    用公共层读回来，再用 CIGAR + 参考独立重算 NM 与 MD，任何字段写错、坐标差一、
    负链没反向互补，都会在这里露出来。
    """
    reference = _random_sequence(_REFERENCE_LENGTH, _SEED)
    cases = _build_cases(reference, random.Random(_SEED + 1))
    mapper = _mapper(reference)
    reads = [case.read for case in cases]
    alignments = list(mapper.map_reads(reads))

    path = tmp_path / "mapped.sam"
    written = write_mapped_sam(path, mapper.reference, reads, alignments)
    header, records = read_sam(path)

    expected_written = sum(1 for alignment in alignments if alignment is not None)
    assert written == expected_written
    assert len(records) == expected_written
    assert [sequence.name for sequence in header.sequences] == ["chr"]
    assert header.reference_length("chr") == len(reference)

    by_name = {record.query_name: record for record in records}
    assert len(by_name) == expected_written  # 名字唯一，下面才能按名字取
    for case, alignment in zip(cases, alignments):
        if alignment is None:
            assert case.read.name not in by_name, "未比对的 read 不该出现在 SAM 里"
            continue
        record = by_name[case.read.name]
        assert record.is_reverse == (alignment.strand == -1), f"{case.name} 朝向标记错"
        assert record.position == alignment.reference_start + 1, f"{case.name} POS 不是 1-based"
        assert str(record.cigar) == str(alignment.cigar), f"{case.name} CIGAR 不一致"
        assert record.mapping_quality == alignment.mapq
        expected_sequence = (
            case.read.sequence if alignment.strand == 1 else _revcomp(case.read.sequence)
        )
        assert record.sequence == expected_sequence, f"{case.name} SEQ 朝向错"
        assert record.tag_int("NM") == alignment.edit_distance, f"{case.name} NM 错"
        assert record.tag("MD") == _recompute_md(reference, expected_sequence, alignment), (
            f"{case.name} MD 与 CIGAR/参考重算的结果不符"
        )
        assert 1 <= record.position <= len(reference)
        assert record.reference_end <= len(reference), f"{case.name} 覆盖区间越过参考末端"
