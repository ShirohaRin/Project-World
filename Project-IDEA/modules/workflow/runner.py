"""工作流的编排入口：阶段 A 预扫描 + 阶段 B 单遍处理链。

分两段是照抄上游 fastp 的结构（它在真正处理之前先跑一遍 Evaluator 做决策，
结论写回 Options，然后才进单遍主循环）。本实现把阶段 A 留在这一层，而不是
塞进原生入口，理由写在 ``runner.阶段 A`` 的注释里——简单说：那三件事各自
本来就是生物模块里独立可跑的算法，让它们各自跑、结论传下去，比再包一层更好。

对外只有 :func:`run_workflow` 一个入口。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..bio_analysis_function.common.native.abi import (
    NativeWorkflowResult,
    PolySpec,
    WorkflowRequest,
    adapter_detect_fastq,
    detect_two_color_system,
    read_stats_fastq,
    run_workflow as _run_native_workflow,
)
from .config import (
    AdapterDetectionReport,
    ScanReport,
    WorkflowConfig,
    WorkflowOutcome,
)
from .report import build_workflow_json, render_workflow_html

__all__ = ["run_workflow"]


def _split_name(path: Path, index: int, digits: int) -> Path:
    """分卷产物的文件名：序号前缀加原文件名（``0001.out.fq``）。

    **必须与 ``common/native/src/workflow.cpp`` 的 ``numbered_name`` 一致**——
    分卷文件是原生层写的，本函数只负责把它们的名字算出来好列进报告。
    测试里有一条用例专门盯着这个契约（造分卷、再看这里算出的名字是否都存在），
    所以两侧一旦漂移就会被发现。
    """
    number = str(index)
    if digits > 0:
        number = number.rjust(digits, "0")
    return path.parent / f"{number}.{path.name}"


def _resolve_unpaired(config: WorkflowConfig, splitting: bool) -> Path | None:
    """落单 read 的输出去向。

    三种取值对应三种意图：

    - 没给（``None``，默认）：写到主输出旁边的一个 ``*.unpaired.*`` 文件；
    - 空串：明确表示丢弃（上游不给 ``--unpaired1`` 时的行为）；
    - 给了路径：写到那里。

    **分卷模式下无处安放落单 read**：原生层只写主输出。默认值这时退化为丢弃
    （并在报告里如实写出），但用户显式给的路径会被判为参数矛盾——明确要过的
    东西不该无声消失。
    """
    if not config.paired:
        return None
    if splitting:
        if config.unpaired:
            raise ValueError(
                "分卷模式下只写主输出，无法同时保留落单 read；"
                "请把 unpaired 设为空串表示丢弃，或不要分卷。"
            )
        return None
    if config.unpaired == "":
        return None
    if config.unpaired is not None:
        return Path(config.unpaired)
    suffix = config.output1.suffix
    stem = config.output1.name[: -len(suffix)] if suffix else config.output1.name
    return config.output1.parent / f"{stem}.unpaired{suffix}"


def _scan_poly_g(config: WorkflowConfig) -> tuple[bool, bool]:
    """决定 polyG 是否开启，返回 ``(是否二色系统, 是否开启)``。

    上游的优先级：用户显式指定 > 二色系统自动判定。显式指定时不读文件，
    因此这个判定不会白白多扫一遍数据。
    """
    if config.trim_poly_g is not None:
        return False, config.trim_poly_g
    is_two_color = detect_two_color_system(config.read1)
    return is_two_color, is_two_color


def _adapter_lists(
    config: WorkflowConfig,
) -> tuple[tuple[str, ...], tuple[str, ...], AdapterDetectionReport]:
    """定下 R1 / R2 的候选接头表，以及这次用到的检测结论。

    上游的规则照抄如下：

    - 用户给了接头序列（``--adapter_sequence``）就用它，不检测；
    - 单端默认**自动检测**，双端默认**不检测**（``--detect_adapter_for_pe`` 要显式开）；
    - R2 没单独给接头时**跟随 R1**；
    - 多接头候选表（``--adapter_fasta``）两边都用。
    """
    report = AdapterDetectionReport()
    if not config.adapter_trimming:
        return (), (), report

    detected1 = config.adapter1
    detected2 = config.adapter2
    source1 = ""
    source2 = ""
    seed1 = ""
    seed2 = ""
    sampled = 0

    # 上游 shallDetectAdapter：单端默认真、双端默认假。
    want_detect = config.detect_adapter
    detect_r1 = want_detect if want_detect is not None else not config.paired
    detect_r2 = bool(want_detect) and config.paired

    if detect_r1 and not detected1:
        found = adapter_detect_fastq(config.read1)
        sampled = found.sampled_reads
        if found.detected:
            detected1 = found.adapter or ""
            source1 = found.source or ""
            seed1 = found.seed_sequence or ""
        else:
            report = AdapterDetectionReport(sampled_reads=sampled)

    if detect_r2 and not detected2 and config.read2 is not None:
        found = adapter_detect_fastq(config.read2)
        sampled = max(sampled, found.sampled_reads)
        if found.detected:
            detected2 = found.adapter or ""
            source2 = found.source or ""
            seed2 = found.seed_sequence or ""

    # R2 没单独给就跟随 R1（上游默认）。
    if config.paired and not detected2:
        detected2 = detected1
        source2 = source1
        seed2 = seed1

    list1 = ([detected1] if detected1 else []) + list(config.extra_adapters)
    list2 = ([detected2] if detected2 else []) + list(config.extra_adapters)

    report = AdapterDetectionReport(
        adapter1=detected1,
        adapter2=detected2 if config.paired else "",
        source1=source1,
        source2=source2 if config.paired else "",
        seed1=seed1,
        seed2=seed2 if config.paired else "",
        sampled_reads=sampled,
    )
    return tuple(list1), tuple(list2), report


def _resolve_split_records(config: WorkflowConfig) -> tuple[int, int]:
    """把"每卷条数"或"分卷份数"统一成每卷条数，返回 ``(每卷条数, 总条数)``。

    ``--split``（按文件数）需要知道总条数才能换算。上游是**估算**的（拿已读
    字节数与文件大小相除去推）；本实现直接精确数一遍——代价是多读一遍文件，
    但换来的分卷是均匀的，而且只在用户显式要求分卷时才付这个代价。
    """
    if config.split_records > 0:
        return config.split_records, 0
    if config.split_number <= 1:
        return 0, 0

    total = read_stats_fastq(config.read1).total_reads
    # 至少每卷一条：输入条数少于份数时上游也只给个警告，这里给出确定的分配。
    per_file = max(1, total // config.split_number)
    return per_file, total


def _build_request(
    config: WorkflowConfig,
    *,
    unpaired: Path | None,
    adapters1: tuple[str, ...],
    adapters2: tuple[str, ...],
    poly_g_enabled: bool,
    split_records: int,
) -> WorkflowRequest:
    """把产品配置摊平成原生层的请求。"""
    poly = config.poly
    return WorkflowRequest(
        read1_path=str(config.read1),
        output1_path=str(config.output1),
        read2_path="" if config.read2 is None else str(config.read2),
        output2_path="" if config.output2 is None else str(config.output2),
        unpaired_path="" if unpaired is None else str(unpaired),
        failed_out="" if config.failed_out is None else str(config.failed_out),
        merged_out="" if config.merged_out is None else str(config.merged_out),
        overlapped_out=(
            "" if config.overlapped_out is None else str(config.overlapped_out)
        ),
        normalize_enabled=config.normalize,
        input_phred=config.input_phred,
        fix_mgi=config.fix_mgi,
        index_filter_enabled=bool(config.index_blacklist1 or config.index_blacklist2),
        index_blacklist1=tuple(config.index_blacklist1),
        index_blacklist2=tuple(config.index_blacklist2),
        index_filter_threshold=config.index_threshold,
        umi=config.umi,
        cut=config.cut,
        max_length1=config.max_length1,
        max_length2=config.max_length2,
        poly=PolySpec(
            g=poly_g_enabled, x=poly.x, min_len_g=poly.min_len_g, min_len_x=poly.min_len_x
        ),
        adapter_enabled=config.adapter_trimming and bool(adapters1 or adapters2),
        adapters=adapters1,
        adapters_r2=adapters2 if config.paired else (),
        adapter_allow_one_gap=config.adapter_allow_one_gap,
        adapter_dimer_enabled=config.adapter_dimer and config.paired,
        adapter_dimer_max_len=config.adapter_dimer_max_len,
        overlap=config.overlap,
        correction_enabled=config.correction or config.merged,
        merge_enabled=config.merged,
        merge_include_unmerged=config.merge_include_unmerged,
        filter=config.filter,
        dedup_evaluate=config.dedup_evaluate or config.dedup,
        dedup_enabled=config.dedup,
        dedup_accuracy_level=config.dedup_accuracy_level,
        dedup_buffer_bytes=config.dedup_buffer_bytes,
        stats_enabled=True,
        compress=config.compress,
        split_records=split_records,
        split_digits=config.split_digits,
        threads=config.threads,
        max_reads=config.max_reads,
    )


def _collect_outputs(
    config: WorkflowConfig,
    unpaired: Path | None,
    split_records: int,
    split_file_count: int,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """算出这次实际写出了哪些文件，返回 ``(全部输出, 分卷产物)``。"""
    if split_records > 0:
        main = [config.output1]
        if config.paired and config.output2 is not None:
            main.append(config.output2)
        parts = [
            _split_name(path, index, config.split_digits)
            for path in main
            for index in range(1, split_file_count + 1)
        ]
        return tuple(path for path in parts if path.exists()), tuple(parts)

    outputs = [config.output1]
    if config.output2 is not None:
        outputs.append(config.output2)
    for optional in (
        unpaired,
        config.failed_out,
        config.merged_out,
        config.overlapped_out,
    ):
        if optional is not None:
            outputs.append(Path(optional))
    return tuple(path for path in outputs if path.exists()), ()


def run_workflow(
    config: WorkflowConfig,
    *,
    html_path: Path | None = None,
    log_dir: Path | None = None,
) -> WorkflowOutcome:
    """跑一次完整的预处理：阶段 A 预扫描 + 阶段 B 单遍处理链。

    参数：
        config: 配置；各步的语义与单独调用对应算法时一致。
        html_path: 呈现给用户的汇总报告写到这里。
        log_dir: 各步统计的那份 ``workflow.json`` 写到这里（供我们收集反馈与
            优化用，不呈现给用户）。两者都不给时只跑不写报告。

    返回：
        :class:`WorkflowOutcome`；统计、阶段 A 的结论与输出文件清单都在里面。

    异常：
        ``ValueError``：配置自相矛盾（如分卷同时要保留落单 read）、文件不存在、
            FASTQ 格式有问题、输出写不了。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    output1 = Path(config.output1)
    output1.parent.mkdir(parents=True, exist_ok=True)

    split_records, total_for_split = _resolve_split_records(config)
    splitting = split_records > 0
    unpaired = _resolve_unpaired(config, splitting)

    # --- 阶段 A：预扫描 ---
    # 三件事各自都是生物模块里独立可跑的算法（二色判定、接头检测、reads 统计），
    # 在这里按需要跑、结论汇总成 ScanReport。放在这一层而不是原生入口里，
    # 是为了让中间结论对调用方可见——报告要如实写出"用了什么参数、为什么"。
    is_two_color, poly_g_enabled = _scan_poly_g(config)
    adapters1, adapters2, adapter_report = _adapter_lists(config)
    scan = ScanReport(
        read_count=total_for_split,
        is_two_color=is_two_color,
        poly_g_enabled=poly_g_enabled,
        adapter=adapter_report,
    )

    # --- 阶段 B：单遍处理链 ---
    request = _build_request(
        config,
        unpaired=unpaired,
        adapters1=adapters1,
        adapters2=adapters2,
        poly_g_enabled=poly_g_enabled,
        split_records=split_records,
    )
    native: NativeWorkflowResult = _run_native_workflow(request)

    outputs, split_files = _collect_outputs(
        config, unpaired, split_records, native.summary.split_file_count
    )
    outcome = WorkflowOutcome(
        config=config,
        scan=scan,
        summary=native.summary,
        pre_stats1=native.pre_stats1,
        pre_stats2=native.pre_stats2,
        post_stats1=native.post_stats1,
        post_stats2=native.post_stats2,
        insert_size_histogram=native.insert_size_histogram,
        outputs=outputs,
        split_files=split_files,
    )

    log_path = None
    if log_dir is not None:
        log_path = Path(log_dir) / "workflow.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(build_workflow_json(outcome), ensure_ascii=False, indent="\t"),
            encoding="utf-8",
        )

    written_html = None
    if html_path is not None:
        written_html = Path(html_path)
        written_html.parent.mkdir(parents=True, exist_ok=True)
        written_html.write_text(render_workflow_html(outcome), encoding="utf-8")

    return replace(outcome, log_path=log_path, html_path=written_html)
