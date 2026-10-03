"""原生双端 overlap 分析的正确性测试：与 Python 实现逐位对拍。

这个工具没有文件进出（一次只算一对 read），因此对拍的粒度就是"一对 read"：
两侧返回的是**同一个** :class:`OverlapResult` 类型，直接 ``==`` 比整体。

用例分三层：

1. **上游自带向量**（``OverlapAnalysis::test()`` 的两组）——最硬的证据，
   逐位核对错位量、重叠长度与错配数；
2. **构造几何**——正向（插入片段短于读长）、反向（接头进了 rc2 的尾部）、
   重叠不足 ``require``、不重叠、带缺口，各种情况两侧结论一致；
3. **随机批量**——多组参数（含 ``allow_gap``）下逐对比对，覆盖扫描边界。

原生库未编译时整个文件跳过。
"""

from __future__ import annotations

import ctypes
import random

import pytest

from modules.bio_analysis_function.common.native import abi, library_path
from modules.bio_analysis_function.common.paired_overlap import (
    GAP_ONLY_VECTOR,
    UPSTREAM_TEST_VECTORS,
    OverlapConfig,
    OverlapResult,
    analyze_overlap,
)
from modules.bio_analysis_function.common.sequences import reverse_complement

pytestmark = pytest.mark.skipif(
    not library_path().exists(),
    reason="原生库未编译；先运行 common/native/tools/compile.py",
)

BASES = "ACGT"


