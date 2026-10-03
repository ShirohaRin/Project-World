"""嵌合比对的分段（分片 A 之一）：一条 read 在参考上被切成的若干段。

上游 JC 线（[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html)
"New junction evidence (JC)"）的第一步是**预处理**：

> 所有带 **> 2 bp 插入或缺失**的比对都被拆成它的子比对。

理由是 >2 bp 的空位在比对里本身就不稳（尤其在简单重复附近），把它拆成两段"各自都很干净的
比对"比硬判一个 indel 更可靠。于是同一条 read 可能贡献多段比对，段与段之间可能有：
- **短的**（≤ 2 bp）空位——仍算在同一段里；
- **长的**（> 2 bp）空位——拆成两段；
- **另一段比对**（read 有多重比对时）。

本模块只做"切"这一件事：把一条 `SamRecord` 切成若干 :class:`AlignmentSegment`。判"它们是不是
嵌合（chimeric）"在 `candidates.py`，打分的在 `scoring.py`。

坐标口径：

1. 位置全是 **0-based**，闭区间（与堆叠、共识调用一致）。
2. **query 区间含段内的插入**（插入的 read 碱基确实属于这一段），但不含前导/尾随的软剪裁，
   也不含贴在段外的插入——段是从**第一个比对碱基**到**最后一个比对碱基**。
3. ``SEQ`` 按本项目一贯的口径当作**与参考同向**（见 `consensus_calling/pileup.py` 的说明），
   所以取 read 上的碱基不需要再反向互补。**这条在本模块里有一条直接后果**：两段必须同链，
   否则"中间那几位 read 碱基朝哪边读"就说不清——见 `candidates.build_junction_sequence`。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...common.alignment_io import SamRecord

__all__ = ["AlignmentSegment", "MIN_SPLIT_GAP", "segments_of"]

#: 空位超过它才把比对拆成两段（上游："> 2 bp"）。
MIN_SPLIT_GAP = 2

_MATCH_OPS = ("M", "=", "X")


@dataclass(frozen=True, slots=True)
class AlignmentSegment:
    """一条 read 的一段连续比对（query 与参考都是 0-based 闭区间）。"""

    query_name: str
    seq_id: str
    query_start: int
    query_end: int
    reference_start: int
    reference_end: int
    is_reverse: bool

    @property
    def query_length(self) -> int:
        """这一段覆盖的 read 碱基数（含段内插入）。"""
        return self.query_end - self.query_start + 1

    @property
    def reference_length(self) -> int:
        """这一段覆盖的参考碱基数。"""
        return self.reference_end - self.reference_start + 1

    @property
    def start_anchor(self) -> int:
        """这条 read **自己**在参考上的起点。

        正链段取 ``reference_start``、负链段取 ``reference_end``——"read 从哪儿开始"要在
        read 自己的方向上量。位置哈希打分（`scoring.py`）数就是它的不同取值个数。
        """
        return self.reference_end if self.is_reverse else self.reference_start

    def query_overlap(self, other: "AlignmentSegment") -> int:
        """两段在 read 上的重叠碱基数（不相交时为 0）。"""
        start = max(self.query_start, other.query_start)
        end = min(self.query_end, other.query_end)
        return max(0, end - start + 1)


def segments_of(record: SamRecord) -> tuple[AlignmentSegment, ...]:
    """把一条 SAM 记录切成若干段（> 2 bp 的空位处分段）。

    未比对的、没有 ``SEQ`` 的、没有 CIGAR 的记录返回空元组；整条 read 里没有任何比对碱基
    （比如只有插入）时也返回空元组——没有比对碱基就没有可表达的位置。
    """
    if record.is_unmapped or record.cigar.is_empty or record.sequence == "*":
        return ()

    # 先把 CIGAR 摊成"块"：每个块记 (类型, query 区间, 参考区间, 空位长度)。
    blocks: list[tuple[str, int, int, int, int, int]] = []
    query = 0
    reference = record.position - 1  # 0-based
    for op in record.cigar.ops:
        kind, span = op.op, op.length
        if kind in _MATCH_OPS:
            blocks.append(("match", query, query + span - 1, reference, reference + span - 1, 0))
            query += span
            reference += span
        elif kind == "I":
            blocks.append(("insert", query, query + span - 1, reference - 1, reference - 1, span))
            query += span
        elif kind == "D":
            blocks.append(("delete", query - 1, query - 1, reference, reference + span - 1, span))
            reference += span
        elif kind == "S":
            query += span  # 软剪裁的碱基在 SEQ 里，只是没比上——不进任何段
        elif kind == "N":
            reference += span  # 跳过的参考区，不承载比对
        elif kind in ("H", "P"):
            continue  # 两侧都不消费
        else:  # pragma: no cover - alignment_io 只允许八种操作，这里兜底
            raise ValueError(f"分段遇到不支持的 CIGAR 操作：{kind!r}。")

    segments: list[AlignmentSegment] = []
    pending: list[tuple[str, int, int, int, int, int]] = []

    def flush() -> None:
        matches = [block for block in pending if block[0] == "match"]
        if matches:
            segments.append(
                AlignmentSegment(
                    query_name=record.query_name,
                    seq_id=record.reference_name,
                    query_start=matches[0][1],
                    query_end=matches[-1][2],
                    reference_start=matches[0][3],
                    reference_end=matches[-1][4],
                    is_reverse=record.is_reverse,
                )
            )
        pending.clear()

    for block in blocks:
        if block[0] != "match" and block[5] > MIN_SPLIT_GAP:
            flush()  # 长空位：在这里断开
        else:
            pending.append(block)
    flush()
    return tuple(segments)
