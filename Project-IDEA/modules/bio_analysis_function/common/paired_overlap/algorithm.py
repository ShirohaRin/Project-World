"""paired_overlap：双端 read 的 overlap 分析（**工具**，不是算法入口）。

**产出**：一个 :class:`OverlapResult`——两条 read 是否重叠、重叠的错位量
（``offset``）、重叠长度（``overlap_len``）、错配数（``diff``）、以及是否带 1 个缺口
（``has_gap``）。**不写任何文件、不改动输入**。

**为什么放在公共层**：这个产出自身对用户没有意义——它必须被进一步处理
（按 overlap 裁掉接头、用重叠区校正低质量碱基、或把两条 read 合并成一条）。
按 `开发规则.md` 3.2 属于第 2 类，因此没有独立入口，由后续的双端算法调用。

实现与 fastp 1.3.x 的 ``OverlapAnalysis::analyze``（``src/overlapanalysis.cpp``）
逐位对齐。它的思路（源自 AfterQC）是：

1. 把 r2 取**反向互补**（记为 rc2），于是"两条 read 来自同一片段"变成了
   "r1 与 rc2 在某一个错位量上对齐"；
2. 沿两个方向扫错位量：**正向**（偏移量为正，rc2 的起点落在 r1 内部）与
   **反向**（偏移量为负，rc2 的起点落在 r1 起点之前）；
3. 每个错位量都要求重叠区前 ``50`` 个碱基内的错配不超过
   ``min(diff_limit, int(overlap_len * diff_percent_limit))``——**只看前 50 个**是刻意的：
   这样'先匹配上、后面再出现一批错配'的情形也能认出来（见模块测试里的第二组向量）；
4. 没找到就（可选）再允许 1 个插入/缺失扫一遍。

**偏移量的符号对应两种截然不同的片段几何**——PE 侧两个算法（裁接头、合并）都靠它，
必须记准：

| 偏移量 | 几何 | 片段长度 |
| --- | --- | --- |
| `> 0` | 片段**长于**读长：两条 read 只在中间重叠，**都没有读进接头** | `len1 + len2 - overlap_len` |
| `= 0` | 片段长度约等于读长 | `overlap_len` |
| `< 0` | 片段**短于**读长：两条 read 都读穿片段、**两端都读进了接头** | `overlap_len` |

两个容易搞反的地方，都是**反向互补会翻转顺序**造成的：

- ``offset < 0`` 时 ``overlap_len`` 就等于**片段长度**，裁接头直接拿它当保留长度即可；
- r2 读到的接头落在 **rc2 的开头**（不是尾部），所以"两端读穿"呈现出来的样子是
  "r1 的头与 rc2 的中间对齐"。

上面两条片段长度公式的出处是上游 ``PairEndProcessor::statInsertSize``
（``src/peprocessor.cpp``），它是这个符号约定的权威依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..sequences import count_mismatches, count_mismatches_bounded, reverse_complement

#: 每个错位量都要求前这么多个碱基的错配不超限（上游写作 complete_compare_require）。
_PROTECTED_PREFIX: Final = 50


@dataclass(frozen=True, slots=True)
class OverlapConfig:
    """overlap 参数，默认值与 fastp 命令行一致。

    | 字段 | fastp 对应物 | 默认 | 说明 |
    | --- | --- | --- | --- |
    | `diff_limit` | `--overlap_diff_limit` | 5 | 重叠区允许的最大错配数 |
    | `require` | `--overlap_require` | 30 | 认定为重叠所需的最短重叠长度 |
    | `diff_percent_limit` | `--overlap_diff_percent_limit` | 0.2 | 错配数还不得超过重叠长度的这个比例 |
    | `allow_gap` | 上游默认关闭 | `False` | 是否再尝试"允许 1 个插入/缺失" |
    """

    diff_limit: int = 5
    require: int = 30
    diff_percent_limit: float = 0.2
    allow_gap: bool = False

    def __post_init__(self) -> None:
        if self.require < 1:
            raise ValueError(f"require 必须不小于 1，当前为 {self.require}。")
        if self.diff_limit < 0:
            raise ValueError(f"diff_limit 不能为负数，当前为 {self.diff_limit}。")
        if not 0.0 <= self.diff_percent_limit <= 1.0:
            raise ValueError(
                f"diff_percent_limit 必须在 0 到 1 之间，当前为 {self.diff_percent_limit}。"
            )


@dataclass(frozen=True, slots=True)
class OverlapResult:
    """一次 overlap 分析的结论。

    ``offset``：r2 的反向互补（rc2）相对 r1 的错位量。符号含义见模块文档——
    简记：``> 0`` 片段长于读长（只中间重叠、无接头），``< 0`` 片段短于读长
    （两端读穿、rc2 开头是接头）。
    """

    overlapped: bool
    offset: int = 0
    overlap_len: int = 0
    diff: int = 0
    has_gap: bool = False
    #: 两条 read 的长度，供 :attr:`insert_size` 使用；由 :func:`analyze_overlap` 填入。
    read1_length: int = 0
    read2_length: int = 0

    @property
    def insert_size(self) -> int:
        """插入片段长度；不重叠时给 0。

        两个分支照抄上游 ``PairEndProcessor::statInsertSize``：``offset > 0`` 时
        两条 read 只在中间重叠、片段比读长长，长度是 ``len1 + len2 - overlap_len``；
        ``offset <= 0`` 时两端读穿，重叠长度本身就是片段长度。
        """
        if not self.overlapped:
            return 0
        if self.offset > 0:
            return self.read1_length + self.read2_length - self.overlap_len
        return self.overlap_len

    def render(self) -> str:
        if not self.overlapped:
            return "未检出双端重叠"
        gap = "（含 1 个缺口）" if self.has_gap else ""
        return (
            f"检出双端重叠{gap}：错位 {self.offset}，重叠 {self.overlap_len} 个碱基，"
            f"错配 {self.diff}，片段长度约 {self.insert_size}"
        )


def _limit_for(overlap_len: int, config: OverlapConfig) -> int:
    """当前重叠长度下的错配上限（上游用 C 的 int() 截断，这里同）。"""
    return min(config.diff_limit, int(overlap_len * config.diff_percent_limit))


def _accept_no_gap(
    left: bytes,
    right: bytes,
    length: int,
    limit: int,
) -> tuple[bool, int]:
    """判定一个错位量是否成立；返回 ``(是否成立, 错配数)``。

    上游在这里只拿**前 50 个碱基**做判定（超限就否掉），但比对长度超过 50 时
    报出去的 ``diff`` 是**整段**的真实错配数。这个不对称是刻意的，也是它能把
    "前面完全对上、后面才开始错"的情形认出来的原因。
    """
    protected = min(length, _PROTECTED_PREFIX)
    mismatches = count_mismatches_bounded(left, right, protected, limit)
    if mismatches > limit:
        return False, mismatches
    if length > _PROTECTED_PREFIX:
        mismatches = count_mismatches(left, right, length)
    return True, mismatches


def diff_with_one_insertion(
    inserted: bytes,
    normal: bytes,
    compare_length: int,
    diff_limit: int,
) -> int:
    """允许 1 个插入时的最小错配数；超过上限返回 ``-1``。

    上游 ``Matcher::diffWithOneInsertion`` 的移植：前缀/后缀累计错配，
    再找插入位置使两段之和最小。与 ``Matcher::matchWithOneInsertion`` 同一套结构，
    区别是它要**最小值**而不是"能不能行"。

    与上游的唯一差别：C++ 里前缀累计数组在提前 break 之后是未初始化内存，
    而最后的判定循环可能读到它们。本实现把累计算完整，因此结果确定、可复现。
    """
    if compare_length < 1:
        return -1

    left = [0] * compare_length
    right = [0] * compare_length

    left[0] = 0 if inserted[0] == normal[0] else 1
    right[compare_length - 1] = (
        0 if inserted[compare_length] == normal[compare_length - 1] else 1
    )

    for index in range(1, compare_length):
        left[index] = left[index - 1] + (0 if inserted[index] == normal[index] else 1)

    for index in range(compare_length - 2, -1, -1):
        right[index] = right[index + 1] + (
            0 if inserted[index + 1] == normal[index] else 1
        )
        if right[index] + left[0] > diff_limit:
            for earlier in range(index):
                right[earlier] = diff_limit + 1
            break

    min_diff = 100000000
    for index in range(1, compare_length):
        if left[index - 1] + right[compare_length - 1] > diff_limit:
            return -1
        diff = left[index - 1] + right[index]
        if diff <= min_diff:
            min_diff = diff
    return min_diff


def analyze_overlap(
    read1: bytes,
    read2: bytes,
    config: OverlapConfig | None = None,
) -> OverlapResult:
    """分析两条 read 是否来自同一片段（双端 overlap）。

    参数：
        read1 / read2: 两条 read 的碱基串（**原始方向**，函数内部自己取反向互补）。
        config: 参数；``None`` 表示默认（与 fastp 一致）。

    返回：
        :class:`OverlapResult`。

    说明：
        ``read2`` 会被反向互补，因此调用方不必先处理方向。
        重叠长度达不到 ``require`` 时一律判定为"不重叠"（上游注释：这是为了
        "不确定就不认"）。
    """
    config = config or OverlapConfig()
    reverse2 = reverse_complement(read2)
    length1 = len(read1)
    length2 = len(reverse2)

    # 1) 正向：r1 的尾巴对 rc2 的头
    offset = 0
    while offset < length1 - config.require:
        overlap_len = min(length1 - offset, length2)
        limit = _limit_for(overlap_len, config)
        accepted, diff = _accept_no_gap(
            read1[offset:], reverse2, overlap_len, limit
        )
        if accepted:
            return OverlapResult(
                True, offset, overlap_len, diff, False,
                read1_length=length1, read2_length=length2,
            )
        offset += 1

    # 2) 反向：r1 的头对 rc2 的中间（接头出现在 rc2 的尾部）
    offset = 0
    while offset > -(length2 - config.require):
        overlap_len = min(length1, length2 - abs(offset))
        limit = _limit_for(overlap_len, config)
        accepted, diff = _accept_no_gap(
            read1, reverse2[-offset:], overlap_len, limit
        )
        if accepted:
            return OverlapResult(
                True, offset, overlap_len, diff, False,
                read1_length=length1, read2_length=length2,
            )
        offset -= 1

    if config.allow_gap:
        # 3) 正向 + 1 个缺口
        offset = 0
        while offset < length1 - config.require:
            overlap_len = min(length1 - offset, length2)
            limit = _limit_for(overlap_len, config)
            diff = diff_with_one_insertion(
                read1[offset:], reverse2, overlap_len - 1, limit
            )
            if diff < 0 or diff > limit:
                diff = diff_with_one_insertion(
                    reverse2, read1[offset:], overlap_len - 1, limit
                )
            if 0 <= diff <= limit:
                return OverlapResult(
                    True, offset, overlap_len, diff, True,
                    read1_length=length1, read2_length=length2,
                )
            offset += 1

        # 4) 反向 + 1 个缺口
        offset = 0
        while offset > -(length2 - config.require):
            overlap_len = min(length1, length2 - abs(offset))
            limit = _limit_for(overlap_len, config)
            diff = diff_with_one_insertion(
                read1, reverse2[-offset:], overlap_len - 1, limit
            )
            if diff < 0 or diff > limit:
                diff = diff_with_one_insertion(
                    reverse2[-offset:], read1, overlap_len - 1, limit
                )
            if 0 <= diff <= limit:
                return OverlapResult(
                    True, offset, overlap_len, diff, True,
                    read1_length=length1, read2_length=length2,
                )
            offset -= 1

    return OverlapResult(
        False, read1_length=length1, read2_length=length2
    )


#: 上游 ``OverlapAnalysis::test()`` 的两组向量，供测试与人工核验使用。
UPSTREAM_TEST_VECTORS: Final = (
    {
        "read1": b"CAGCGCCTACGGGCCCCTTTTTCTGCGCGACCGCGTGGCTGTGGGCGCGGATGCCTTTGAGCGCGGTGACTTCTCACTGCGTATCGAGC",
        "read2": b"ACCTCCAGCGGCTCGATACGCAGTGAGAAGTCACCGCGCTCAAAGGCATCCGCGCCCACAGCCACGCGGTCGCGCAGAAAAAGGGGTCC",
        "diff_limit": 2,
        "require": 30,
        "diff_percent_limit": 0.2,
        "expect": (10, 79, 1),
    },
    {
        # 前 50 个碱基完全一致、后面 30 个全是错配：靠"只判定前 50 个"才认得出。
        # 上游向量里 r2 是 rc("A"*50 + "G"*30)，这里直接写成它的反向互补形式。
        "read1": b"A" * 50 + b"C" * 30,
        "read2": b"C" * 30 + b"T" * 50,
        "diff_limit": 0,
        "require": 30,
        "diff_percent_limit": 0.0,
        "expect": (0, 80, 30),
    },
)

#: 一组"只有打开 ``allow_gap`` 才认得出"的向量（实测扫出来的实例）：
#: 两条 read 之间隔着单个碱基 indel，重叠区 48 个碱基、indel 落在靠末端的一小段里。
#: 无缺口那一轮只判定前 50 个碱基，按位比对时错配超限；缺口那一轮给出 ``diff=0``。
#: 详细条件见 ``tests/test_algorithm.py`` 里那个用例。
GAP_ONLY_VECTOR: Final = {
    "read1": b"CATATAGGTAGTTCTTGTCTAGGTGCTTCGCCGATACCAGCTGCGGAACGGTACTACGGG",
    "read2": b"GTTCCGCAGCTGTATCGGCGAAGCACCTAGACAAGAACTACCTATATG",
    "diff_limit": 8,
    "require": 15,
    "expect": (0, 48, 0),
}
