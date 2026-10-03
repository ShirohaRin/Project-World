"""read 名字的解析与规范化。

**这是工具，不是算法**（见 `开发规则.md` 3.2 第 1 类）：它没有自己的输入输出
契约，只是别的算法里重复出现的那几行。目前的使用方：
UMI 提取（按 index 取 UMI）、reads 过滤（按 index 黑名单过滤）。

**产出**：给定一个 read 名，取出它里面的 index 段，或返回修好格式的名字。
没有文件、没有统计。

名字的格式假设完全照抄上游：``... 1:N:0:TATAGCCT+GGTCCCGA`` 这类
Illumina 风格的 read 名。上游的 ``Read::firstIndex`` / ``lastIndex``
是按"倒数第 3 个字符往左扫"实现的，不依赖任何字段位置解析，
所以对本项目里常见的 ``@`` 省略写法也成立。
"""

from __future__ import annotations

from typing import Final

#: index 段的两种分隔符。上游 ``lastIndex`` 遇到其中任何一个都收尾。
_INDEX_SEPARATORS: Final = (":", "+")


def first_index(name: str) -> str:
    """取 read 名里**第一段** index（上游 ``Read::firstIndex``）。

    从倒数第 3 个字符往左扫：遇到 ``+`` 记下右边界，再遇到 ``:`` 就返回两段之间
    的部分。名字短于 5 个字符时返回空串（上游如此）。

    例：``... 1:N:0:TATAGCCT+GGTCCCGA`` → ``TATAGCCT``。
    """
    # 上游的 mName 带行首 '@'，而它的长度判断依赖这一点，所以这里补回去再解析。
    text = "@" + name
    length = len(text)
    if length < 5:
        return ""
    end = length
    for index in range(length - 3, -1, -1):
        if text[index] == "+":
            end = index - 1
        if text[index] == ":":
            return text[index + 1 : end + 1]
    return ""


def last_index(name: str) -> str:
    """取 read 名里**最后一段** index（上游 ``Read::lastIndex``）。

    从倒数第 3 个字符往左扫，遇到 ``:`` 或 ``+`` 就返回它右边到末尾的部分。
    名字短于 5 个字符时返回空串。

    例：``...TATAGCCT+GGTCCCGA`` → ``GGTCCCGA``。
    """
    text = "@" + name
    length = len(text)
    if length < 5:
        return ""
    for index in range(length - 3, -1, -1):
        if text[index] in _INDEX_SEPARATORS:
            return text[index + 1 :]
    return ""


def fix_mgi_name(name: str) -> str:
    """把 MGI 风格的 ``xxx/1`` 改成 ``xxx /1``（上游 ``Read::fixMGI``）。

    在斜杠前面**插一个空格**，让名字变成 ``<主名> <分隔><编号>`` 的形式——
    很多下游 BAM 工具要求这种形态。名字不是 ``.../1`` 或 ``.../2`` 时原样返回。

    ``name`` 不含行首的 ``@``（与 :class:`FastqRecord` 一致），因此
    ``/1`` 就是最后两个字符。
    """
    if len(name) >= 2 and name[-2] == "/" and name[-1] in ("1", "2"):
        return name[:-2] + " " + name[-2:]
    return name
