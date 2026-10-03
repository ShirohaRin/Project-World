"""umi_processing：把 UMI 提取出来，挂到 read 名字上。

**设计目标：与 fastp 1.3.x 的 ``UmiProcessor::process`` / ``addUmiToName``
（``src/umiprocessor.cpp``）逐位一致**，其中用到的那三个 ``Read`` 方法
（``trimFront`` / ``firstIndex`` / ``lastIndex``，``src/read.cpp``）也一并照搬。
改这里之前请先读同目录的 `umi_processing.md`。

**UMI 是什么、为什么要动它**：UMI（unique molecular identifier）是建库时给每个原始
DNA 分子贴的一小段随机序列。同一个分子在 PCR 里被复制多次，它们的 read 带着**相同**的
UMI；不同分子的 UMI 不同。因此 UMI 是"区分真重复与 PCR 重复"的依据——但它必须**先被
搬到 read 名字里**（而不是留在序列开头），下游工具才用得上；留在序列里还会干扰比对。

本算法做的就是这件事：**按指定位置取出 UMI、写进 read 名，并把序列里那一段剪掉**。
它不比对、不聚合，也不改变碱基内容之外的任何东西。

## 六种 UMI 来源（上游的 ``--umi_loc``）

| 取值 | UMI 来自 | 需要 R2 | 会剪序列吗 |
| --- | --- | --- | --- |
| `index1` | R1 名字里的**第一段** index | 否 | 否（index 不在序列里） |
| `index2` | R2 名字里的**最后一段** index | **是** | 否 |
| `read1` | R1 序列开头的 ``length`` 个碱基 | 否 | **是**（再额外跳过 ``skip`` 个） |
| `read2` | R2 序列开头的 ``length`` 个碱基 | **是** | **是** |
| `per_index` | R1 的第一段 index，并上 R2 的最后一段 index | 否（单端时只取 R1） | 否 |
| `per_read` | R1 与 R2 各自的序列开头 | 否（单端时只取 R1） | **是** |

`index1`/`index2`/`read1`/`read2` 四种模式下，取到的 UMI 会被挂到**两条** read 上
（双端时）；`per_index`/`per_read` 则是"两条的拼在一起、同样挂到两条上"。

## 两个容易踩的地方

1. **名字里带 index 的格式**：Illumina 的 read 名形如
   ``...:1097 1:N:0:TATAGCCT+GGTCCCGA``，空格后面才是 index 段，其中用 ``+`` 分成
   两段。解析规则完全照搬上游（从右往左扫），不自行发明。
2. **``trimFront`` 永远留至少 1 个碱基**（上游写成 ``min(length-1, len)``）。
   因此 UMI 比 read 还长的输入不会产生空 read，而是留下 1 个碱基。

本文件只做一对（或一条）read 的处理，不读写文件；文件级接口见 ``runner.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...common.fastq import FastqRecord
from ...common.read_names import first_index, last_index

#: UMI 的六种来源，与上游 ``UMI_LOC_*`` 一一对应。
UMI_LOCATIONS: Final = (
    "index1",
    "index2",
    "read1",
    "read2",
    "per_index",
    "per_read",
)

#: 需要 R2 才有意义的来源。
_NEEDS_READ2: Final = ("index2", "read2")

#: 会把 UMI 从序列里剪掉的来源（剪完还要再跳过 ``skip`` 个碱基）。
_TRIMMING_LOCATIONS: Final = ("read1", "read2", "per_read")


@dataclass(frozen=True, slots=True)
class UmiConfig:
    """UMI 提取参数，字段与上游 ``UMIOptions`` 对应。

    | 字段 | fastp 对应物 | 默认 | 说明 |
    | --- | --- | --- | --- |
    | `location` | `--umi_loc` | `read1` | UMI 从哪来，取值见 :data:`UMI_LOCATIONS` |
    | `length` | `--umi_len` | 0 | UMI 长度（`read1`/`read2`/`per_read` 用；0 表示不取） |
    | `skip` | `--umi_skip` | 0 | 剪掉 UMI 之后**再跳过**几个碱基 |
    | `prefix` | `--umi_prefix` | 空 | UMI 前的前缀，非空时写成 ``prefix_UMI`` |
    | `delimiter` | `--umi_delim` | `:` | 标签与名字之间的分隔符 |
    """

    location: str = "read1"
    length: int = 0
    skip: int = 0
    prefix: str = ""
    delimiter: str = ":"

    def __post_init__(self) -> None:
        if self.location not in UMI_LOCATIONS:
            raise ValueError(
                f"location 必须是 {UMI_LOCATIONS} 之一，当前为 {self.location!r}。"
            )
        if self.length < 0:
            raise ValueError(f"length 不能为负数，当前为 {self.length}。")
        if self.skip < 0:
            raise ValueError(f"skip 不能为负数，当前为 {self.skip}。")
        if not self.delimiter:
            raise ValueError("delimiter 不能是空串。")


@dataclass(frozen=True, slots=True)
class UmiResult:
    """一次 UMI 处理的结果。

    ``read2`` 为 ``None`` 表示这次是单端处理——输入没有 R2，输出的 R2 也是 ``None``。
    ``umi`` 是实际取到的 UMI 字符串（可能为空：来源是 index 而名字里没有 index 时）。
    """

    read1: FastqRecord
    read2: FastqRecord | None = None
    umi: str = ""


#: ``first_index`` / ``last_index`` 已上移到公共层（``common/read_names.py``）——
#: 它们现在有两个使用方：本模块（按 index 取 UMI）与 reads 过滤（按 index
#: 黑名单过滤）。这里仍然从本模块可以导入到它们，因为顶部做了 re-export。


def trim_front(sequence: bytes, quality: bytes, count: int) -> tuple[bytes, bytes]:
    """从头部剪掉 ``count`` 个碱基（上游 ``Read::trimFront``）。

    **它最多留 1 个碱基**：上游写作 ``len = min(length - 1, len)``，所以正常的 read
    不会被剪空。长度为 0 时上游算出 -1、在 C++ 里等于清空一个本就空的串，这里等价处理。
    """
    effective = min(len(sequence) - 1, count)
    if effective <= 0:
        return sequence, quality
    return sequence[effective:], quality[effective:]


def add_umi_to_name(name: str, umi: str, config: UmiConfig) -> str:
    """把 UMI 标签挂到 read 名上（上游 ``UmiProcessor::addUmiToName``）。

    标签形如 ``:UMI`` 或 ``:prefix_UMI``，插在名字里**第一个空格之前**——空格之后
    是测序仪的注释段（含 index），UMI 不能插到它后面；名字里没有空格就追加到末尾。
    """
    if config.prefix:
        tag = f"{config.delimiter}{config.prefix}_{umi}"
    else:
        tag = f"{config.delimiter}{umi}"
    space = name.find(" ")
    if space < 0:
        return name + tag
    return name[:space] + tag + name[space:]


def process_umi(
    read1: FastqRecord,
    read2: FastqRecord | None = None,
    config: UmiConfig | None = None,
) -> UmiResult:
    """按配置取出 UMI、挂到名字上，必要时把序列里的 UMI 剪掉。

    参数：
        read1: 正向 read（必填）。
        read2: 反向 read；单端数据传 ``None``。`index2` / `read2` 两种来源没有它
            就取不到 UMI（会得到空串，与上游一致）。
        config: 参数；``None`` 表示默认（``read1``、长度 0，即什么都不取）。

    返回：
        :class:`UmiResult`。**任何情况下都返回结果**——UMI 取不到时名字不动。
    """
    settings = config or UmiConfig()

    umi = ""
    name1 = read1.name
    sequence1 = read1.sequence
    quality1 = read1.quality
    name2 = read2.name if read2 is not None else None
    sequence2 = read2.sequence if read2 is not None else None
    quality2 = read2.quality if read2 is not None else None
    merged: str | None = None

    location = settings.location
    if location == "index1":
        umi = first_index(name1)
    elif location == "index2" and name2 is not None:
        umi = last_index(name2)
    elif location == "read1":
        take = min(len(sequence1), settings.length)
        umi = sequence1[:take].decode("ascii", "replace")
        sequence1, quality1 = trim_front(
            sequence1, quality1, take + settings.skip
        )
    elif location == "read2" and sequence2 is not None:
        take = min(len(sequence2), settings.length)
        umi = sequence2[:take].decode("ascii", "replace")
        sequence2, quality2 = trim_front(
            sequence2, quality2, take + settings.skip
        )
    elif location == "per_index":
        merged = first_index(name1)
        if name2 is not None:
            merged = merged + "_" + last_index(name2)
    elif location == "per_read":
        take1 = min(len(sequence1), settings.length)
        merged = sequence1[:take1].decode("ascii", "replace")
        sequence1, quality1 = trim_front(
            sequence1, quality1, take1 + settings.skip
        )
        if sequence2 is not None:
            take2 = min(len(sequence2), settings.length)
            merged = merged + "_" + sequence2[:take2].decode("ascii", "replace")
            sequence2, quality2 = trim_front(
                sequence2, quality2, take2 + settings.skip
            )

    if merged is not None:
        # per_index / per_read：拼好的串无条件挂上去（上游就是这么写的，
        # 即使它是空串也会留下一个分隔符）。
        name1 = add_umi_to_name(name1, merged, settings)
        if name2 is not None:
            name2 = add_umi_to_name(name2, merged, settings)
    elif umi:
        # 其余四种：取到的 UMI 挂到两条 read 上（空的就不挂）。
        name1 = add_umi_to_name(name1, umi, settings)
        if name2 is not None:
            name2 = add_umi_to_name(name2, umi, settings)

    result1 = FastqRecord(name1, sequence1, quality1)
    result2 = (
        FastqRecord(name2, sequence2, quality2)  # type: ignore[arg-type]
        if name2 is not None
        else None
    )
    return UmiResult(read1=result1, read2=result2, umi=merged if merged is not None else umi)


#: 上游 ``Read::test()`` 的自带向量：它只验证 ``lastIndex``，这里照搬过来当锚点。
UPSTREAM_INDEX_VECTOR: Final = {
    "name": "NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA",
    "first_index": "TATAGCCT",
    "last_index": "GGTCCCGA",
}