def random_sequence(length: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return "".join(rng.choices(BASES, k=length)).encode("ascii")


def assert_matches_python(
    read1: bytes,
    read2: bytes,
    config: OverlapConfig | None = None,
):
    """两侧结论必须完全相同（同一个类型，整体比较）。"""
    native = abi.analyze_overlap(read1, read2, config)
    python_result = analyze_overlap(read1, read2, config)

    assert native == python_result
    return native


# --------------------------------------------------------------------------
# 上游自带向量
# --------------------------------------------------------------------------


@pytest.mark.parametrize("vector", UPSTREAM_TEST_VECTORS)
def test_native_matches_upstream_vector(vector: dict) -> None:
    """上游 ``OverlapAnalysis::test()`` 的两组向量：逐位一致。"""
    config = OverlapConfig(
        diff_limit=vector["diff_limit"],
        require=vector["require"],
        diff_percent_limit=vector["diff_percent_limit"],
    )

    result = assert_matches_python(vector["read1"], vector["read2"], config)

    offset, overlap_len, diff = vector["expect"]
    assert result.overlapped
    assert (result.offset, result.overlap_len, result.diff) == (offset, overlap_len, diff)


# --------------------------------------------------------------------------
# 构造几何
# --------------------------------------------------------------------------


def _cases() -> dict[str, tuple[bytes, bytes, OverlapConfig | None, bool]]:
    """构造好的几何：名称 → (read1, read2, 参数, 是否应当重叠)。"""
    fragment = random_sequence(100, seed=1)
    forward = (fragment[0:80], reverse_complement(fragment[20:100]), None, True)

    prefix = random_sequence(20, seed=2)
    read1 = random_sequence(80, seed=3)
    reverse = (read1, reverse_complement(prefix + read1), None, True)

    short = (
        fragment[0:80],
        reverse_complement(fragment[70:100]),  # 只重叠 10 个碱基
        OverlapConfig(require=5),
        True,
    )

    independent = (random_sequence(150, seed=5), random_sequence(150, seed=6), None, False)

    # 末端附近多一个碱基：无缺口那一轮就能认下（判定只看前 50 个碱基，
    # 插入在 78 号位，前 50 个完全一致），错配数报的是整段的真实值。
    read1_gap = random_sequence(80, seed=4)
    shifted = read1_gap[:78] + b"A" + read1_gap[78:]
    tail_insertion = (
        read1_gap,
        reverse_complement(shifted),
        None,
        True,
    )

    # 中间多一个碱基：无缺口判定（前 50 个里有 10 个错配）过不去；缺口那一轮的
    # 前缀累计会先超限、直接返回 -1，因此两侧都报"不重叠"。
    middle_insertion = (
        read1_gap,
        reverse_complement(read1_gap[:40] + b"A" + read1_gap[40:]),
        None,
        False,
    )

    return {
        "正向_插入片段短于读长": forward,
        "反向_接头进了反向互补的尾部": reverse,
        "重叠不足_放宽门槛后成立": short,
        "完全不重叠": independent,
        "末端单碱基插入_无缺口路径认下": tail_insertion,
        "中间单碱基插入_两侧都不认": middle_insertion,
    }


@pytest.mark.parametrize("case_name", sorted(_cases()))
def test_native_matches_python_on_constructed_geometry(case_name: str) -> None:
    read1, read2, config, expected_overlap = _cases()[case_name]

    result = assert_matches_python(read1, read2, config)

    assert result.overlapped is expected_overlap


def test_native_reports_forward_geometry_exactly() -> None:
    """正向：错位量、重叠长度、片段长度都要与 Python 侧给出同一组数。"""
    fragment = random_sequence(100, seed=1)
    read1 = fragment[0:80]
    read2 = reverse_complement(fragment[20:100])

    result = assert_matches_python(read1, read2)

    assert (result.offset, result.overlap_len, result.diff) == (20, 60, 0)
    # 片段长于读长：两条 read 只在中间重叠，片段长 = 80 + 80 - 60。
    assert result.insert_size == 100


def test_native_reports_reverse_geometry_exactly() -> None:
    """反向：错位量为负，重叠长度等于 read1 的长度。"""
    prefix = random_sequence(20, seed=2)
    read1 = random_sequence(80, seed=3)

    result = assert_matches_python(read1, reverse_complement(prefix + read1))

    assert (result.offset, result.overlap_len, result.diff) == (-20, 80, 0)
    # 两端读穿：重叠长度本身就是片段长度。
    assert result.insert_size == 80


# --------------------------------------------------------------------------
# 随机批量
# --------------------------------------------------------------------------

_CONFIGS = (
    None,  # 默认（diff_limit=5、require=30、比例 0.2）
    OverlapConfig(diff_limit=0, diff_percent_limit=0.0),
    OverlapConfig(diff_limit=2, require=5, diff_percent_limit=0.2),
    OverlapConfig(require=10, diff_percent_limit=0.3),
    OverlapConfig(allow_gap=True),
)

_CASES = ("同一个片段_不同读长", "带单碱基插入", "互不相关")


def _random_pairs(case_name: str, count: int) -> list[tuple[bytes, bytes]]:
    """固定种子的成对 read，三种形态各造一批。"""
    rng = random.Random(20260918)
    pairs: list[tuple[bytes, bytes]] = []
    for _ in range(count):
        length1 = rng.randint(40, 151)
        length2 = rng.randint(40, 151)
        if case_name == "同一个片段_不同读长":
            fragment = "".join(rng.choices(BASES, k=200))
            start = rng.randint(0, 60)
            pairs.append(
                (
                    fragment[:length1].encode("ascii"),
                    reverse_complement(fragment[start : start + length2].encode("ascii")),
                )
            )
        elif case_name == "带单碱基插入":
            base = "".join(rng.choices(BASES, k=160))
            position = rng.randint(1, 150)
            inserted = base[:position] + rng.choice(BASES) + base[position:]
            pairs.append(
                (
                    base[:length1].encode("ascii"),
                    reverse_complement(inserted[:length2].encode("ascii")),
                )
            )
        else:
            pairs.append(
                (
                    "".join(rng.choices(BASES, k=length1)).encode("ascii"),
                    "".join(rng.choices(BASES, k=length2)).encode("ascii"),
                )
            )
    return pairs


@pytest.mark.parametrize("case_name", _CASES)
@pytest.mark.parametrize("config", _CONFIGS)
def test_native_matches_python_on_random_pairs(case_name: str, config) -> None:
    """随机批量逐对比对，覆盖四轮扫描的起点与边界。"""
    for read1, read2 in _random_pairs(case_name, 60):
        assert_matches_python(read1, read2, config)


def test_native_matches_python_when_gap_changes_the_conclusion() -> None:
    """缺口那一轮确实会翻盘——窗口很窄，但不是死代码。

    这组向量来自公共层（``GAP_ONLY_VECTOR``）：两条 read 之间隔着单个碱基
    indel、重叠区 48 个碱基、indel 落在靠末端的一小段里。三条同时成立才翻得动：
    重叠区不长、indel 靠末端、无缺口那一轮恰好过不去。详尽的说明写在公共层
    那份测试里，这里只核对原生侧给出同一结论。
    """
    vector = GAP_ONLY_VECTOR
    strict_config = OverlapConfig(
        diff_limit=vector["diff_limit"], require=vector["require"]
    )
    gap_config = OverlapConfig(
        diff_limit=vector["diff_limit"], require=vector["require"], allow_gap=True
    )

    strict = assert_matches_python(vector["read1"], vector["read2"], strict_config)
    lenient = assert_matches_python(vector["read1"], vector["read2"], gap_config)

    assert not strict.overlapped
    assert (lenient.offset, lenient.overlap_len, lenient.diff) == vector["expect"]
    assert lenient.has_gap


def test_native_matches_python_on_gap_cases() -> None:
    """带缺口那一轮：逐对比对，并确认这条路径真的被走到过。

    构造方式是"两条 read 中有一条带单碱基 indel"（插入 / 缺失各一半），读长与
    重叠长度都取短一些——缺口那一轮只在重叠区不长的窗口里才有机会翻盘。
    """
    rng = random.Random(31415926)
    with_gap = 0

    for _ in range(150):
        length = rng.randint(10, 60)
        base = "".join(rng.choices(BASES, k=length + 2))
        position = rng.randint(1, length)
        if rng.random() < 0.5:
            mutated = base[:position] + rng.choice(BASES) + base[position:]
        else:
            mutated = base[:position] + base[position + 1 :]
        read1 = base[: rng.randint(5, length)].encode("ascii")
        read2 = reverse_complement(
            mutated[: rng.randint(5, len(mutated))].encode("ascii")
        )
        config = OverlapConfig(
            require=rng.choice((5, 10, 15)), diff_limit=8, allow_gap=True
        )

        if assert_matches_python(read1, read2, config).has_gap:
            with_gap += 1

    assert with_gap > 0, "这批用例一个缺口结论都没走到，说明这条路径没被覆盖"


# --------------------------------------------------------------------------
# 边界与错误处理
# --------------------------------------------------------------------------


def test_native_matches_python_on_empty_and_short_reads() -> None:
    """退化输入两侧完全一致。

    其中有两处上游的怪处，如实记录、不修：

    - 只要有一条 read 是空串、另一条够长（长到能进扫描循环），就会在 ``offset=0``
      处拿一个**长度为 0 的"重叠"**通过判定——判定比 0 个碱基，错配上限也是 0。
      实际数据不会有空 read，这里只保证两侧一致。
    - ``require`` 放宽到很小时，几百个碱基里凑出 4 个连续相同碱基是常事，
      于是会认出一段 4 碱基的重叠。这正是 ``require`` 默认取 30 的理由。
    """
    # 两条都空 / 都短于 require：进不了扫描循环，不重叠
    for read1, read2 in ((b"", b""), (b"ACGT", b"ACGT")):
        result = assert_matches_python(read1, read2)
        assert not result.overlapped
        assert result.insert_size == 0

    # 空串 + 长 read：长度为 0 的退化"重叠"
    degenerate = assert_matches_python(random_sequence(150, seed=8), b"")
    assert degenerate == OverlapResult(
        True, 0, 0, 0, False, read1_length=150, read2_length=0
    )
    # 把两条 read 对调（空 read1 + 长 read2）同样是长度为 0 的退化"重叠"。
    # 这里只比结论字段：两条 read 的长度对调了，长度字段自然不同。
    mirrored = assert_matches_python(b"", random_sequence(150, seed=8))
    assert (mirrored.overlapped, mirrored.offset, mirrored.overlap_len) == (
        True,
        0,
        0,
    )

    # require 放宽到 1：4 个连续相同的碱基就会被认成重叠
    loose = assert_matches_python(
        b"ACGT", random_sequence(150, seed=9), OverlapConfig(require=1)
    )
    assert loose.overlapped
    assert loose.overlap_len == 4


def test_native_layer_rejects_invalid_options() -> None:
    """原生侧的参数校验。

    Python 的 ``OverlapConfig`` 会先拦一道，公开包装函数因此不可能传出非法值；
    这里直接调 C 入口（用私有的选项结构体），确认后备校验确实生效——
    将来若有别的语言直接调这个 C 函数，这道校验就是唯一的防线。
    """
    library = abi.load_library()
    result = abi._OverlapResult()

    for fields, field_name in (
        ((5, 0, 2000, 0), "require"),
        ((-1, 30, 2000, 0), "diff_limit"),
        ((5, 30, 15000, 0), "diff_percent"),
    ):
        options = abi._OverlapOptions(
            diff_limit=fields[0],
            require=fields[1],
            diff_percent_bp=fields[2],
            allow_gap=fields[3],
        )
        status = library.bio_analyze_overlap(
            b"ACGT", b"ACGT", ctypes.byref(options), ctypes.byref(result)
        )

        assert status == abi.BIO_ERR_ARGUMENT
        assert field_name in abi._decode_message(result.message)


def test_native_rejects_null_reads() -> None:
    """空指针是参数错误；空**串**则是合法输入（上面已覆盖）。"""
    library = abi.load_library()
    result = abi._OverlapResult()

    status = library.bio_analyze_overlap(b"ACGT", None, None, ctypes.byref(result))

    assert status == abi.BIO_ERR_ARGUMENT
    assert "空指针" in abi._decode_message(result.message)
