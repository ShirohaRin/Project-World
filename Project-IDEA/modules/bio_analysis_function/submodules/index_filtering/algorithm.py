"""index_filtering：按 index（barcode）黑名单过滤 reads。

**与 fastp 1.3.x 逐位对齐**：判定照 ``Filter::filterByIndex`` 与
``Filter::match``。

--------------------------------------------------------------------------
它解决什么问题
--------------------------------------------------------------------------
一次测序 run 里通常混着多个样本，靠 index（也叫 barcode、标签序列）区分。
建库或拆分出错时会冒出**不属于本样本**的 index——最常见的是 index 交叉污染、
或 index hopping（标签跳跃）。把它们在比对之前筛掉，能省掉后面一大堆假结果。

用法是**给一份黑名单**（每行一个 index 序列）：命中黑名单的 read 被丢掉。
上游没有"白名单"模式：要"只保留某些 index"，就把其余的列成黑名单。

--------------------------------------------------------------------------
怎么判"命中"
--------------------------------------------------------------------------
逐位比较，**只比两者中较短的长度**，错配数不超过阈值就算命中（上游 ``match``）：

- 阈值默认 **0**（完全一致）；
- 若黑名单里写的是 ``ACGT`` 而 read 的 index 是 ``AC``，只比前 2 位——
  全同就算命中。这一条容易看漏，但它是上游的行为。

双端时两端的 index 来源不同：

| 端 | 取哪段 index | 对哪份黑名单 |
| --- | --- | --- |
| R1 | 名字里的**第一段**（``firstIndex``） | ``blacklist1`` |
| R2 | 名字里的**最后一段**（``lastIndex``） | ``blacklist2`` |

任一端命中，**整对**丢弃（保持 R1/R2 对齐）。

--------------------------------------------------------------------------
与相邻算法的分工
--------------------------------------------------------------------------
它和 :mod:`read_filtering` 都叫"过滤"，但判据完全不同：那边看的是
**read 本身的质量**，这边看的是**read 的标签属于哪个样本**。上游也把它们
放在处理链的不同位置（index 过滤在最前，质量过滤在剪切之后）。

:func:`matches_blacklist` 与名字解析来自公共层
:mod:`~modules.bio_analysis_function.common.read_names`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence

from ...common.read_names import first_index, last_index

#: 错配阈值的默认值。上游 ``--filter_by_index_threshold`` 默认 0（完全一致）。
DEFAULT_THRESHOLD: Final = 0


@dataclass(frozen=True, slots=True)
class IndexFilterConfig:
    """过滤参数。

    | 字段 | 上游 | 说明 |
    | --- | --- | --- |
    | `blacklist1` | `--filter_by_index1`（文件，每行一个） | 与 R1 的第一段 index 比对 |
    | `blacklist2` | `--filter_by_index2` | 与 R2 的最后一段 index 比对；单端时忽略 |
    | `threshold` | `--filter_by_index_threshold` | 允许的错配数，默认 0 |

    两份黑名单都空时**不过滤任何 read**（上游同样如此）。
    """

    blacklist1: tuple[str, ...] = ()
    blacklist2: tuple[str, ...] = ()
    threshold: int = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError(f"threshold 不能为负数，当前为 {self.threshold}。")

    @property
    def enabled(self) -> bool:
        """两份黑名单都空时什么也不做。"""
        return bool(self.blacklist1 or self.blacklist2)


def matches_blacklist(
    blacklist: Sequence[str], target: str, threshold: int
) -> bool:
    """``target`` 是否命中黑名单（上游 ``Filter::match``）。

    **只比两者中较短的长度**：``entry`` 与 ``target`` 逐位比较，错配数一旦
    超过阈值就提前退出。因为提前退出时错配数已经大于阈值，
    所以最后用 ``<= threshold`` 判断是安全的。
    """
    for entry in blacklist:
        diff = 0
        for index in range(min(len(entry), len(target))):
            if entry[index] != target[index]:
                diff += 1
                if diff > threshold:
                    break
        if diff <= threshold:
            return True
    return False


def is_filtered(name1: str, name2: str | None, config: IndexFilterConfig) -> bool:
    """这一条（或一对）该不该丢。

    ``name2`` 为 ``None`` 表示单端——此时只看 R1 的第一段 index 与
    ``blacklist1``。
    """
    if config.blacklist1 and matches_blacklist(
        config.blacklist1, first_index(name1), config.threshold
    ):
        return True
    if name2 is not None and config.blacklist2 and matches_blacklist(
        config.blacklist2, last_index(name2), config.threshold
    ):
        return True
    return False
