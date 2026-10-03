"""带状局部比对：允许短 indel 与两端软剪裁的逐位对齐与 CIGAR 生成（分片 C + D）。

比"数一数有几个错配"复杂的地方在于两件事：

1. read 与参考之间可能多一个或少一个碱基，一旦有 indel，错位之后的所有位置都会被
   记成错配——所以这里做的是真正的序列比对；
2. read 的两端可能**本来就不属于这段参考**（接头读通、质量掉尾），硬把它们塞进比对
   会把比对"拉歪"：起点错位、还凭空多出 indel/错配。

打分与目标
----------

在候选对角线附近开一条带宽 ``max_indel`` 的带，做**带状局部比对**：

===========  ======
操作         得分
===========  ======
匹配         ``+1``
错配         ``-1``
每个 indel   ``-3``
软剪裁       ``0``（但两端各有 ``max_clip`` 的上限）
===========  ======

**为什么空位比错配更贵。** 一开始两者都取 ``-1``，结果测试抓出一个假 indel：一条接头
读通的 read 被解释成"``4S4M1D100M``"——它用"多覆盖 4 个参考碱基 + 1 个缺失"换到了 103 个
匹配，总分 101 反而超过正确答案（``8S100M``，100 分）。indel 在真实数据里本来就比替换
罕见得多，把空位罚成 ``-3`` 才是符合事实的取舍：真要是一个真实的缺失，它省下来的错配
远多于 3 分，该赢还是赢；而"拼出来的假 indel"就赢不了了。

**为什么软剪裁必须参与打分，而不是比对完再剪。** 我一开始写成"先按编辑距离选路径、
再把两端的异常碱基剪掉"，测试同样立刻抓出真问题：DP 会选一条"把这些碱基吸收成错配"
的等价路径，剪裁根本改不动它——于是接头读通的 read 得到的是一个起点偏了几位、
还带假 indel 的结果。把剪裁放进目标函数里，它才真正参与"怎么对齐"这个决定。

**为什么匹配给正分。** 这决定了"少量的末端错配会不会被剪掉"：一段 3 个碱基里有 1 个
错配，留着得 ``+1+1-1 = +1``（保留，真实变异不会被抹掉）；而单独 1 个末端错配得
``-1``（剪掉）。这正是我们想要的取舍：**内部的变异保住，两端明显不属于这段参考的
碱基剪掉**。

参考两端自由（半全局），read 两端自由但受 ``max_clip`` 限制，因此比对可以只覆盖
read 中间的一段。

并列时的取舍
------------

**明确写死**，不是"随便"：

1. **格内**：得分相等时优先走对角（匹配/错配），其次插入，最后缺失；
2. **终点**：得分相同时取**剪裁更少**的、再取**更靠左**的列、最后取**更靠后**的行。

两条都有测试把可观察结果钉住（见 ``tests/test_dp.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.alignment_io import Cigar, CigarOp

__all__ = ["BandedAlignment", "align_banded"]

#: 不可达的哨兵（用整数而不是 inf，避免浮点参与打分比较）。
_UNREACHABLE = -10 ** 9

#: 打分方案（见模块文档的取舍说明）。
_MATCH_SCORE = 1
_MISMATCH_SCORE = -1
_GAP_SCORE = -3

#: 回溯优先级：对角（0）> 插入（1）> 缺失（2）；``-2`` 表示"这里是对齐的起点"。
_MOVE_DIAGONAL = 0
_MOVE_INSERTION = 1
_MOVE_DELETION = 2
_MOVE_START = -2


@dataclass(frozen=True, slots=True)
class BandedAlignment:
    """一次带内比对的结果。

    ``reference_start`` 是**绝对 0-based 坐标**（调用方给的窗口起点已加回去）；
    ``reference_length`` 是对齐覆盖的参考长度，它等于 ``cigar`` 消费的参考长度——
    indel 存在时**不再等于读长**，这一点与分片 B 的"无缺口"结果不同。
    ``soft_clipped`` 是被软剪裁掉的读段碱基数（两端之和），``query_length``
    仍等于整条 read 的长度（``S`` 也算消费 query）。
    """

    reference_start: int
    reference_length: int
    query_length: int
    mismatches: int
    insertions: int
    deletions: int
    cigar: Cigar
    soft_clipped: int = 0

    def __post_init__(self) -> None:
        if self.cigar.query_length != self.query_length:
            raise ValueError(
                f"CIGAR {self.cigar} 消费的 query 长度 {self.cigar.query_length} "
                f"与 query 长度 {self.query_length} 不符。"
            )
        if self.cigar.reference_length != self.reference_length:
            raise ValueError(
                f"CIGAR {self.cigar} 消费的参考长度 {self.cigar.reference_length} "
                f"与参考覆盖长度 {self.reference_length} 不符。"
            )
        clipped = sum(op.length for op in self.cigar.ops if op.op == "S")
        if clipped != self.soft_clipped:
            raise ValueError(
                f"CIGAR {self.cigar} 里的软剪裁碱基数 {clipped} 与声明的 "
                f"{self.soft_clipped} 不符。"
            )

    @property
    def aligned_length(self) -> int:
        """真正参与比对的读段长度（剪掉软剪裁部分）。"""
        return self.query_length - self.soft_clipped

    @property
    def edit_distance(self) -> int:
        """错配 + 插入碱基 + 缺失碱基（不含软剪裁——剪掉的部分不算错误）。"""
        return self.mismatches + self.insertions + self.deletions


def _run_length_ops(ops: list[str]) -> tuple[CigarOp, ...]:
    """把逐位操作序列压成 CIGAR 的游程。"""
    encoded: list[CigarOp] = []
    for op in ops:
        if encoded and encoded[-1].op == op:
            previous = encoded[-1]
            encoded[-1] = CigarOp(length=previous.length + 1, op=op)
        else:
            encoded.append(CigarOp(length=1, op=op))
    return tuple(encoded)


def align_banded(
    query: str,
    window: str,
    *,
    window_start: int,
    center: int,
    max_indel: int,
    max_clip: int = 0,
    min_aligned_length: int = 0,
) -> BandedAlignment | None:
    """把 ``query`` 比到 ``window``（参考的一段）上，允许最多 ``max_indel`` 的带内漂移。

    参数：
        query: read 序列（负链要先反向互补成与参考同向）。
        window: 参考的一段；坐标由 ``window_start``（绝对 0-based）给出。
        center: 期望的对角线在**窗口内**的列号（即 query 第 0 位对着窗口的第几列）。
        max_indel: 带宽，也就是允许的净漂移；同时决定了空位能有多深。
        max_clip: 每端最多软剪裁多少碱基；0（默认）表示不剪裁。
        min_aligned_length: 至少要有多少个碱基参与比对（剪掉的除外）。

    返回：
        :class:`BandedAlignment`；找不到满足约束的路径时返回 ``None``。

    ``min_aligned_length`` 是靠**收紧剪裁上限逐次重试**来满足的：先按 ``max_clip`` 求最优，
    若参与比对的长度仍不够，就把上限收一格再求一次（最多试 ``max_clip + 1`` 次）。
    这样不必在动态规划里额外跟踪"起点在第几行"，而结果又严格满足约束——
    每次求一遍很便宜（带内格子数量级是 读长 × 带宽）。
    """
    if max_indel < 0:
        raise ValueError(f"带宽不能为负，当前为 {max_indel}。")
    if max_clip < 0:
        raise ValueError(f"剪裁上限不能为负，当前为 {max_clip}。")
    if min_aligned_length < 0:
        raise ValueError(f"最短参与比对长度不能为负，当前为 {min_aligned_length}。")
    length = len(query)
    if length == 0 or not window:
        return None
    for clip in range(min(max_clip, length), -1, -1):
        result = _align_once(
            query,
            window,
            window_start=window_start,
            center=center,
            max_indel=max_indel,
            max_clip=clip,
        )
        if result is not None and result.aligned_length >= min_aligned_length:
            return result
    return None


def _align_once(
    query: str,
    window: str,
    *,
    window_start: int,
    center: int,
    max_indel: int,
    max_clip: int,
) -> BandedAlignment | None:
    """给定剪裁上限求一次带状局部比对（``align_banded`` 的调用内层）。"""
    length = len(query)
    columns = len(window)

    # 每行允许的列区间——带宽就是在这里生效的。
    low = [max(0, center + row - max_indel) for row in range(length + 1)]
    high = [min(columns, center + row + max_indel) for row in range(length + 1)]

    score = [[_UNREACHABLE] * (columns + 1) for _ in range(length + 1)]
    move = [[-1] * (columns + 1) for _ in range(length + 1)]

    # 起点：允许把 read 开头的 i 个碱基剪掉（i ≤ max_clip），从任意列开始、得 0 分。
    for row in range(0, min(max_clip, length) + 1):
        for column in range(low[row], high[row] + 1):
            score[row][column] = 0
            move[row][column] = _MOVE_START

    for row in range(1, length + 1):
        previous_low, previous_high = low[row - 1], high[row - 1]
        current_low, current_high = low[row], high[row]
        for column in range(current_low, current_high + 1):
            # 保留"这一格就是起点"的选项（起点分是 0）。
            best = score[row][column]
            best_move = move[row][column]
            # 对角：匹配或错配
            if previous_low <= column - 1 <= previous_high:
                previous = score[row - 1][column - 1]
                if previous > _UNREACHABLE:
                    value = previous + (
                        _MATCH_SCORE if query[row - 1] == window[column - 1] else _MISMATCH_SCORE
                    )
                    if value > best:
                        best, best_move = value, _MOVE_DIAGONAL
            # 插入：消费 read、不消费参考
            if previous_low <= column <= previous_high:
                previous = score[row - 1][column]
                if previous > _UNREACHABLE and previous + _GAP_SCORE > best:
                    best, best_move = previous + _GAP_SCORE, _MOVE_INSERTION
            # 缺失：消费参考、不消费 read
            if column - 1 >= current_low:
                previous = score[row][column - 1]
                if previous > _UNREACHABLE and previous + _GAP_SCORE > best:
                    best, best_move = previous + _GAP_SCORE, _MOVE_DELETION
            score[row][column] = best
            move[row][column] = best_move

    # 终点：read 末尾可以再剪掉若干碱基，但剪裁总数受 max_clip 限制。
    first_end_row = length - min(max_clip, length)
    best_key: tuple[int, int, int, int] | None = None
    best_row = best_column = -1
    for row in range(first_end_row, length + 1):
        for column in range(low[row], high[row] + 1):
            value = score[row][column]
            if value <= _UNREACHABLE:
                continue
            key = (-value, length - row, column, -row)
            if best_key is None or key < best_key:
                best_key, best_row, best_column = key, row, column
    if best_key is None:
        return None

    # 回溯到起点标记。
    ops: list[str] = []
    row, column = best_row, best_column
    while move[row][column] != _MOVE_START:
        step = move[row][column]
        if step == _MOVE_DIAGONAL:
            ops.append("M")
            row, column = row - 1, column - 1
        elif step == _MOVE_INSERTION:
            ops.append("I")
            row -= 1
        elif step == _MOVE_DELETION:
            ops.append("D")
            column -= 1
        else:  # pragma: no cover - 不可达的格子不会被当成路径
            raise AssertionError("回溯走到了没有来源的格子。")
    ops.reverse()
    start_row, start_column = row, column
    if best_row - start_row <= 0 or best_column - start_column <= 0:
        return None  # 空对齐：没有真正比对上的碱基

    final_ops = ["S"] * start_row + ops + ["S"] * (length - best_row)

    # 按最终操作序列统计（软剪裁不算错配、也不占参考长度）。
    query_index, reference_index = 0, start_column
    mismatches = insertions = deletions = soft_clipped = 0
    for op in final_ops:
        if op == "S":
            query_index += 1
            soft_clipped += 1
        elif op == "M":
            if query[query_index] != window[reference_index]:
                mismatches += 1
            query_index += 1
            reference_index += 1
        elif op == "I":
            insertions += 1
            query_index += 1
        else:  # D
            deletions += 1
            reference_index += 1
    if query_index != length:  # pragma: no cover - 消费关系错了就会在这里炸
        raise AssertionError("回溯出来的操作序列没有把 read 消费完。")
    return BandedAlignment(
        reference_start=window_start + start_column,
        reference_length=reference_index - start_column,
        query_length=length,
        mismatches=mismatches,
        insertions=insertions,
        deletions=deletions,
        cigar=Cigar(ops=_run_length_ops(final_ops)),
        soft_clipped=soft_clipped,
    )
