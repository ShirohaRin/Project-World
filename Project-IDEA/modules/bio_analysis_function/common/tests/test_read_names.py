"""read 名字解析的测试。

两个 index 取值函数的完整行为在 UMI 提取的测试里已经覆盖（它先用到），
这里只补最要紧的几条与 ``fix_mgi_name``——它们是公共层自己的测试，
将来别的模块引用时不必回头翻子模块的测试。
"""

from __future__ import annotations

from modules.bio_analysis_function.common.read_names import (
    first_index,
    fix_mgi_name,
    last_index,
)

_NAME = "NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA"


def test_first_and_last_index() -> None:
    assert first_index(_NAME) == "TATAGCCT"
    assert last_index(_NAME) == "GGTCCCGA"


def test_short_names_give_empty() -> None:
    for name in ("", "a", "abc"):
        assert first_index(name) == ""
        assert last_index(name) == ""


def test_fix_mgi_name_inserts_a_space() -> None:
    assert fix_mgi_name("MGI-123/1") == "MGI-123 /1"
    assert fix_mgi_name("MGI-123/2") == "MGI-123 /2"


def test_fix_mgi_name_leaves_other_shapes_alone() -> None:
    for name in ("read1", "read/3", "read/", "read 1:N:0:ACGT"):
        assert fix_mgi_name(name) == name


def test_fix_mgi_name_only_looks_at_the_last_two_characters() -> None:
    """上游只看末两位，所以名字就是 ``/1`` 也会被改。"""
    assert fix_mgi_name("/1") == " /1"
