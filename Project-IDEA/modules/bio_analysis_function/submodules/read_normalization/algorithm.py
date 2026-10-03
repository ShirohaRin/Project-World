"""read_normalization：reads 规范化（质量编码转换 + MGI 名字修复）。

**这是处理链最前面的一步**：把"格式上需要先归置一下"的东西归置好，
后面的算法就不必各自操心。做两件事，各自可以单独开关：

1. **质量编码转换**（上游 ``--phred64`` / ``Read::convertPhred64To33``）：
   某些平台（尤其老数据）的质量字符是 **Phred+64**，与现在通行的 Phred+33
   差 31。不转的话，所有按质量判定的算法（质量剪切、过滤、统计）都会读错——
   而且**不会报错**，只会给出偏移了 31 的结论。
2. **MGI 名字修复**（上游 ``--fix_mgi_id`` / ``Read::fixMGI``）：
   把 ``xxx/1`` 改成 ``xxx /1``（斜杠前插一个空格）。

本文件只做记录级的转换，不读写文件；文件级接口见 ``runner.py``。

--------------------------------------------------------------------------
它不做的事
--------------------------------------------------------------------------
**不做"自动判断编码"。** 原因要说清楚：Phred+64 的质量字符范围是
``'@'(64)`` 到 ``'~'(126)``，Phred+33 是 ``'!'(33)`` 到 ``'~'(126)``——
两者**在高质量区间完全重叠**。只有出现 ASCII < 64 的字符时才能**确定**是 33；
反过来"没看到 < 64 的字符"既可能是 Phred+33 的高质量数据，也可能是 Phred+64。
所以本模块提供 :func:`detect_phred_offset` 做**单向判断**（能确定 33 时给出 33，
否则给 ``None`` 表示"看不出来"），绝不猜。

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------
它**不碰碱基**（只动质量字符与名字），也不丢弃任何 read。
质量判定归 :mod:`read_filtering`，质量剪切归 :mod:`quality_trimming`——
那些都由本模块的输出接手。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable

from ...common.fastq import FastqRecord
from ...common.read_names import fix_mgi_name

#: 两种质量编码的偏移。
PHRED_OFFSET_33: Final = 33
PHRED_OFFSET_64: Final = 64

#: Phred+64 → Phred+33 的位移（两者相差 31）。
_PHRED64_TO_33_SHIFT: Final = PHRED_OFFSET_64 - PHRED_OFFSET_33

#: 转换表。上游写作 ``(*mQuality)[i] = max(33, (*mQuality)[i] - 31)``——
#: ``max(33, ...)`` 是**必须的**：字符低于 ``'@'`` 时减 31 会得到小于 33 的值，
#: 而上游把下界钉在 33。照抄。
_PHRED64_TO_33_TABLE: Final[bytes] = bytes(
    max(PHRED_OFFSET_33, code - _PHRED64_TO_33_SHIFT) for code in range(256)
)

#: Phred+64 的质量字符**最少**要到这个 ASCII 值。看到更小的字符就能确定是 33。
_PHRED64_MIN_CODE: Final = PHRED_OFFSET_64


def convert_phred64_to_phred33(quality: bytes) -> bytes:
    """把 Phred+64 的质量串转成 Phred+33（上游 ``Read::convertPhred64To33``）。"""
    return quality.translate(_PHRED64_TO_33_TABLE)


def detect_phred_offset(qualities: Iterable[bytes]) -> int | None:
    """尽力判断这批质量串用的是哪种编码。

    返回：

    - ``33``：**确定**是 Phred+33（看到了 ASCII < 64 的质量字符，
      而 Phred+64 的字符不会小于 ``'@'``）；
    - ``None``：**看不出来**——所有字符都落在两种编码的重叠区间里。

    它**不会**返回 64：没有可靠依据能断定"一定是 Phred+64"。
    要按 64 处理时请显式指定，而不是依赖猜测。
    """
    for quality in qualities:
        if any(code < _PHRED64_MIN_CODE for code in quality):
            return PHRED_OFFSET_33
    return None


@dataclass(frozen=True, slots=True)
class NormalizeConfig:
    """规范化参数。

    | 字段 | 上游 | 说明 |
    | --- | --- | --- |
    | `input_phred` | `--phred64` 的有无 | 输入的质量编码偏移，只能是 33 或 64 |
    | `fix_mgi` | `--fix_mgi_id` | 是否把 ``xxx/1`` 改成 ``xxx /1`` |
    """

    input_phred: int = PHRED_OFFSET_33
    fix_mgi: bool = False

    def __post_init__(self) -> None:
        if self.input_phred not in (PHRED_OFFSET_33, PHRED_OFFSET_64):
            raise ValueError(
                f"input_phred 只能是 {PHRED_OFFSET_33} 或 {PHRED_OFFSET_64}，"
                f"当前为 {self.input_phred}。"
            )


@dataclass(frozen=True, slots=True)
class NormalizeOutcome:
    """一条记录的规范化结果。

    ``renamed`` 与 ``requantified`` 分别表示"名字改过"与"质量被重编码过"——
    两个都是**逐条**的事实，统计里会分开累加。
    """

    record: FastqRecord
    renamed: bool = False
    requantified: bool = False


def normalize_record(record: FastqRecord, config: NormalizeConfig) -> NormalizeOutcome:
    """规范化一条记录；没变时返回**原对象**（避免无谓的复制）。

    ``MGI`` 修复只对 ``.../1``、``.../2`` 这种形状生效，其余名字原样保留。
    """
    name = record.name
    quality = record.quality
    renamed = False
    requantified = False

    if config.fix_mgi:
        fixed = fix_mgi_name(name)
        if fixed != name:
            name = fixed
            renamed = True

    if config.input_phred == PHRED_OFFSET_64:
        quality = convert_phred64_to_phred33(quality)
        requantified = True

    if not renamed and not requantified:
        return NormalizeOutcome(record)
    return NormalizeOutcome(
        FastqRecord(name=name, sequence=record.sequence, quality=quality),
        renamed=renamed,
        requantified=requantified,
    )
