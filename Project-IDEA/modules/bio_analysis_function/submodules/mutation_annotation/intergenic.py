"""基因间区域与距离（分片 C）。

上游 breseq 报告里的这一列：

```text
intergenic (-110/-179)  topA→
intergenic (+22/-4)     yhdG→/→fis
intergenic (-66/+287)   glmU ←/+atpC
```

含义：变异落在两个基因之间，两个数分别量它到**左右两侧最近基因**的距离。**符号不是随便加的**：
``+`` 表示变异在该基因**转录方向的 3' 侧（下游）**、``-`` 表示在 5' 侧（上游）——所以同一个位置
对正链基因与负链基因的符号正好相反（``glmU ←/+atpC`` 那一行就是这个情形）。

本实现在下面几条约定下工作（**对上游口径的重建**，不承诺与上游逐字一致，见文档）：

1. 参与"基因"定义的特征类型默认是 :data:`DEFAULT_GENE_KINDS`（``CDS`` / ``tRNA`` / ``rRNA`` /
   ``tmRNA`` / ``ncRNA`` / ``misc_RNA``）。``misc_feature``、``repeat_region`` 这类**不是基因**，
   不该把"基因间"切开。
2. **落在这些特征内部（含端点）的变异不是 intergenic**，本函数返回 ``None``：编码区内的注释由
   `effects.py` 负责，落在非编码特征内部的注释归后续分片。
3. 左侧基因取"结束坐标小于变异位置的那些里结束得最晚的"，右侧取"起始坐标大于变异位置的那些里
   起始得最早的"。``join`` 的多段特征**按每一段分别参与比较**——跨复制原点的基因因此可能同时
   出现在两侧。
4. 距离是碱基数（**≥ 1**，因为落在基因内的情形已经在第 2 条里排除掉了）；另给一份**带符号**的
   距离，按该段的链方向算成"转录下游为正"。``description`` 的写法对齐上游：``intergenic (+6/-5)``，
   某一侧没有基因时写 ``.``（如 ``intergenic (-7/.)``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.reference_io import ReferenceSequence

__all__ = ["DEFAULT_GENE_KINDS", "IntergenicEffect", "intergenic_effect"]

#: 哪些特征类型算"基因"（决定"基因间"的两侧边界）。
DEFAULT_GENE_KINDS: tuple[str, ...] = ("CDS", "tRNA", "rRNA", "tmRNA", "ncRNA", "misc_RNA")


@dataclass(frozen=True, slots=True)
class IntergenicEffect:
    """一个位置落在基因之间时的两侧邻居与距离。

    ``*_distance`` 是碱基数（正数）；``*_signed_distance`` 是把"转录下游"记为正之后的带符号值。
    某一侧没有基因（落在注释的最左/最右之外）时，对应字段为 ``None``。
    """

    seq_id: str
    position: int
    left_gene: str | None = None
    left_distance: int | None = None
    left_signed_distance: int | None = None
    right_gene: str | None = None
    right_distance: int | None = None
    right_signed_distance: int | None = None

    @property
    def is_between_genes(self) -> bool:
        """两侧是否都有基因（否则只是"在注释的边上"，不是真正的两基因之间）。"""
        return self.left_gene is not None and self.right_gene is not None

    @property
    def description(self) -> str:
        """报告里那一列：``intergenic (+6/-5)``；缺一侧时写 ``.``。"""
        left = (
            f"{self.left_signed_distance:+d}" if self.left_distance is not None else "."
        )
        right = (
            f"{self.right_signed_distance:+d}" if self.right_distance is not None else "."
        )
        return f"intergenic ({left}/{right})"


def intergenic_effect(
    reference: ReferenceSequence,
    position: int,
    *,
    kinds: tuple[str, ...] = DEFAULT_GENE_KINDS,
) -> IntergenicEffect | None:
    """注释一个位置与两侧最近基因的关系；落在基因内（或没有任何基因）时返回 ``None``。

    参数：
        reference: 一条参考序列（含特征表）。
        position: **1-based** 参考坐标。
        kinds: 算作"基因"的特征类型，默认 :data:`DEFAULT_GENE_KINDS`。
    """
    if not 1 <= position <= reference.length:
        raise ValueError(
            f"位置 {position} 超出 {reference.seq_id} 的长度 {reference.length}。"
        )

    left: tuple[int, int, str, int] | None = None  # (start, end, gene, strand)
    right: tuple[int, int, str, int] | None = None
    for feature in reference.features_of(*kinds):
        gene = feature.gene
        for part in feature.location.parts:
            if part.start <= position <= part.end:
                return None  # 落在基因内部（含端点）
            if part.end < position:
                if left is None or part.end > left[1]:
                    left = (part.start, part.end, gene, part.strand)
            elif part.start > position:
                if right is None or part.start < right[0]:
                    right = (part.start, part.end, gene, part.strand)

    if left is None and right is None:
        return None  # 这条参考上没有任何可参照的基因

    left_distance = left_signed = None
    left_gene = None
    if left is not None:
        _start, end, left_gene, strand = left
        left_distance = position - end
        # 基因组坐标上变异在左基因右侧：正链是转录下游（+），负链是转录上游（−）。
        left_signed = left_distance if strand == 1 else -left_distance

    right_distance = right_signed = None
    right_gene = None
    if right is not None:
        start, _end, right_gene, strand = right
        right_distance = start - position
        # 变异在右基因左侧：正链是转录上游（−），负链是转录下游（+）。
        right_signed = -right_distance if strand == 1 else right_distance

    return IntergenicEffect(
        seq_id=reference.seq_id,
        position=position,
        left_gene=left_gene,
        left_distance=left_distance,
        left_signed_distance=left_signed,
        right_gene=right_gene,
        right_distance=right_distance,
        right_signed_distance=right_signed,
    )
