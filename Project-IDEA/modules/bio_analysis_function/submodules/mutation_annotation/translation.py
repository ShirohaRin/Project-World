"""遗传密码与 CDS 翻译（分片 A）。

突变注释的第一步：把参考上的编码区（CDS）翻成蛋白。有了它，后面才能回答"这个碱基变了，
蛋白第几个氨基酸从什么变成了什么"（分片 B）以及"这个变异落在哪个基因里"。

三条约定先写清楚，后面每一片都会用到：

1. **标准遗传密码（NCBI 表 1）**，不做非标准密码表、不做密码子偏好。细菌重测序绝大多数
   情况就是表 1；真要支持线粒体或某些原生生物的表，那是另一件事，不该混进来。
2. **起始密码子统一译成 ``M``**。细菌常用的起始密码子除了 ``ATG`` 还有 ``GTG`` / ``TTG``
   （少数情况有 ``ATT`` / ``ATA`` / ``CTG``）；按标准表它们分别译成 V / L / I / I / L，但作为
   **起始**时生物实际装上去的是甲硫氨酸，上游 breseq 的报告也是这么写的——它把
   ``ATG→ATA`` 的起始子改变写成 ``M1M``，正说明 ``ATA`` 也在它的起始集合里。
   :data:`START_CODONS` 就是这条规则用的集合。
3. **终止密码子不进蛋白**。完整 CDS 的最后一个密码子通常是 ``TAA`` / ``TAG`` / ``TGA``；
   把它记在 :attr:`CdsTranslation.stop_codon` 里，但不写进 :attr:`CdsTranslation.protein`
   ——这样蛋白长度与"第几个氨基酸"的编号才和下游报告对得上。

两条边界：**含未知碱基的密码子译成 ``X``**（``N`` 在参考里很常见，硬报错会让整条链读不下去）；
**结构性问题报错、注释口径差异如实记录**——长度不是 3 的倍数无法翻译，这是数据结构错误，
直接报错；而"完整 CDS 却没有终止密码子"是命名方口径不同（有的文件不把终止密码子算进 CDS），
不报错、记在字段里，由调用方决定要不要在意。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.reference_io import Feature, ReferenceFormatError, ReferenceSequence

__all__ = [
    "CODON_TABLE",
    "START_CODONS",
    "STOP",
    "UNKNOWN_RESIDUE",
    "CdsTranslation",
    "cds_translation",
    "translate",
    "translate_cds",
    "translate_features",
]

#: 终止密码子在蛋白序列里的默认符号。
STOP = "*"
#: 含未知碱基的密码子译成这个（不是报错）。
UNKNOWN_RESIDUE = "X"

#: 64 个密码子的氨基酸（NCBI 表 1），按"第一/第二/第三位均为 T、C、A、G"的顺序排列。
_AMINO_ACIDS = (
    "FFLLSSSSYY**CC*W"  # 第一位 T
    "LLLLPPPPHHQQRRRR"  # 第一位 C
    "IIIMTTTTNNKKSSRR"  # 第一位 A
    "VVVVAAAADDEEGGGG"  # 第一位 G
)
_BASE_ORDER = "TCAG"


def _build_codon_table() -> dict[str, str]:
    table: dict[str, str] = {}
    index = 0
    for first in _BASE_ORDER:
        for second in _BASE_ORDER:
            for third in _BASE_ORDER:
                table[first + second + third] = _AMINO_ACIDS[index]
                index += 1
    return table


#: 密码子 → 氨基酸（标准遗传密码）。测试会用"每个氨基酸的简并度"独立核对这张表。
CODON_TABLE: dict[str, str] = _build_codon_table()

#: 细菌里出现过的起始密码子：``ATG`` 与常见的替代起始。**作为起始时一律译成 M**
#: （见模块开头第 2 条）。上游把 ``ATG→ATA`` 报成 ``M1M``，所以 ``ATA`` 也在集合里。
START_CODONS = frozenset({"ATG", "GTG", "TTG", "ATT", "ATA", "CTG"})


@dataclass(frozen=True, slots=True)
class CdsTranslation:
    """一个 CDS 的翻译结果。

    ``sequence`` 是**按位置取好**的 CDS 核苷酸（负链已经反向互补），``protein`` 不含末尾的
    终止密码子；``trailing_bases`` 是"序列末尾不足一个密码子、被忽略掉的碱基数"（完整 CDS
    恒为 0，部分 CDS 可能非 0）。
    """

    seq_id: str
    gene: str
    strand: int
    sequence: str
    protein: str
    start_codon: str
    stop_codon: str
    partial: bool = False
    trailing_bases: int = 0

    @property
    def nucleotide_length(self) -> int:
        """CDS 核苷酸数（含终止密码子）。"""
        return len(self.sequence)

    @property
    def residue_count(self) -> int:
        """蛋白残基数（不含终止密码子）。"""
        return len(self.protein)

    @property
    def start_is_standard(self) -> bool:
        """起始密码子是否就是 ``ATG``。"""
        return self.start_codon == "ATG"

    @property
    def start_is_alternative(self) -> bool:
        """是否用了非 ``ATG`` 的常见起始密码子（``GTG`` / ``TTG`` / …）。"""
        return self.start_codon in START_CODONS and self.start_codon != "ATG"

    @property
    def start_is_unknown(self) -> bool:
        """起始密码子不在已知起始集合里——注释可能有问题，值得关注。"""
        return self.start_codon not in START_CODONS

    @property
    def has_stop_codon(self) -> bool:
        """序列末尾是否有终止密码子。"""
        return bool(self.stop_codon)

    @property
    def has_internal_stop(self) -> bool:
        """蛋白内部是否有终止密码子（伪基因、或注释坐标有问题时会看到）。"""
        return STOP in self.protein


def _codons(text: str) -> list[str]:
    """切密码子；末尾不足 3 个碱基的部分直接丢掉。"""
    return [text[index : index + 3] for index in range(0, len(text) - 2, 3)]


def translate(sequence: str, *, stop_symbol: str = STOP) -> str:
    """按标准遗传密码翻译一段核酸序列。

    - 末尾不足一个密码子的碱基**忽略**（核酸序列常常不是 3 的倍数，这里不报错）；
    - 含未知碱基的密码子译成 ``X``；
    - 终止密码子译成 ``stop_symbol``（默认 ``*``）。
    """
    residues: list[str] = []
    for codon in _codons(sequence.upper()):
        residue = CODON_TABLE.get(codon)
        if residue is None:
            residues.append(UNKNOWN_RESIDUE)
        elif residue == STOP:
            residues.append(stop_symbol)
        else:
            residues.append(residue)
    return "".join(residues)


def translate_cds(
    sequence: str,
    *,
    gene: str = "",
    seq_id: str = "",
    strand: int = 1,
    partial: bool = False,
) -> CdsTranslation:
    """翻译一个 CDS：起始密码子按 ``M``、末尾终止密码子不进蛋白。

    参数：
        sequence: CDS 核苷酸（与蛋白同向，负链调用方已经反向互补好）。
        gene / seq_id / strand: 随结果一起带出的标识，便于报告。
        partial: 该 CDS 的端点带 ``<`` / ``>``（序列不完整）。此时长度不要求是 3 的倍数，
            末尾不足一个密码子的碱基被忽略并记在 ``trailing_bases`` 里。

    完整 CDS（``partial=False``）的长度不是 3 的倍数时报错——那是结构上无法翻译的数据。
    """
    text = sequence.upper()
    if not text:
        raise ValueError("CDS 序列为空，无法翻译。")
    if not partial and len(text) % 3 != 0:
        raise ValueError(
            f"完整 CDS 的长度 {len(text)} 不是 3 的倍数，无法按读码框翻译；"
            f"若这份注释本来就是部分序列，请把 partial 置为真。"
        )
    codons = _codons(text)
    if not codons:
        raise ValueError(f"CDS 长度 {len(text)} 不足一个密码子，无法翻译。")

    residues: list[str] = []
    for index, codon in enumerate(codons):
        if index == 0 and codon in START_CODONS:
            residues.append("M")  # 起始密码子统一按 M（见模块开头第 2 条）
            continue
        residue = CODON_TABLE.get(codon)
        if residue is None:
            residues.append(UNKNOWN_RESIDUE)
        elif residue == STOP:
            residues.append(STOP)
        else:
            residues.append(residue)

    stop_codon = ""
    if CODON_TABLE.get(codons[-1]) == STOP:
        stop_codon = codons[-1]
        residues.pop()  # 末尾终止密码子不进蛋白

    return CdsTranslation(
        seq_id=seq_id,
        gene=gene,
        strand=strand,
        sequence=text,
        protein="".join(residues),
        start_codon=codons[0],
        stop_codon=stop_codon,
        partial=partial,
        trailing_bases=len(text) - 3 * len(codons),
    )


def cds_translation(feature: Feature, reference: ReferenceSequence) -> CdsTranslation:
    """把一个 ``CDS`` 特征翻译成蛋白（按它的位置取序列，负链自动反向互补）。"""
    if feature.location.mixed_strand:
        raise ValueError(
            f"特征 {feature} 的各段链方向不一致，这种位置没法当成一个读码框翻译。"
        )
    try:
        sequence = reference.extract(feature.location)
    except ReferenceFormatError as error:  # pragma: no cover - 模型层已校验过特征在范围内
        raise ValueError(f"特征 {feature} 的序列取不出来：{error}") from error
    return translate_cds(
        sequence,
        gene=feature.gene,
        seq_id=reference.seq_id,
        strand=feature.location.strand,
        partial=not feature.location.complete,
    )


def translate_features(
    reference: ReferenceSequence, *kinds: str
) -> tuple[CdsTranslation, ...]:
    """翻译参考里指定类型的特征（默认 ``CDS``），按文件顺序返回。"""
    wanted = kinds or ("CDS",)
    return tuple(
        cds_translation(feature, reference) for feature in reference.features_of(*wanted)
    )
