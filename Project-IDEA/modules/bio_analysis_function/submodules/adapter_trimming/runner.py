"""文件级接口：把接头裁剪跑在一整个 FASTQ 上。

**接头来源**（对应产品入口里的"接头来源"选项）：

| 模式 | 对应 fastp | 说明 |
| --- | --- | --- |
| 手动指定 | `--adapter_sequence` | 直接给一条接头序列。**最可靠**，推荐用法，因为接头取决于试剂盒 |
| 候选表 | `--adapter_fasta` | 给一组候选接头依次尝试；内置的 234 条已知接头表也可以当候选表 |
| 自动检测 | `--adapter_sequence=auto` | 先用公共层的接头检测（:mod:`...common.adapter_detection`）从数据里找出接头，再按它裁剪 |

读与写复用公共层的 :func:`~modules.bio_analysis_function.common.fastq.process_fastq`，
因此分块、压缩、统计口径与失败清理都与其它预处理算法一致。

**本算法不丢弃 read**：长度被裁到 0 的（接头二聚体）也会原样写出——上游正是如此，
它把"这条 read 该不该丢"留给后面的过滤步骤（长度过滤会按 0 长度丢弃）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ...common.adapter_detection import (
    AdapterDetection,
    AdapterDetectionConfig,
    detect_adapter_from_file,
)
from ...common.fastq import FastqRecord, process_fastq
from .algorithm import AdapterTrimConfig, trim_adapter, trim_adapters

#: 报告里最多列出几种"切下来的接头序列"。
_TOP_ADAPTER_LIMIT = 5


@dataclass(frozen=True, slots=True)
class AdapterTrimSummary:
    """一次接头裁剪的统计结果。

    计数口径与模块其它预处理算法一致（``total = kept + dropped``，本算法 dropped 恒为 0）：
    另外多出三项——被裁过的 read 数、裁掉的碱基数、以及**切下来的接头序列**的出现次数
    （上游把它汇总成"检出了哪些接头"的报告）。
    """

    total_reads: int
    kept_reads: int
    dropped_reads: int
    trimmed_reads: int
    trimmed_bases: int
    bases_before: int
    bases_after: int
    adapter: str | None
    adapter_source: str | None
    top_adapters: tuple[tuple[str, int], ...] = ()
    detection: AdapterDetection | None = None

    @property
    def trimmed_rate(self) -> float:
        """被裁过的 read 占比。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.trimmed_reads / self.total_reads

    @property
    def bases_removed(self) -> int:
        return self.bases_before - self.bases_after

    def render(self) -> str:
        """渲染成便于阅读与日志记录的多行文本。"""
        lines = [f"输入 read 数：{self.total_reads}"]
        if self.adapter_source is None:
            lines.append("未执行裁剪：没有可用的接头序列")
            return "\n".join(lines)
        source = {
            "given": "手动指定",
            "list": "候选表",
            "detected": "自动检测",
        }.get(self.adapter_source, self.adapter_source)
        lines.append(f"接头（{source}）：{self.adapter}")
        lines.append(
            f"裁过接头的 read：{self.trimmed_reads}（{self.trimmed_rate:.2%}），"
            f"去掉碱基 {self.bases_removed}"
        )
        for sequence, count in self.top_adapters:
            lines.append(f"  · 切下来的 {sequence}：{count} 次")
        return "\n".join(lines)


def _resolve_adapter(
    input_path: Path,
    adapter: str | None,
    adapters: Sequence[str],
    auto_detect: bool,
    detect_config: AdapterDetectionConfig | None,
) -> tuple[str | tuple[str, ...] | None, str | None, AdapterDetection | None]:
    """决定这次用哪些接头裁剪，返回 ``(接头, 来源, 检测结论)``。"""
    given = [item for item in (adapter,) if item] if adapter else []
    if given:
        if len(adapters) > 0:
            raise ValueError("adapter 与 adapters 不能同时给：前者是单条指定，后者是候选表。")
        return given[0], "given", None
    if adapters:
        return tuple(adapters), "list", None
    if auto_detect:
        detection = detect_adapter_from_file(input_path, config=detect_config)
        if detection.detected and detection.adapter:
            return detection.adapter, "detected", detection
        return None, None, detection
    raise ValueError(
        "必须指定接头来源：手动指定一条接头序列（adapter）、"
        "给一组候选接头（adapters），或开启自动检测（auto_detect=True）。"
    )


def trim_adapter_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    adapter: str | None = None,
    adapters: Sequence[str] = (),
    auto_detect: bool = False,
    config: AdapterTrimConfig | None = None,
    detect_config: AdapterDetectionConfig | None = None,
    compress: bool | None = None,
) -> AdapterTrimSummary:
    """对整个 FASTQ 文件做接头裁剪并写出结果。

    参数：
        input_path: 输入 FASTQ 路径，gzip 按魔数自动识别。
        output_path: 输出 FASTQ 路径；父目录会自动创建。
        adapter: 手动指定的接头序列；给它就只用它。
        adapters: 候选接头表（依次尝试）。
        auto_detect: 是否从数据里自动检测接头（仅在没有指定 adapter/adapters 时生效）。
        config: 裁剪参数；``None`` 表示默认（与 fastp 一致）。
        detect_config: 自动检测的参数；``None`` 表示默认。
        compress: 输出是否 gzip 压缩；``None``（默认）表示**跟随输入**。

    返回：
        :class:`AdapterTrimSummary`（含被裁 read 数、去掉的碱基数、切下来的接头序列）。

    异常：
        ``ValueError``：文件不存在 / 格式不合法 / 三种接头来源都没给。
    """
    input_file = Path(input_path)
    effective_config = config or AdapterTrimConfig()
    chosen, source, detection = _resolve_adapter(
        input_file, adapter, adapters, auto_detect, detect_config
    )

    counters = {"trimmed_reads": 0, "trimmed_bases": 0}
    removed_counter: Counter[bytes] = Counter()

    def transform(record: FastqRecord) -> FastqRecord:
        if chosen is None:
            return record
        if isinstance(chosen, str):
            result = trim_adapter(record.sequence, record.quality, chosen, effective_config)
        else:
            result = trim_adapters(record.sequence, record.quality, chosen, effective_config)
        if not result.trimmed:
            return record
        counters["trimmed_reads"] += 1
        counters["trimmed_bases"] += len(result.removed)
        removed_counter[result.removed] += 1
        return FastqRecord(
            name=record.name, sequence=result.sequence, quality=result.quality
        )

    stream = process_fastq(input_file, output_path, transform, compress=compress)

    top_adapters = tuple(
        (sequence.decode("ascii", "replace"), count)
        for sequence, count in removed_counter.most_common(_TOP_ADAPTER_LIMIT)
    )
    return AdapterTrimSummary(
        total_reads=stream.total_reads,
        kept_reads=stream.kept_reads,
        dropped_reads=stream.dropped_reads,
        trimmed_reads=counters["trimmed_reads"],
        trimmed_bases=counters["trimmed_bases"],
        bases_before=stream.bases_before,
        bases_after=stream.bases_after,
        adapter=chosen if isinstance(chosen, str) else (",".join(chosen) if chosen else None),
        adapter_source=source,
        top_adapters=top_adapters,
        detection=detection,
    )
