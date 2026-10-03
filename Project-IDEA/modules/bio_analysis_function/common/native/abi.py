"""原生库的加载与 Python 封装（ctypes）。

这一层负责三件事：

1. **定位并加载共享库**（Windows 上是 ``bio_native.dll``，Linux 上是 ``libbio_native.so``）；
2. **声明与 C ABI 一一对应的结构体与函数签名**；
3. **把 C 状态码翻译成 Python 异常**，让上层代码不必处理状态码。

算法逻辑不在这里——在 C++ 侧。本文件只做"类型摆渡"。

关于并发：``ctypes.CDLL`` 调用时会释放 GIL，因此多个 Python 线程可以真正
并行地跑原生计算。
"""

from __future__ import annotations

import ctypes
import platform
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..fastq import FastqStreamSummary
from ..known_adapters import KNOWN_ADAPTERS
from ..paired_overlap import OverlapConfig, OverlapResult

_LIB_DIR = Path(__file__).resolve().parent / "lib"
_LIB_NAME = "bio_native.dll" if platform.system() == "Windows" else "libbio_native.so"

# --- 状态码（与 bio_native.h 的 bio_status_t 一致） -------------------------

BIO_OK = 0
BIO_ERR_INPUT_NOT_FOUND = 1
BIO_ERR_INPUT_FORMAT = 2
BIO_ERR_OUTPUT = 3
BIO_ERR_ARGUMENT = 4
BIO_ERR_INTERNAL = 5

# --- 压缩策略（与 bio_native.h 的 bio_compress_t 一致） ---------------------

_COMPRESS_FOLLOW_INPUT = -1
_COMPRESS_OFF = 0
_COMPRESS_ON = 1

# --- 过滤结果码（与 bio_native.h 的 bio_filter_verdict_t 一致） --------------
#
# 数值与上游 fastp 的 src/common.h 相同，也与 Python 侧
# submodules/read_filtering 的结果码相同，因此两侧的 failures 字典可直接比较。

BIO_FILTER_PASS = 0
BIO_FILTER_FAIL_N_BASE = 12
BIO_FILTER_FAIL_LENGTH = 16
BIO_FILTER_FAIL_TOO_LONG = 17
BIO_FILTER_FAIL_QUALITY = 20
BIO_FILTER_FAIL_COMPLEXITY = 24

# --- 接头检测的结论来源（与 bio_native.h 的 bio_adapter_source_t 一致） ------

BIO_ADAPTER_SOURCE_NONE = 0
BIO_ADAPTER_SOURCE_KNOWN = 1
BIO_ADAPTER_SOURCE_KMER = 2

_SOURCE_NAMES = {
    BIO_ADAPTER_SOURCE_KNOWN: "known",
    BIO_ADAPTER_SOURCE_KMER: "kmer",
}

_MESSAGE_CAPACITY = 512
_ADAPTER_SEQUENCE_CAPACITY = 128
_DETECT_REASON_CAPACITY = 256


class _Stats(ctypes.Structure):
    """对应 bio_stats_t。"""

    _fields_ = [
        ("total_reads", ctypes.c_int64),
        ("kept_reads", ctypes.c_int64),
        ("changed_reads", ctypes.c_int64),
        ("dropped_reads", ctypes.c_int64),
        ("bases_before", ctypes.c_int64),
        ("bases_after", ctypes.c_int64),
    ]


class _Result(ctypes.Structure):
    """对应 bio_result_t。字段顺序与对齐必须与 C 侧完全一致。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("stats", _Stats),
    ]


class _QualityTrimOptions(ctypes.Structure):
    """对应 bio_quality_trim_options_t。字段顺序必须与 C 侧完全一致。"""

    _fields_ = [
        ("enabled_front", ctypes.c_int32),
        ("enabled_right", ctypes.c_int32),
        ("enabled_tail", ctypes.c_int32),
        ("window_size_front", ctypes.c_int32),
        ("window_size_right", ctypes.c_int32),
        ("window_size_tail", ctypes.c_int32),
        ("quality_front", ctypes.c_int32),
        ("quality_right", ctypes.c_int32),
        ("quality_tail", ctypes.c_int32),
        ("trim_front", ctypes.c_int32),
        ("trim_tail", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _PolyTrimOptions(ctypes.Structure):
    """对应 bio_poly_trim_options_t。字段顺序必须与 C 侧完全一致。"""

    _fields_ = [
        ("enabled_poly_g", ctypes.c_int32),
        ("enabled_poly_x", ctypes.c_int32),
        ("min_length_poly_g", ctypes.c_int32),
        ("min_length_poly_x", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _ReadFilterOptions(ctypes.Structure):
    """对应 bio_read_filter_options_t。字段顺序必须与 C 侧完全一致。

    两个"比例"字段是**万分之一**（basis point）的整数，不是浮点：C ABI 里出现
    浮点字段时结构体布局依赖编译器对齐规则，整数没有这个隐患。
    对外仍按百分数 / 比例收参数，转换在本文件的包装函数里做。
    """

    _fields_ = [
        ("enabled_quality", ctypes.c_int32),
        ("qualified_quality_phred", ctypes.c_int32),
        ("unqualified_limit_bp", ctypes.c_int32),
        ("n_base_limit", ctypes.c_int32),
        ("average_qual", ctypes.c_int32),
        ("enabled_length", ctypes.c_int32),
        ("required_length", ctypes.c_int32),
        ("max_length", ctypes.c_int32),
        ("enabled_complexity", ctypes.c_int32),
        ("complexity_threshold_bp", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _FilterBreakdown(ctypes.Structure):
    """对应 bio_filter_breakdown_t：按原因分类的失败条数。"""

    _fields_ = [
        ("failed_quality", ctypes.c_int64),
        ("failed_n_base", ctypes.c_int64),
        ("failed_too_short", ctypes.c_int64),
        ("failed_too_long", ctypes.c_int64),
        ("failed_low_complexity", ctypes.c_int64),
    ]


class _FilterResult(ctypes.Structure):
    """对应 bio_filter_result_t：比 _Result 多一块分类明细。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("stats", _Stats),
        ("breakdown", _FilterBreakdown),
    ]


class _AdapterDetectOptions(ctypes.Structure):
    """对应 bio_adapter_detect_options_t。字段顺序必须与 C 侧完全一致。"""

    _fields_ = [
        ("max_reads", ctypes.c_int64),
        ("max_bases", ctypes.c_int64),
        ("min_reads", ctypes.c_int32),
        ("key_length", ctypes.c_int32),
        ("shift_tail", ctypes.c_int32),
        ("max_adapter_length", ctypes.c_int32),
    ]


class _AdapterDetectResult(ctypes.Structure):
    """对应 bio_adapter_detect_result_t。

    混合了 int32、int64 与字符数组，顺序照 C 侧抄；ctypes 与 C 编译器用的是同一套
    平台 ABI（x64 下 int64 按 8 字节对齐），因此布局一致。
    """

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("adapter", ctypes.c_char * _ADAPTER_SEQUENCE_CAPACITY),
        ("seed_sequence", ctypes.c_char * _ADAPTER_SEQUENCE_CAPACITY),
        ("reason", ctypes.c_char * _DETECT_REASON_CAPACITY),
        ("source", ctypes.c_int32),
        ("sampled_reads", ctypes.c_int64),
        ("sampled_bases", ctypes.c_int64),
        ("seed_count", ctypes.c_int64),
        ("seed_fold_milli", ctypes.c_int64),
    ]


class _AdapterTrimOptions(ctypes.Structure):
    """对应 bio_adapter_trim_options_t。字段顺序必须与 C 侧完全一致。"""

    _fields_ = [
        ("match_required", ctypes.c_int32),
        ("allow_one_gap", ctypes.c_int32),
        ("compress", ctypes.c_int32),
        ("threads", ctypes.c_int32),
    ]


class _OverlapOptions(ctypes.Structure):
    """对应 bio_overlap_options_t。

    ``diff_percent_bp`` 是**万分之一**整数（2000 = 0.2），见
    :func:`analyze_overlap` 的说明。
    """

    _fields_ = [
        ("diff_limit", ctypes.c_int32),
        ("require", ctypes.c_int32),
        ("diff_percent_bp", ctypes.c_int32),
        ("allow_gap", ctypes.c_int32),
    ]


class _OverlapResult(ctypes.Structure):
    """对应 bio_overlap_result_t（不读不写文件，因此没有 stats 字段）。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("overlapped", ctypes.c_int32),
        ("offset", ctypes.c_int32),
        ("overlap_len", ctypes.c_int32),
        ("diff", ctypes.c_int32),
        ("has_gap", ctypes.c_int32),
    ]


class _PairedMergeOptions(ctypes.Structure):
    _fields_ = [
        ("diff_limit", ctypes.c_int32),
        ("require", ctypes.c_int32),
        ("diff_percent_bp", ctypes.c_int32),
        ("allow_gap", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _PairedMergeResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_pairs", ctypes.c_int64),
        ("merged_pairs", ctypes.c_int64),
        ("unmerged_pairs", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
        ("gap_overlaps", ctypes.c_int64),
    ]


class _PairedAdapterTrimOptions(ctypes.Structure):
    _fields_ = [
        ("diff_limit", ctypes.c_int32),
        ("require", ctypes.c_int32),
        ("diff_percent_bp", ctypes.c_int32),
        ("allow_gap", ctypes.c_int32),
        ("front_trimmed1", ctypes.c_int32),
        ("front_trimmed2", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _PairedAdapterTrimResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_pairs", ctypes.c_int64),
        ("trimmed_pairs", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
        ("trimmed_bases", ctypes.c_int64),
    ]


class _PairedCorrectionOptions(ctypes.Structure):
    _fields_ = [
        ("diff_limit", ctypes.c_int32),
        ("require", ctypes.c_int32),
        ("diff_percent_bp", ctypes.c_int32),
        ("allow_gap", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _PairedCorrectionResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_pairs", ctypes.c_int64),
        ("corrected_pairs", ctypes.c_int64),
        ("corrected_reads", ctypes.c_int64),
        ("corrected_bases", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
    ]


class _UmiOptions(ctypes.Structure):
    _fields_ = [
        ("location", ctypes.c_int32),
        ("length", ctypes.c_int32),
        ("skip", ctypes.c_int32),
        ("prefix", ctypes.c_char_p),
        ("delimiter", ctypes.c_char_p),
        ("threads", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _UmiResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("reads_with_umi", ctypes.c_int64),
        ("trimmed_bases", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
    ]


class _DedupOptions(ctypes.Structure):
    """对应 bio_dedup_options_t。

    ``accuracy_level`` 传 0 表示"按模式取默认"（只评估 1、去重 3），
    由 C 侧解析并回填实际使用的档位——这样"默认值"只有一处定义。
    ``buffer_bytes`` 传 0 表示按档位取值；非 0 会覆盖每个缓冲区的字节数
    （对拍测试用它把 1 GiB 的位图压小）。
    """

    _fields_ = [
        ("accuracy_level", ctypes.c_int32),
        ("buffer_bytes", ctypes.c_int64),
        ("compress", ctypes.c_int32),
    ]


class _DedupResult(ctypes.Structure):
    """对应 bio_dedup_result_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("stats", _Stats),
        ("duplicate_reads", ctypes.c_int64),
        ("accuracy_level", ctypes.c_int32),
    ]


class _StatsReport(ctypes.Structure):
    """对应 bio_report_t：只带状态与消息的轻量返回结构。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
    ]


class _StatSummary(ctypes.Structure):
    """对应 bio_stat_summary_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("total_bases", ctypes.c_int64),
        ("q20_bases", ctypes.c_int64),
        ("q30_bases", ctypes.c_int64),
        ("q40_bases", ctypes.c_int64),
        ("gc_bases", ctypes.c_int64),
        ("mean_length", ctypes.c_int32),
        ("cycles", ctypes.c_int32),
        ("max_length", ctypes.c_int32),
    ]


#: 曲线种类（与 ``bio_stat_curve_kind_t`` 一致）。
_CURVE_KINDS: dict[str, int] = {
    "mean": 0,
    "A": 1,
    "T": 2,
    "C": 3,
    "G": 4,
    "N": 5,
}

#: 含量曲线的种类。键里多一个 ``GC``，它是 G 与 C 的和。
_CONTENT_KINDS: dict[str, int] = {
    "A": 6,
    "T": 7,
    "C": 8,
    "G": 9,
    "N": 10,
    "GC": 11,
}


class _InsertSizeOptions(ctypes.Structure):
    """对应 bio_insert_size_options_t。"""

    _fields_ = [
        ("max_size", ctypes.c_int32),
        ("diff_limit", ctypes.c_int32),
        ("require", ctypes.c_int32),
        ("diff_percent_bp", ctypes.c_int32),
        ("allow_gap", ctypes.c_int32),
    ]


class _InsertSizeResult(ctypes.Structure):
    """对应 bio_insert_size_result_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_pairs", ctypes.c_int64),
        ("overlapped_pairs", ctypes.c_int64),
        ("peak_size", ctypes.c_int32),
        ("max_size", ctypes.c_int32),
    ]


class _OverrepOptions(ctypes.Structure):
    """对应 bio_overrep_options_t。字段为 0 表示"用默认值"。"""

    _fields_ = [
        ("sampling", ctypes.c_int32),
        ("base_limit", ctypes.c_int64),
        ("seq_length_sample", ctypes.c_int32),
    ]


class _OverrepSummary(ctypes.Structure):
    """对应 bio_overrep_summary_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("total_bases", ctypes.c_int64),
        ("sampled_reads", ctypes.c_int64),
        ("seq_length", ctypes.c_int32),
        ("sampling", ctypes.c_int32),
        ("sequence_count", ctypes.c_int32),
        ("max_sequence_length", ctypes.c_int32),
    ]


class _NormalizeOptions(ctypes.Structure):
    """对应 bio_normalize_options_t。"""

    _fields_ = [
        ("input_phred", ctypes.c_int32),
        ("fix_mgi", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _NormalizeResult(ctypes.Structure):
    """对应 bio_normalize_result_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("renamed_reads", ctypes.c_int64),
        ("requantified_reads", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
    ]


class _IndexFilterOptions(ctypes.Structure):
    """对应 bio_index_filter_options_t。

    两份黑名单以「字符串数组 + 个数」传入；空时指针为 NULL、个数为 0。
    """

    _fields_ = [
        ("blacklist1", ctypes.POINTER(ctypes.c_char_p)),
        ("blacklist1_count", ctypes.c_int32),
        ("blacklist2", ctypes.POINTER(ctypes.c_char_p)),
        ("blacklist2_count", ctypes.c_int32),
        ("threshold", ctypes.c_int32),
        ("compress", ctypes.c_int32),
    ]


class _IndexFilterResult(ctypes.Structure):
    """对应 bio_index_filter_result_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("filtered_reads", ctypes.c_int64),
        ("input_bases", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
    ]


class _WorkflowOptions(ctypes.Structure):
    """对应 bio_workflow_options_t。字段顺序必须与 C 侧完全一致。"""

    _fields_ = [
        ("read1_path", ctypes.c_char_p),
        ("read2_path", ctypes.c_char_p),
        ("output1_path", ctypes.c_char_p),
        ("output2_path", ctypes.c_char_p),
        ("unpaired_path", ctypes.c_char_p),
        ("failed_out", ctypes.c_char_p),
        ("merged_out", ctypes.c_char_p),
        ("overlapped_out", ctypes.c_char_p),
        ("normalize_enabled", ctypes.c_int32),
        ("input_phred", ctypes.c_int32),
        ("fix_mgi", ctypes.c_int32),
        ("index_filter_enabled", ctypes.c_int32),
        ("index_blacklist1", ctypes.POINTER(ctypes.c_char_p)),
        ("index_blacklist1_count", ctypes.c_int32),
        ("index_blacklist2", ctypes.POINTER(ctypes.c_char_p)),
        ("index_blacklist2_count", ctypes.c_int32),
        ("index_filter_threshold", ctypes.c_int32),
        ("umi_enabled", ctypes.c_int32),
        ("umi_location", ctypes.c_int32),
        ("umi_length", ctypes.c_int32),
        ("umi_skip", ctypes.c_int32),
        ("umi_prefix", ctypes.c_char_p),
        ("umi_delimiter", ctypes.c_char_p),
        ("quality_trim", _QualityTrimOptions),
        ("max_length1", ctypes.c_int32),
        ("max_length2", ctypes.c_int32),
        ("poly_trim", _PolyTrimOptions),
        ("adapter_enabled", ctypes.c_int32),
        ("adapter_sequences", ctypes.POINTER(ctypes.c_char_p)),
        ("adapter_count", ctypes.c_int32),
        ("adapter_sequences_r2", ctypes.POINTER(ctypes.c_char_p)),
        ("adapter_count_r2", ctypes.c_int32),
        ("adapter_match_required", ctypes.c_int32),
        ("adapter_allow_one_gap", ctypes.c_int32),
        ("adapter_dimer_enabled", ctypes.c_int32),
        ("adapter_dimer_max_len", ctypes.c_int32),
        ("overlap", _OverlapOptions),
        ("correction_enabled", ctypes.c_int32),
        ("merge_enabled", ctypes.c_int32),
        ("merge_include_unmerged", ctypes.c_int32),
        ("filter", _ReadFilterOptions),
        ("dedup_evaluate", ctypes.c_int32),
        ("dedup_enabled", ctypes.c_int32),
        ("dedup_accuracy_level", ctypes.c_int32),
        ("dedup_buffer_bytes", ctypes.c_int64),
        ("stats_enabled", ctypes.c_int32),
        ("compress", ctypes.c_int32),
        ("split_records", ctypes.c_int64),
        ("split_digits", ctypes.c_int32),
        ("threads", ctypes.c_int32),
        ("max_reads", ctypes.c_int64),
    ]


class _WorkflowSummary(ctypes.Structure):
    """对应 bio_workflow_summary_t。"""

    _fields_ = [
        ("status", ctypes.c_int32),
        ("message", ctypes.c_char * _MESSAGE_CAPACITY),
        ("total_reads", ctypes.c_int64),
        ("total_bases", ctypes.c_int64),
        ("output_reads", ctypes.c_int64),
        ("output_bases", ctypes.c_int64),
        ("unpaired_reads", ctypes.c_int64),
        ("pairs_total", ctypes.c_int64),
        ("normalized_reads", ctypes.c_int64),
        ("index_filtered_reads", ctypes.c_int64),
        ("umi_tagged_reads", ctypes.c_int64),
        ("duplicate_reads", ctypes.c_int64),
        ("dedup_accuracy_level", ctypes.c_int32),
        ("trimmed_reads", ctypes.c_int64),
        ("poly_trimmed_reads", ctypes.c_int64),
        ("adapter_trimmed_reads", ctypes.c_int64),
        ("adapter_dimer_pairs", ctypes.c_int64),
        ("corrected_pairs", ctypes.c_int64),
        ("corrected_bases", ctypes.c_int64),
        ("filtered_reads", ctypes.c_int64),
        ("filter_breakdown", _FilterBreakdown),
        ("pairs_merged", ctypes.c_int64),
        ("gap_overlap_pairs", ctypes.c_int64),
        ("insert_size_peak", ctypes.c_int32),
        ("insert_size_unknown", ctypes.c_int64),
        ("split_file_count", ctypes.c_int32),
    ]


# 工作流结果里四个质量统计的槽位（与 bio_workflow_stats_slot_t 一致）。
WORKFLOW_STATS_PRE1 = 0
WORKFLOW_STATS_PRE2 = 1
WORKFLOW_STATS_POST1 = 2
WORKFLOW_STATS_POST2 = 3

#: 插入片段直方图的桶数（含最后一个溢出桶）。
WORKFLOW_INSERT_SIZE_BUCKETS = 513

_library: ctypes.CDLL | None = None


def library_path() -> Path:
    """原生库的预期路径。"""
    return _LIB_DIR / _LIB_NAME


def load_library() -> ctypes.CDLL:
    """加载原生库（只加载一次），并声明函数签名。

    异常：
        ``RuntimeError``：库文件不存在（尚未编译）或无法加载。
    """
    global _library
    if _library is not None:
        return _library

    path = library_path()
    if not path.exists():
        raise RuntimeError(
            f"未找到原生库：{path}\n"
            "请先编译：python modules/bio_analysis_function/common/native/tools/compile.py"
        )
    try:
        library = ctypes.CDLL(str(path))
    except OSError as error:
        raise RuntimeError(f"加载原生库失败：{path}（{error}）") from error

    library.bio_native_version.argtypes = []
    library.bio_native_version.restype = ctypes.c_char_p

    library.bio_quality_trim_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_QualityTrimOptions),
        ctypes.POINTER(_Result),
    ]
    library.bio_quality_trim_fastq.restype = ctypes.c_int32

    library.bio_poly_trim_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_PolyTrimOptions),
        ctypes.POINTER(_Result),
    ]
    library.bio_poly_trim_fastq.restype = ctypes.c_int32

    library.bio_read_filter_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_ReadFilterOptions),
        ctypes.POINTER(_FilterResult),
    ]
    library.bio_read_filter_fastq.restype = ctypes.c_int32

    library.bio_detect_adapter.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int32,
        ctypes.POINTER(_AdapterDetectOptions),
        ctypes.POINTER(_AdapterDetectResult),
    ]
    library.bio_detect_adapter.restype = ctypes.c_int32

    library.bio_trim_adapter_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int32,
        ctypes.POINTER(_AdapterTrimOptions),
        ctypes.POINTER(_Result),
    ]
    library.bio_trim_adapter_fastq.restype = ctypes.c_int32

    library.bio_analyze_overlap.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_OverlapOptions),
        ctypes.POINTER(_OverlapResult),
    ]
    library.bio_analyze_overlap.restype = ctypes.c_int32

    library.bio_merge_paired_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_PairedMergeOptions),
        ctypes.POINTER(_PairedMergeResult),
    ]
    library.bio_merge_paired_fastq.restype = ctypes.c_int32

    library.bio_trim_paired_adapter_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_PairedAdapterTrimOptions),
        ctypes.POINTER(_PairedAdapterTrimResult),
    ]
    library.bio_trim_paired_adapter_fastq.restype = ctypes.c_int32

    library.bio_correct_paired_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_PairedCorrectionOptions),
        ctypes.POINTER(_PairedCorrectionResult),
    ]
    library.bio_correct_paired_fastq.restype = ctypes.c_int32

    library.bio_process_umi_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_UmiOptions),
        ctypes.POINTER(_UmiResult),
    ]
    library.bio_process_umi_fastq.restype = ctypes.c_int32

    library.bio_deduplicate_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_DedupOptions),
        ctypes.POINTER(_DedupResult),
    ]
    library.bio_deduplicate_fastq.restype = ctypes.c_int32

    # 统计：不透明句柄 + 分步查询（理由见 bio_native.h 的说明）。
    library.bio_stats_create.argtypes = []
    library.bio_stats_create.restype = ctypes.c_void_p
    library.bio_stats_destroy.argtypes = [ctypes.c_void_p]
    library.bio_stats_destroy.restype = None
    library.bio_stats_scan_fastq.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(_StatsReport),
    ]
    library.bio_stats_scan_fastq.restype = ctypes.c_int32
    library.bio_stats_summary.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_StatSummary),
    ]
    library.bio_stats_summary.restype = ctypes.c_int32
    library.bio_stats_curve.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    library.bio_stats_curve.restype = ctypes.c_int32
    library.bio_stats_quality_histogram.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    library.bio_stats_quality_histogram.restype = ctypes.c_int32
    library.bio_stats_kmer.argtypes = list(
        library.bio_stats_quality_histogram.argtypes
    )
    library.bio_stats_kmer.restype = ctypes.c_int32
    library.bio_stats_lengths.argtypes = list(
        library.bio_stats_quality_histogram.argtypes
    )
    library.bio_stats_lengths.restype = ctypes.c_int32

    library.bio_insert_size_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_InsertSizeOptions),
        ctypes.POINTER(_InsertSizeResult),
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    library.bio_insert_size_fastq.restype = ctypes.c_int32

    library.bio_overrep_create.argtypes = [ctypes.POINTER(_OverrepOptions)]
    library.bio_overrep_create.restype = ctypes.c_void_p
    library.bio_overrep_destroy.argtypes = [ctypes.c_void_p]
    library.bio_overrep_destroy.restype = None
    library.bio_overrep_scan_fastq.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(_StatsReport),
    ]
    library.bio_overrep_scan_fastq.restype = ctypes.c_int32
    library.bio_overrep_summary.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_OverrepSummary),
    ]
    library.bio_overrep_summary.restype = ctypes.c_int32
    library.bio_overrep_sequence.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_char_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.POINTER(ctypes.c_int64),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    library.bio_overrep_sequence.restype = ctypes.c_int32

    library.bio_normalize_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_NormalizeOptions),
        ctypes.POINTER(_NormalizeResult),
    ]
    library.bio_normalize_fastq.restype = ctypes.c_int32

    library.bio_index_filter_fastq.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(_IndexFilterOptions),
        ctypes.POINTER(_IndexFilterResult),
    ]
    library.bio_index_filter_fastq.restype = ctypes.c_int32

    library.bio_detect_two_color_system.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(_StatsReport),
    ]
    library.bio_detect_two_color_system.restype = ctypes.c_int32

    # 工作流：一次调用跑完整条链，结果是不透明句柄（理由见 bio_native.h）。
    library.bio_run_workflow.argtypes = [
        ctypes.POINTER(_WorkflowOptions),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(_StatsReport),
    ]
    library.bio_run_workflow.restype = ctypes.c_int32
    library.bio_workflow_destroy.argtypes = [ctypes.c_void_p]
    library.bio_workflow_destroy.restype = None
    library.bio_workflow_stats.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    library.bio_workflow_stats.restype = ctypes.c_void_p
    library.bio_workflow_summary.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WorkflowSummary),
    ]
    library.bio_workflow_summary.restype = ctypes.c_int32
    library.bio_workflow_insert_size.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    library.bio_workflow_insert_size.restype = ctypes.c_int32

    _library = library
    return library


def native_version() -> str:
    """原生库自报的版本号，用于确认没有加载到过期的库。"""
    return load_library().bio_native_version().decode("ascii")


def _compress_mode(compress: bool | None) -> int:
    if compress is None:
        return _COMPRESS_FOLLOW_INPUT
    return _COMPRESS_ON if compress else _COMPRESS_OFF


def _decode_message(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _to_summary(stats: _Stats) -> FastqStreamSummary:
    """把 C 侧的统计结构体转成公共层的统计类型。"""
    return FastqStreamSummary(
        total_reads=stats.total_reads,
        kept_reads=stats.kept_reads,
        changed_reads=stats.changed_reads,
        dropped_reads=stats.dropped_reads,
        bases_before=stats.bases_before,
        bases_after=stats.bases_after,
    )


def quality_trim_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    enabled_front: bool = False,
    enabled_right: bool = False,
    enabled_tail: bool = False,
    window_size_front: int = 4,
    window_size_right: int = 4,
    window_size_tail: int = 4,
    quality_front: int = 20,
    quality_right: int = 20,
    quality_tail: int = 20,
    trim_front: int = 0,
    trim_tail: int = 0,
    threads: int = 0,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """调用原生实现，对整个 FASTQ 文件做滑窗质量剪切。

    参数含义与 ``submodules/quality_trimming`` 的 ``QualityCutConfig`` 完全一致，
    这里直接展开成关键字参数，避免多一层无意义的中间对象。

    关于 ``threads``：``0``（默认）表示由原生实现自动决定线程数，
    ``1`` 为纯单线程，``> 1`` 显式启用「读取 / 计算 / 写出」三段并行的流水线。
    **线程数不影响结果**——输出文件逐字节相同、统计完全相同，它只影响速度。
    当前默认走单线程：质量剪切单条 read 的计算量很小，实测多线程没有收益
    （瓶颈在磁盘吞吐与 gzip 编解码），详见 `common/native/native.md`。

    返回：
        :class:`~modules.bio_analysis_function.common.fastq.FastqStreamSummary`。

    异常：
        ``ValueError``：C 侧报告的任何失败（路径、格式、参数、输出），
        消息文本与 Python 实现保持一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    options = _QualityTrimOptions(
        enabled_front=1 if enabled_front else 0,
        enabled_right=1 if enabled_right else 0,
        enabled_tail=1 if enabled_tail else 0,
        window_size_front=window_size_front,
        window_size_right=window_size_right,
        window_size_tail=window_size_tail,
        quality_front=quality_front,
        quality_right=quality_right,
        quality_tail=quality_tail,
        trim_front=trim_front,
        trim_tail=trim_tail,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _Result()

    status = library.bio_quality_trim_fastq(
        str(input_path).encode("utf-8"),
        str(output_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return _to_summary(result.stats)


def poly_trim_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    enabled_poly_g: bool = False,
    enabled_poly_x: bool = False,
    min_length_poly_g: int = 10,
    min_length_poly_x: int = 10,
    threads: int = 0,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """调用原生实现，对整个 FASTQ 文件做 polyG / polyX 尾部修剪。

    参数含义与 ``submodules/poly_trimming`` 的 ``PolyTrimConfig`` 完全一致，
    这里展开成关键字参数。

    返回：
        :class:`~modules.bio_analysis_function.common.fastq.FastqStreamSummary`。

    说明：
        本算法只裁剪、不丢弃 read，因此 ``dropped_reads`` 恒为 0。

    异常：
        ``ValueError``：C 侧报告的任何失败（路径、格式、参数、输出），
        消息文本与 Python 实现保持一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    options = _PolyTrimOptions(
        enabled_poly_g=1 if enabled_poly_g else 0,
        enabled_poly_x=1 if enabled_poly_x else 0,
        min_length_poly_g=min_length_poly_g,
        min_length_poly_x=min_length_poly_x,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _Result()

    status = library.bio_poly_trim_fastq(
        str(input_path).encode("utf-8"),
        str(output_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return _to_summary(result.stats)


@dataclass(frozen=True, slots=True)
class NativeFilterSummary:
    """原生 reads 过滤的统计结果。

    字段与 ``submodules/read_filtering`` 的 ``FilterSummary`` 对齐，但**刻意不复用
    那个类型**：本层是模块公共层，不能反向依赖子模块。``failures`` 只含出现过的
    原因（键为结果码），与 Python 侧口径一致，因此两侧的字典可以直接比较。
    """

    total_reads: int
    kept_reads: int
    dropped_reads: int
    bases_before: int
    bases_after: int
    failures: dict[int, int]

    @property
    def bases_removed(self) -> int:
        """被丢弃 read 带走的碱基总数。"""
        return self.bases_before - self.bases_after

    @property
    def kept_rate(self) -> float:
        """通过比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.kept_reads / self.total_reads


def _to_filter_summary(result: _FilterResult) -> NativeFilterSummary:
    """把 C 侧的分类明细转成 Python 字典（只保留非零项）。"""
    breakdown = {
        BIO_FILTER_FAIL_QUALITY: result.breakdown.failed_quality,
        BIO_FILTER_FAIL_N_BASE: result.breakdown.failed_n_base,
        BIO_FILTER_FAIL_LENGTH: result.breakdown.failed_too_short,
        BIO_FILTER_FAIL_TOO_LONG: result.breakdown.failed_too_long,
        BIO_FILTER_FAIL_COMPLEXITY: result.breakdown.failed_low_complexity,
    }
    return NativeFilterSummary(
        total_reads=result.stats.total_reads,
        kept_reads=result.stats.kept_reads,
        dropped_reads=result.stats.dropped_reads,
        bases_before=result.stats.bases_before,
        bases_after=result.stats.bases_after,
        failures={code: count for code, count in breakdown.items() if count > 0},
    )


def read_filter_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    failed_output_path: str | Path | None = None,
    enabled_quality: bool = True,
    qualified_quality_phred: int = 15,
    unqualified_percent_limit: float = 40.0,
    n_base_limit: int = 5,
    average_qual: int = 0,
    enabled_length: bool = True,
    required_length: int = 15,
    max_length: int = 0,
    enabled_complexity: bool = False,
    complexity_threshold: float = 0.3,
    threads: int = 0,
    compress: bool | None = None,
) -> NativeFilterSummary:
    """调用原生实现，对整个 FASTQ 文件做 reads 过滤，只写出通过的 read。

    参数含义与 ``submodules/read_filtering`` 的 ``ReadFilterConfig`` 完全一致，
    这里展开成关键字参数（而不是收一个配置对象），同样是为了不反向依赖子模块。

    ``failed_output_path`` 非空时，被丢弃的 read 会**原样**另写一份到那个文件，
    并在名字后追加失败原因标签（如 ``failed_too_short``）；写出的条数等于返回
    统计里的 ``dropped_reads``。留空表示不要这份存档。

    两个比例参数的精度：``unqualified_percent_limit`` 按**百分数**收
    （40.0 = 40%），``complexity_threshold`` 按**比例**收（0.3 = 30%），
    传给原生层时换算成万分之一整数（4000 / 3000）。换算四舍五入到 0.01%
    （复杂度到 0.0001），实际会设的阈值都在这个精度之内、无损传递。

    返回：
        :class:`NativeFilterSummary`，含总数、通过数、按原因分类的失败明细与碱基数。

    关于 ``threads``：只影响速度、不影响结果。``0``（默认）表示由实现自行决定，
    本算法取**硬件并发数**（实测未压缩输入上 8 线程约为单线程的 2.0×）；
    ``1`` 显式单线程。注意这一点与 :func:`quality_trim_fastq` 不同——那两个算法
    计算量小、加线程反而慢，自动值取的是单线程。

    异常：
        ``ValueError``：C 侧报告的任何失败（路径、格式、参数、输出），
        消息文本与 Python 实现保持一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    options = _ReadFilterOptions(
        enabled_quality=1 if enabled_quality else 0,
        qualified_quality_phred=qualified_quality_phred,
        unqualified_limit_bp=round(unqualified_percent_limit * 100),
        n_base_limit=n_base_limit,
        average_qual=average_qual,
        enabled_length=1 if enabled_length else 0,
        required_length=required_length,
        max_length=max_length,
        enabled_complexity=1 if enabled_complexity else 0,
        complexity_threshold_bp=round(complexity_threshold * 10000),
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _FilterResult()
    failed = (
        None if failed_output_path is None else str(failed_output_path).encode("utf-8")
    )

    status = library.bio_read_filter_fastq(
        str(input_path).encode("utf-8"),
        str(output_path).encode("utf-8"),
        failed,
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return _to_filter_summary(result)


@dataclass(frozen=True, slots=True)
class NativeAdapterDetection:
    """原生接头检测的结论。

    字段与 ``common/adapter_detection`` 的 ``AdapterDetection`` 对齐，
    但刻意不复用那个类型（公共层不反向依赖子模块）。

    ``source`` 为 ``"known"``（命中已知接头表，拿到的是完整序列）或 ``"kmer"``
    （从数据里拼出来的，可能只是接头的一段）；``adapter`` 为 ``None`` 表示没检出。
    """

    adapter: str | None
    source: str | None
    sampled_reads: int
    sampled_bases: int
    reason: str
    seed_sequence: str | None
    seed_count: int
    seed_fold: float

    @property
    def detected(self) -> bool:
        """是否检测到接头。"""
        return self.adapter is not None


def adapter_detect_fastq(
    input_path: str | Path,
    *,
    adapters: Sequence[str] | None = None,
    max_reads: int = 256 * 1024,
    max_bases: int = 151 * 256 * 1024,
    min_reads: int = 10_000,
    key_length: int = 10,
    shift_tail: int = 1,
    max_adapter_length: int = 60,
) -> NativeAdapterDetection:
    """调用原生实现，从一份 FASTQ 里检测接头序列（不写任何文件）。

    参数含义与 ``common/adapter_detection`` 的 ``AdapterDetectionConfig`` 一致。

    ``adapters`` 的三种取值：
      - ``None``（默认）：用公共层的内置已知接头表（234 条，``common/known_adapters.py``）；
      - ``()``：跳过第一段，只做从头检测；
      - 自定义序列：用给定的候选表（内部会按字典序排，保证并列取舍与上游一致）。

    **已知接头表由本层传进原生库**，而不是在 C++ 里再存一份：表只有一份来源。

    返回：
        :class:`NativeAdapterDetection`；``adapter`` 为 ``None`` 表示没检测到，
        原因在 ``reason``。

    异常：
        ``ValueError``：C 侧报告的任何失败（路径、格式、参数）。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    table = KNOWN_ADAPTERS if adapters is None else tuple(adapters)
    encoded = [adapter.encode("ascii") for adapter in table]
    # ctypes 的定长数组：空表传 NULL，原生层据此跳过第一段。
    table_array = (ctypes.c_char_p * len(encoded))(*encoded) if encoded else None

    options = _AdapterDetectOptions(
        max_reads=max_reads,
        max_bases=max_bases,
        min_reads=min_reads,
        key_length=key_length,
        shift_tail=shift_tail,
        max_adapter_length=max_adapter_length,
    )
    result = _AdapterDetectResult()

    status = library.bio_detect_adapter(
        str(input_path).encode("utf-8"),
        table_array,
        len(encoded),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    adapter = _decode_message(result.adapter)
    return NativeAdapterDetection(
        adapter=adapter or None,
        source=_SOURCE_NAMES.get(result.source) if adapter else None,
        sampled_reads=result.sampled_reads,
        sampled_bases=result.sampled_bases,
        reason=_decode_message(result.reason),
        seed_sequence=_decode_message(result.seed_sequence) or None,
        seed_count=result.seed_count,
        seed_fold=result.seed_fold_milli / 1000.0,
    )


def trim_adapter_fastq(
    input_path: str | Path,
    output_path: str | Path,
    *,
    adapters: Sequence[str],
    match_required: int = 4,
    allow_one_gap: bool = True,
    threads: int = 0,
    compress: bool | None = None,
) -> FastqStreamSummary:
    """调用原生实现，按接头表把接头从整份 FASTQ 上剪掉。

    参数：
        adapters: 候选接头表（大写 ACGT）。**空序列表示不裁**（文件按原样写出）。
            一条 = 手动指定；多条 = 依次尝试（最短匹配长度自动按候选数抬高：
            多于 16 条取 5、多于 256 条取 6）。
        match_required: 单条接头时的最短匹配长度，默认 4。
        allow_one_gap: 是否允许 1 个插入/缺失，默认允许。
        threads: 0 = 自动（本算法暂取单线程，见 5.4.5）；1 = 单线程；> 1 = 指定。
        compress: 输出是否 gzip；``None``（默认）表示跟随输入。

    返回：
        :class:`~modules.bio_analysis_function.common.fastq.FastqStreamSummary`。
        本算法不丢 read，因此 ``dropped_reads`` 恒为 0，``changed_reads``
        就是被裁过的条数。

    说明：
        "自动检测"是上层的粘合：先调 :func:`adapter_detect_fastq` 拿到接头，
        再把它当成一条候选传进来。

    异常：
        ``ValueError``：文件不存在 / 格式不合法 / 参数越界。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    table = tuple(adapters)
    encoded = [adapter.encode("ascii") for adapter in table]
    table_array = (ctypes.c_char_p * len(encoded))(*encoded) if encoded else None

    options = _AdapterTrimOptions(
        match_required=match_required,
        allow_one_gap=1 if allow_one_gap else 0,
        compress=_compress_mode(compress),
        threads=threads,
    )
    result = _Result()

    status = library.bio_trim_adapter_fastq(
        str(input_path).encode("utf-8"),
        str(output_path).encode("utf-8"),
        table_array,
        len(encoded),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return _to_summary(result.stats)


@dataclass(frozen=True, slots=True)
class NativePairedMergeSummary:
    total_pairs: int
    merged_pairs: int
    unmerged_pairs: int
    input_bases: int
    output_bases: int
    gap_overlaps: int


def merge_paired_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output_path: str | Path,
    *,
    diff_limit: int = 5,
    require: int = 30,
    diff_percent_limit: float = 0.2,
    allow_gap: bool = False,
    threads: int = 0,
    compress: bool | None = None,
) -> NativePairedMergeSummary:
    library = load_library()
    options = _PairedMergeOptions(
        diff_limit=diff_limit,
        require=require,
        diff_percent_bp=round(diff_percent_limit * 10000),
        allow_gap=1 if allow_gap else 0,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _PairedMergeResult()
    status = library.bio_merge_paired_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8"),
        str(output_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))
    return NativePairedMergeSummary(
        total_pairs=result.total_pairs,
        merged_pairs=result.merged_pairs,
        unmerged_pairs=result.unmerged_pairs,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
        gap_overlaps=result.gap_overlaps,
    )


@dataclass(frozen=True, slots=True)
class NativePairedAdapterTrimSummary:
    total_pairs: int
    trimmed_pairs: int
    input_bases: int
    output_bases: int
    trimmed_bases: int


def trim_paired_adapter_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output1_path: str | Path,
    output2_path: str | Path,
    *,
    diff_limit: int = 5,
    require: int = 30,
    diff_percent_limit: float = 0.2,
    allow_gap: bool = False,
    front_trimmed1: int = 0,
    front_trimmed2: int = 0,
    threads: int = 0,
    compress: bool | None = None,
) -> NativePairedAdapterTrimSummary:
    """调用原生实现，按 overlap 裁掉双端接头（成对进、成对出）。

    参数含义与 ``submodules/paired_end_adapter_trimming`` 的 ``trim_paired_fastq``
    一致。返回的是原生侧统计；Python 侧的 ``PairedAdapterTrimSummary`` 还多一个
    "切下来的接头序列"汇总——那部分只用于报告，原生层不记录（与单端接头裁剪同口径），
    因此两侧对拍时比的是输出字节与这里的通用统计。

    关于调用粒度：本函数按"处理一整个文件"设计（内部走原生流水线），不是逐对调用。
    """
    library = load_library()
    options = _PairedAdapterTrimOptions(
        diff_limit=diff_limit,
        require=require,
        diff_percent_bp=round(diff_percent_limit * 10000),
        allow_gap=1 if allow_gap else 0,
        front_trimmed1=front_trimmed1,
        front_trimmed2=front_trimmed2,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _PairedAdapterTrimResult()
    status = library.bio_trim_paired_adapter_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8"),
        str(output1_path).encode("utf-8"),
        str(output2_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))
    return NativePairedAdapterTrimSummary(
        total_pairs=result.total_pairs,
        trimmed_pairs=result.trimmed_pairs,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
        trimmed_bases=result.trimmed_bases,
    )


@dataclass(frozen=True, slots=True)
class NativePairedCorrectionSummary:
    total_pairs: int
    corrected_pairs: int
    corrected_reads: int
    corrected_bases: int
    input_bases: int
    output_bases: int


def correct_paired_fastq(
    read1_path: str | Path,
    read2_path: str | Path,
    output1_path: str | Path,
    output2_path: str | Path,
    *,
    diff_limit: int = 5,
    require: int = 30,
    diff_percent_limit: float = 0.2,
    allow_gap: bool = False,
    threads: int = 0,
    compress: bool | None = None,
) -> NativePairedCorrectionSummary:
    """调用原生实现，校正双端重叠区的错配碱基（成对进、成对出）。

    参数含义与 ``submodules/paired_end_base_correction`` 的 ``correct_paired_fastq``
    一致。两个质量门槛（可信 ≥ Q30、不可信 ≤ Q14）是上游写死的常量，不在这里。

    关于调用粒度：本函数按"处理一整个文件"设计（内部走原生流水线），不是逐对调用。
    """
    library = load_library()
    options = _PairedCorrectionOptions(
        diff_limit=diff_limit,
        require=require,
        diff_percent_bp=round(diff_percent_limit * 10000),
        allow_gap=1 if allow_gap else 0,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _PairedCorrectionResult()
    status = library.bio_correct_paired_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8"),
        str(output1_path).encode("utf-8"),
        str(output2_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))
    return NativePairedCorrectionSummary(
        total_pairs=result.total_pairs,
        corrected_pairs=result.corrected_pairs,
        corrected_reads=result.corrected_reads,
        corrected_bases=result.corrected_bases,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
    )


#: UMI 来源 → 原生层的 location 取值（与 bio_native.h 的注释一致）。
_UMI_LOCATION_CODES: dict[str, int] = {
    "index1": 1,
    "index2": 2,
    "read1": 3,
    "read2": 4,
    "per_index": 5,
    "per_read": 6,
}


@dataclass(frozen=True, slots=True)
class NativeUmiSummary:
    total_reads: int
    reads_with_umi: int
    trimmed_bases: int
    input_bases: int
    output_bases: int


def process_umi_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    location: str = "read1",
    length: int = 0,
    skip: int = 0,
    prefix: str = "",
    delimiter: str = ":",
    threads: int = 0,
    compress: bool | None = None,
) -> NativeUmiSummary:
    """调用原生实现做 UMI 处理（单端或双端）。

    参数含义与 ``submodules/umi_processing`` 的 ``process_umi_fastq`` 一致；
    单端数据把 ``read2_path`` / ``output2_path`` 都留空。

    ``location`` 传字符串（``index1`` / ``index2`` / ``read1`` / ``read2`` /
    ``per_index`` / ``per_read``），这里换算成原生层的整数取值。
    """
    library = load_library()
    if location not in _UMI_LOCATION_CODES:
        raise ValueError(f"未知的 UMI 来源：{location!r}。")
    options = _UmiOptions(
        location=_UMI_LOCATION_CODES[location],
        length=length,
        skip=skip,
        # 空串要传 NULL（C 侧按"用默认值"处理）。
        prefix=prefix.encode("utf-8") if prefix else None,
        delimiter=delimiter.encode("utf-8") if delimiter else None,
        threads=threads,
        compress=_compress_mode(compress),
    )
    result = _UmiResult()
    status = library.bio_process_umi_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8") if read2_path is not None else None,
        str(output1_path).encode("utf-8"),
        str(output2_path).encode("utf-8") if output2_path is not None else None,
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))
    return NativeUmiSummary(
        total_reads=result.total_reads,
        reads_with_umi=result.reads_with_umi,
        trimmed_bases=result.trimmed_bases,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
    )


@dataclass(frozen=True, slots=True)
class NativeDeduplicationSummary:
    """原生重复检测的统计结果。

    字段与 ``submodules/deduplication`` 的 ``DuplicationSummary`` 对齐，但**刻意
    不复用**那个类型：本层是模块公共层，不能反向依赖子模块。

    ``total_reads`` 单端时是 read 条数、双端时是 read 对数（``paired`` 指出是哪一种）；
    ``dropped_reads`` 只在真去重时非零；``bases_after`` 是"保留下来的碱基数"，
    与是否真的写出文件无关——这三点与 Python 侧口径完全一致。
    """

    total_reads: int
    duplicate_reads: int
    kept_reads: int
    dropped_reads: int
    bases_before: int
    bases_after: int
    accuracy_level: int
    paired: bool

    @property
    def duplicate_rate(self) -> float:
        """重复率。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.duplicate_reads / self.total_reads

    @property
    def kept_rate(self) -> float:
        """保留比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.kept_reads / self.total_reads


def deduplicate_fastq(
    read1_path: str | Path,
    output1_path: str | Path | None = None,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    accuracy_level: int = 0,
    buffer_bytes: int = 0,
    compress: bool | None = None,
) -> NativeDeduplicationSummary:
    """调用原生实现做重复检测与去重（单端或双端）。

    参数含义与 ``submodules/deduplication`` 的 ``deduplicate_fastq`` 一致：

    - ``output1_path`` 留空 = **只评估**，不写任何文件（对应 fastp 默认行为）；
      给了 = 真去重（对应 ``--dedup``）。
    - 给了 ``read2_path`` 就是双端：每对的 R1 与 R2 **首尾相接**后一起判重，
      重复时成对丢弃。
    - ``accuracy_level`` 传 0（默认）表示按模式取默认档位（只评估 1、去重 3）。
    - ``buffer_bytes`` 传 0（默认）表示按档位取值；非 0 会覆盖每个缓冲区的字节数。
      **两边实现对拍时必须传同一个值**——位图大小决定位置向量怎么取模、
      进而决定假阳性率，改小了结论就变。C 侧也会回填实际使用的档位。

    异常：
        ``ValueError``：C 侧报告的任何失败（路径、格式、参数组合、输出），
        消息文本与 Python 实现保持一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    paired = read2_path is not None
    options = _DedupOptions(
        accuracy_level=accuracy_level,
        buffer_bytes=buffer_bytes,
        compress=_compress_mode(compress),
    )
    result = _DedupResult()
    status = library.bio_deduplicate_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8") if paired else None,
        str(output1_path).encode("utf-8") if output1_path is not None else None,
        str(output2_path).encode("utf-8") if output2_path is not None else None,
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return NativeDeduplicationSummary(
        total_reads=result.stats.total_reads,
        duplicate_reads=result.duplicate_reads,
        kept_reads=result.stats.kept_reads,
        dropped_reads=result.stats.dropped_reads,
        bases_before=result.stats.bases_before,
        bases_after=result.stats.bases_after,
        accuracy_level=result.accuracy_level,
        paired=paired,
    )


@dataclass(frozen=True, slots=True)
class NativeReadStats:
    """原生 reads 质量统计的结果。

    字段与 ``submodules/read_stats`` 的 ``ReadStatsSummary`` 对齐，但**刻意
    不复用**那个类型：本层是模块公共层，不能反向依赖子模块。

    曲线都是按 cycle（位置）的序列，长度 = ``cycles``；
    ``quality_histogram`` 只含非零项、键为 Phred 值；``length_counts`` 是
    "读长 → 条数"。
    """

    total_reads: int
    total_bases: int
    q20_bases: int
    q30_bases: int
    q40_bases: int
    gc_bases: int
    mean_length: int
    cycles: int
    max_length: int
    quality_curves: dict[str, tuple[float, ...]]
    content_curves: dict[str, tuple[float, ...]]
    quality_histogram: dict[int, int]
    kmer_counts: tuple[int, ...]
    length_counts: dict[int, int]

    @property
    def q20_rate(self) -> float:
        """Q20 及以上碱基的比例。"""
        return self.q20_bases / self.total_bases if self.total_bases else 0.0

    @property
    def q30_rate(self) -> float:
        """Q30 及以上碱基的比例。"""
        return self.q30_bases / self.total_bases if self.total_bases else 0.0

    @property
    def q40_rate(self) -> float:
        """Q40 及以上碱基的比例。"""
        return self.q40_bases / self.total_bases if self.total_bases else 0.0

    @property
    def gc_content(self) -> float:
        """GC 碱基比例。"""
        return self.gc_bases / self.total_bases if self.total_bases else 0.0


def read_stats_fastq(input_path: str | Path) -> NativeReadStats:
    """调用原生实现统计一份 FASTQ 的质量。

    参数与返回与 ``submodules/read_stats`` 的 ``stat_fastq`` 对应；
    这里的返回类型是 ``NativeReadStats``，字段口径完全一致，两侧可直接对照。

    实现上用的是**不透明累加器**：先创建，扫描，再把汇总与各条曲线逐项取回来，
    最后无论成败都销毁。之所以不像其他算法那样"一次调用填一个结构体"，
    是因为本算法的产物是一份**报告**——曲线的长度取决于数据的最长读长，
    运行期才知道，塞不进定长结构体（详见 ``bio_native.h`` 的说明）。

    异常：
        ``ValueError``：文件不存在、FASTQ 格式有问题。
        ``RuntimeError``：原生库未编译或无法加载，或统计器创建失败。
    """
    library = load_library()
    handle = library.bio_stats_create()
    if not handle:
        raise RuntimeError("原生层无法创建统计器（内存不足）。")
    try:
        report = _StatsReport()
        status = library.bio_stats_scan_fastq(
            handle, str(input_path).encode("utf-8"), ctypes.byref(report)
        )
        if status != BIO_OK:
            raise ValueError(_decode_message(report.message))
        return _collect_read_stats(library, handle)
    finally:
        library.bio_stats_destroy(handle)


def _collect_read_stats(library: ctypes.CDLL, handle: int) -> NativeReadStats:
    """把累加器里的标量、曲线与几张表逐项取回来。"""
    summary = _StatSummary()
    if library.bio_stats_summary(handle, ctypes.byref(summary)) != BIO_OK:
        raise ValueError("原生层无法汇总统计结果。")

    quality_curves = {
        name: _read_double_curve(library, handle, kind, summary.cycles)
        for name, kind in _CURVE_KINDS.items()
    }
    content_curves = {
        name: _read_double_curve(library, handle, kind, summary.cycles)
        for name, kind in _CONTENT_KINDS.items()
    }
    # 质量直方图固定 94 项（Phred 0~93）；5-mer 固定 1024 项；
    # 读长分布的长度由 max_length 决定。
    histogram = _read_int64_array(
        library.bio_stats_quality_histogram, handle, 94
    )
    kmers = _read_int64_array(library.bio_stats_kmer, handle, 1024)
    lengths = _read_int64_array(
        library.bio_stats_lengths, handle, summary.max_length + 1
    )

    return NativeReadStats(
        total_reads=summary.total_reads,
        total_bases=summary.total_bases,
        q20_bases=summary.q20_bases,
        q30_bases=summary.q30_bases,
        q40_bases=summary.q40_bases,
        gc_bases=summary.gc_bases,
        mean_length=summary.mean_length,
        cycles=summary.cycles,
        max_length=summary.max_length,
        quality_curves=quality_curves,
        content_curves=content_curves,
        quality_histogram={
            phred: count for phred, count in enumerate(histogram) if count
        },
        kmer_counts=tuple(kmers),
        length_counts={
            length: count for length, count in enumerate(lengths) if count
        },
    )


def _read_double_curve(
    library: ctypes.CDLL, handle: int, kind: int, length: int
) -> tuple[float, ...]:
    """取一条曲线。长度就是 ``cycles``，所以容量一定够，不必处理重试。"""
    buffer = (ctypes.c_double * max(length, 1))()
    written = ctypes.c_int32(0)
    status = library.bio_stats_curve(
        handle, kind, buffer, length, ctypes.byref(written)
    )
    if status != BIO_OK:
        raise ValueError("原生层无法取出统计曲线。")
    return tuple(buffer[: written.value])


def _read_int64_array(function, handle: int, length: int) -> tuple[int, ...]:
    """取一张定长的计数表（直方图 / k-mer / 读长分布）。"""
    buffer = (ctypes.c_int64 * max(length, 1))()
    written = ctypes.c_int32(0)
    status = function(handle, buffer, length, ctypes.byref(written))
    if status != BIO_OK:
        raise ValueError("原生层无法取出统计结果数组。")
    return tuple(buffer[: written.value])


@dataclass(frozen=True, slots=True)
class NativeInsertSizeSummary:
    """原生插入片段长度统计的结果。

    ``histogram`` 的长度是 ``max_size + 1``，最后一项是**溢出桶**
    （判不出 + 超上限）。恒等式：

        total_pairs = sum(histogram[:max_size]) + histogram[max_size]
    """

    total_pairs: int
    overlapped_pairs: int
    max_size: int
    histogram: tuple[int, ...]
    peak_size: int

    @property
    def unknown_pairs(self) -> int:
        """溢出桶里的条数（判不出片段长度，或超过上限）。"""
        return self.histogram[self.max_size] if self.histogram else 0

    @property
    def overlap_rate(self) -> float:
        """能判出片段长度的比例。"""
        if self.total_pairs == 0:
            return 0.0
        return self.overlapped_pairs / self.total_pairs


def analyze_insert_size(
    read1_path: str | Path,
    read2_path: str | Path,
    *,
    max_size: int = 512,
    diff_limit: int = 5,
    require: int = 30,
    diff_percent_limit: float = 0.2,
    allow_gap: bool = False,
) -> NativeInsertSizeSummary:
    """调用原生实现统计双端插入片段长度分布。

    参数与 ``submodules/insert_size_distribution`` 的 ``analyze_insert_size``
    对应，只有 ``max_size`` 这里要求**显式给正数**（默认 512）——直方图缓冲区
    由本函数按它分配，给 0 会让两侧对不上。

    异常：
        ``ValueError``：文件不存在、两份记录数不一致、参数越界。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    if max_size < 1:
        raise ValueError(f"max_size 必须为正，当前为 {max_size}。")
    library = load_library()
    options = _InsertSizeOptions(
        max_size=max_size,
        diff_limit=diff_limit,
        require=require,
        # 比例参数按万分之一整数传（与其它算法同一套约定）。
        diff_percent_bp=round(diff_percent_limit * 10000),
        allow_gap=1 if allow_gap else 0,
    )
    result = _InsertSizeResult()
    histogram = (ctypes.c_int64 * (max_size + 1))()
    length = ctypes.c_int32(0)

    status = library.bio_insert_size_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8"),
        ctypes.byref(options),
        ctypes.byref(result),
        histogram,
        max_size + 1,
        ctypes.byref(length),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return NativeInsertSizeSummary(
        total_pairs=result.total_pairs,
        overlapped_pairs=result.overlapped_pairs,
        max_size=result.max_size,
        histogram=tuple(histogram[: length.value]),
        peak_size=result.peak_size,
    )


@dataclass(frozen=True, slots=True)
class NativeOverrepresentedSequence:
    """一条原生层检出的过表达序列。"""

    sequence: str
    count: int
    estimated_count: int
    base_percent: float
    distribution: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NativeOverrepSummary:
    """原生过表达序列分析的结果。"""

    total_reads: int
    total_bases: int
    sampled_reads: int
    seq_length: int
    sampling: int
    sequences: tuple[NativeOverrepresentedSequence, ...]


def find_overrepresented_sequences(
    input_path: str | Path,
    *,
    sampling: int = 20,
    base_limit: int = 151 * 10000,
    seq_length_sample: int = 1000,
) -> NativeOverrepSummary:
    """调用原生实现做一次过表达序列分析。

    参数与 ``submodules/overrepresented_sequences`` 的
    ``find_overrepresented_sequences`` 对应。内部会**读两遍文件**
    （先找候选、再量化），这是上游的分工决定的，不是实现缺陷。

    异常：
        ``ValueError``：文件不存在、参数越界、FASTQ 格式有问题。
        ``RuntimeError``：原生库未编译或无法加载，或句柄创建失败。
    """
    library = load_library()
    options = _OverrepOptions(
        sampling=sampling,
        base_limit=base_limit,
        seq_length_sample=seq_length_sample,
    )
    handle = library.bio_overrep_create(ctypes.byref(options))
    if not handle:
        raise RuntimeError("原生层无法创建累加器（内存不足）。")
    try:
        report = _StatsReport()
        status = library.bio_overrep_scan_fastq(
            handle, str(input_path).encode("utf-8"), ctypes.byref(report)
        )
        if status != BIO_OK:
            raise ValueError(_decode_message(report.message))

        summary = _OverrepSummary()
        if library.bio_overrep_summary(handle, ctypes.byref(summary)) != BIO_OK:
            raise ValueError("原生层无法读取分析结果。")
        return NativeOverrepSummary(
            total_reads=summary.total_reads,
            total_bases=summary.total_bases,
            sampled_reads=summary.sampled_reads,
            seq_length=summary.seq_length,
            sampling=summary.sampling,
            sequences=tuple(
                _read_overrep_entry(library, handle, index, summary)
                for index in range(summary.sequence_count)
            ),
        )
    finally:
        library.bio_overrep_destroy(handle)


def _read_overrep_entry(
    library: ctypes.CDLL, handle: int, index: int, summary: _OverrepSummary
) -> NativeOverrepresentedSequence:
    """取第 ``index`` 条检出结果（序列 + 计数 + 位置分布）。"""
    sequence_capacity = summary.max_sequence_length + 1
    sequence_buffer = ctypes.create_string_buffer(sequence_capacity)
    count = ctypes.c_int64(0)
    estimated = ctypes.c_int64(0)
    percent = ctypes.c_double(0.0)
    distribution = (ctypes.c_int64 * max(summary.seq_length, 1))()
    length = ctypes.c_int32(0)

    status = library.bio_overrep_sequence(
        handle,
        index,
        sequence_buffer,
        sequence_capacity,
        ctypes.byref(count),
        ctypes.byref(estimated),
        ctypes.byref(percent),
        distribution,
        summary.seq_length,
        ctypes.byref(length),
    )
    if status != BIO_OK:
        raise ValueError("原生层无法读取过表达序列条目。")
    return NativeOverrepresentedSequence(
        sequence=sequence_buffer.value.decode("ascii", "replace"),
        count=count.value,
        estimated_count=estimated.value,
        base_percent=percent.value,
        distribution=tuple(distribution[: length.value]),
    )


@dataclass(frozen=True, slots=True)
class NativeNormalizeSummary:
    """原生 reads 规范化的统计结果。

    字段与 ``submodules/read_normalization`` 的 ``NormalizeSummary`` 对齐。
    ``output_bases`` 恒等于 ``input_bases``——规范化不改长度。
    """

    total_reads: int
    renamed_reads: int
    requantified_reads: int
    input_bases: int
    output_bases: int


def normalize_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    input_phred: int = 33,
    fix_mgi: bool = False,
    compress: bool | None = None,
) -> NativeNormalizeSummary:
    """调用原生实现做 reads 规范化（单端或双端）。

    参数与 ``submodules/read_normalization`` 的 ``normalize_fastq`` 一致：
    ``input_phred`` 只能是 33 或 64（默认 33，即不改质量）；
    双端时 ``read2_path`` 与 ``output2_path`` 必须同时给。

    异常：
        ``ValueError``：文件不存在、路径组合不合法、参数越界、
        两份配对记录数不一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()
    options = _NormalizeOptions(
        input_phred=input_phred,
        fix_mgi=1 if fix_mgi else 0,
        compress=_compress_mode(compress),
    )
    result = _NormalizeResult()
    status = library.bio_normalize_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8") if read2_path is not None else None,
        str(output1_path).encode("utf-8"),
        str(output2_path).encode("utf-8") if output2_path is not None else None,
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return NativeNormalizeSummary(
        total_reads=result.total_reads,
        renamed_reads=result.renamed_reads,
        requantified_reads=result.requantified_reads,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
    )


@dataclass(frozen=True, slots=True)
class NativeIndexFilterSummary:
    """原生按 index 过滤的统计结果。

    ``total_reads`` 单端时是 read 条数、双端时是 read 对数（``paired`` 指出是哪种），
    与去重同口径。
    """

    total_reads: int
    filtered_reads: int
    input_bases: int
    output_bases: int
    paired: bool

    @property
    def kept_reads(self) -> int:
        return self.total_reads - self.filtered_reads

    @property
    def filtered_rate(self) -> float:
        """被过滤的比例。输入为空时返回 0.0。"""
        if self.total_reads == 0:
            return 0.0
        return self.filtered_reads / self.total_reads


def _string_array(items: Sequence[str]):
    """把 Python 字符串序列变成 ctypes 的字符串数组；空时返回 ``(None, 0)``。

    返回值里的数组必须在调用期间被持有（否则会被回收），所以调用方要接住它。
    """
    if not items:
        return None, 0
    array = (ctypes.c_char_p * len(items))(
        *[item.encode("utf-8") for item in items]
    )
    return array, len(items)


def filter_by_index_fastq(
    read1_path: str | Path,
    output1_path: str | Path,
    *,
    read2_path: str | Path | None = None,
    output2_path: str | Path | None = None,
    blacklist1: Sequence[str] = (),
    blacklist2: Sequence[str] = (),
    threshold: int = 0,
    compress: bool | None = None,
) -> NativeIndexFilterSummary:
    """调用原生实现按 index 黑名单过滤（单端或双端）。

    参数与 ``submodules/index_filtering`` 的 ``filter_by_index_fastq`` 一致，
    黑名单这里直接收字符串序列（子模块收的是 ``IndexFilterConfig``）。

    提醒一个陷阱（上游行为）：匹配时**只比两者中较短的长度**，所以名字里取不到
    index 的 read 会被任何非空黑名单命中——那一批会被全丢。

    异常：
        ``ValueError``：文件不存在、路径组合不合法、两份配对记录数不一致。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()
    array1, count1 = _string_array(blacklist1)
    array2, count2 = _string_array(blacklist2)
    options = _IndexFilterOptions(
        blacklist1=array1,
        blacklist1_count=count1,
        blacklist2=array2,
        blacklist2_count=count2,
        threshold=threshold,
        compress=_compress_mode(compress),
    )
    result = _IndexFilterResult()
    status = library.bio_index_filter_fastq(
        str(read1_path).encode("utf-8"),
        str(read2_path).encode("utf-8") if read2_path is not None else None,
        str(output1_path).encode("utf-8"),
        str(output2_path).encode("utf-8") if output2_path is not None else None,
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return NativeIndexFilterSummary(
        total_reads=result.total_reads,
        filtered_reads=result.filtered_reads,
        input_bases=result.input_bases,
        output_bases=result.output_bases,
        paired=read2_path is not None,
    )


def analyze_overlap(
    read1: bytes,
    read2: bytes,
    config: OverlapConfig | None = None,
) -> OverlapResult:
    """调用原生实现，分析两条 read 是否来自同一片段（不读不写文件）。

    参数含义与 ``common/paired_overlap`` 的 ``analyze_overlap`` 完全一致，
    返回的也是**同一个** :class:`~...common.paired_overlap.OverlapResult` 类型，
    因此两侧的结果可以直接用 ``==`` 对拍。

    关于调用粒度：本函数按"一对 read 一次调用"设计，跨语言调用本身有固定开销
    （字符串拷贝 + ctypes 打包），满量数据不要用它逐对跑——双端算法会在原生层的
    流水线里直接调 C++ 的 ``bio::analyze_overlap``，见 ``include/bio_native.h``
    对 ``bio_analyze_overlap`` 的说明。

    关于比例精度：``diff_percent_limit`` 按**万分之一**传给原生层
    （0.2 → 2000，与 reads 过滤的比例口径一致）。四位小数以内的值无损，
    更细的位数会被舍入。

    返回：
        :class:`~...common.paired_overlap.OverlapResult`；``overlapped`` 为
        ``False`` 是**正常结论**（这对 read 不重叠），不是失败。

    异常：
        ``ValueError``：参数越界。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()

    settings = config or OverlapConfig()
    options = _OverlapOptions(
        diff_limit=settings.diff_limit,
        require=settings.require,
        diff_percent_bp=round(settings.diff_percent_limit * 10000),
        allow_gap=1 if settings.allow_gap else 0,
    )
    result = _OverlapResult()

    status = library.bio_analyze_overlap(
        bytes(read1),
        bytes(read2),
        ctypes.byref(options),
        ctypes.byref(result),
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(result.message))

    return OverlapResult(
        overlapped=bool(result.overlapped),
        offset=result.offset,
        overlap_len=result.overlap_len,
        diff=result.diff,
        has_gap=bool(result.has_gap),
        # 长度不在 C ABI 里传：调用方本来就知道，填上才能和 Python 版整体相等。
        read1_length=len(read1),
        read2_length=len(read2),
    )


# --- 二色测序系统判定 -------------------------------------------------------


def detect_two_color_system(read1_path: str | Path) -> bool:
    """判断这份数据是不是二色合成化学测序仪的产物。

    只看**第一条 read 的名字前缀**（仪器型号），不读碱基、不做统计。用途是
    决定要不要自动开 polyG 修剪——那种化学里 G 是"两个荧光通道都没信号"，
    信号变暗的一簇会被读成成片的假 G。

    按上游 fastp 的规矩，**用户显式指定了 polyG 开关时以用户的为准**，
    本函数只在两者都没指定时才参与决策（这条规则由工作流层执行）。

    异常：
        ``ValueError``：文件不存在或不是合法 FASTQ。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()
    flag = ctypes.c_int32(0)
    report = _StatsReport()
    status = library.bio_detect_two_color_system(
        str(read1_path).encode("utf-8"), ctypes.byref(flag), ctypes.byref(report)
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(report.message))
    return bool(flag.value)


# --- 工作流 -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CutSpec:
    """首尾固定修剪 + 滑窗质量剪切（对应 ``bio_quality_trim_options_t``）。"""

    front: bool = False
    right: bool = False
    tail: bool = False
    window_front: int = 4
    window_right: int = 4
    window_tail: int = 4
    quality_front: int = 20
    quality_right: int = 20
    quality_tail: int = 20
    trim_front: int = 0
    trim_tail: int = 0


@dataclass(frozen=True, slots=True)
class PolySpec:
    """polyG / polyX 尾部修剪（对应 ``bio_poly_trim_options_t``）。"""

    g: bool = False
    x: bool = False
    min_len_g: int = 10
    min_len_x: int = 10


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """reads 过滤（对应 ``bio_read_filter_options_t``）。

    ``unqualified_bp`` 与 ``complexity_threshold_bp`` 用**万分之一**表示
    （4000 = 40%），与 C ABI 及子模块口径一致。
    """

    quality: bool = True
    qualified_phred: int = 15
    unqualified_bp: int = 4000
    n_base_limit: int = 5
    average_qual: int = 0
    length: bool = True
    required_length: int = 15
    max_length: int = 0
    complexity: bool = False
    complexity_threshold_bp: int = 3000


@dataclass(frozen=True, slots=True)
class OverlapSpec:
    """双端 overlap 判定（对应 ``bio_overlap_options_t``）。"""

    diff_limit: int = 5
    require: int = 30
    diff_percent_bp: int = 2000
    allow_gap: bool = False


@dataclass(frozen=True, slots=True)
class UmiSpec:
    """UMI 处理（``location`` 取值见 ``bio_umi_options_t``：1~6）。"""

    location: int
    length: int = 0
    skip: int = 0
    prefix: str = ""
    delimiter: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    """``bio_workflow_options_t`` 的 Python 对应物，字段一一对应。

    默认值是"什么都不做、只把数据抄一遍"；有意义的配置至少要给出输入输出路径。
    真正的产品级默认值（该开哪些步骤、阈值多少）由上层 workflow 模块负责，
    这一层只做类型摆渡——与 abi.py 里其他函数的定位一致。
    """

    read1_path: str
    output1_path: str
    read2_path: str = ""
    output2_path: str = ""
    unpaired_path: str = ""
    failed_out: str = ""
    merged_out: str = ""
    overlapped_out: str = ""

    normalize_enabled: bool = False
    input_phred: int = 33
    fix_mgi: bool = False

    index_filter_enabled: bool = False
    index_blacklist1: Sequence[str] = ()
    index_blacklist2: Sequence[str] = ()
    index_filter_threshold: int = 0

    umi: UmiSpec | None = None

    cut: CutSpec = field(default_factory=CutSpec)
    max_length1: int = 0
    max_length2: int = 0
    poly: PolySpec = field(default_factory=PolySpec)

    adapter_enabled: bool = False
    adapters: Sequence[str] = ()
    #: R2 的候选接头表。留空表示不对 R2 做按序列的匹配（上游默认跟随 R1，
    #: 所以通常与 ``adapters`` 一样）。
    adapters_r2: Sequence[str] = ()
    adapter_match_required: int = 4
    adapter_allow_one_gap: bool = False
    adapter_dimer_enabled: bool = False
    adapter_dimer_max_len: int = 2

    overlap: OverlapSpec = field(default_factory=OverlapSpec)
    correction_enabled: bool = False
    merge_enabled: bool = False
    merge_include_unmerged: bool = False

    filter: FilterSpec = field(default_factory=FilterSpec)

    dedup_evaluate: bool = False
    dedup_enabled: bool = False
    dedup_accuracy_level: int = 0
    dedup_buffer_bytes: int = 0

    stats_enabled: bool = True
    compress: bool | None = None
    split_records: int = 0
    split_digits: int = 4
    threads: int = 0
    max_reads: int = 0


@dataclass(frozen=True, slots=True)
class NativeWorkflowSummary:
    """工作流的汇总标量（对应 ``bio_workflow_summary_t``）。"""

    total_reads: int
    total_bases: int
    output_reads: int
    output_bases: int
    unpaired_reads: int
    pairs_total: int
    normalized_reads: int
    index_filtered_reads: int
    umi_tagged_reads: int
    duplicate_reads: int
    dedup_accuracy_level: int
    trimmed_reads: int
    poly_trimmed_reads: int
    adapter_trimmed_reads: int
    adapter_dimer_pairs: int
    corrected_pairs: int
    corrected_bases: int
    filtered_reads: int
    failures: dict[int, int]
    pairs_merged: int
    gap_overlap_pairs: int
    insert_size_peak: int
    insert_size_unknown: int
    split_file_count: int

    @property
    def filtered_rate(self) -> float:
        """被丢弃的 read 占读入总量的比例。输入为空时返回 0.0。"""
        if not self.total_reads:
            return 0.0
        return self.filtered_reads / self.total_reads

    @property
    def duplicate_rate(self) -> float:
        """重复率。单端按条、双端按对算，因此分母单位与 ``duplicate_reads`` 一致。"""
        if not self.pairs_total:
            return 0.0
        return self.duplicate_reads / self.pairs_total


@dataclass(frozen=True, slots=True)
class NativeWorkflowResult:
    """工作流的结果：汇总 + 四个质量统计 + 插入片段直方图。"""

    summary: NativeWorkflowSummary
    pre_stats1: NativeReadStats
    pre_stats2: NativeReadStats
    post_stats1: NativeReadStats
    post_stats2: NativeReadStats
    insert_size_histogram: tuple[int, ...]


# 结果码 → 失败分类明细里的字段名（与 bio_filter_breakdown_t 一致）。
_BREAKDOWN_FIELDS = (
    ("failed_quality", 20),
    ("failed_n_base", 12),
    ("failed_too_short", 16),
    ("failed_too_long", 17),
    ("failed_low_complexity", 24),
)


def _to_workflow_summary(raw: _WorkflowSummary) -> NativeWorkflowSummary:
    failures = {
        verdict: getattr(raw.filter_breakdown, name)
        for name, verdict in _BREAKDOWN_FIELDS
        if getattr(raw.filter_breakdown, name)
    }
    return NativeWorkflowSummary(
        total_reads=raw.total_reads,
        total_bases=raw.total_bases,
        output_reads=raw.output_reads,
        output_bases=raw.output_bases,
        unpaired_reads=raw.unpaired_reads,
        pairs_total=raw.pairs_total,
        normalized_reads=raw.normalized_reads,
        index_filtered_reads=raw.index_filtered_reads,
        umi_tagged_reads=raw.umi_tagged_reads,
        duplicate_reads=raw.duplicate_reads,
        dedup_accuracy_level=raw.dedup_accuracy_level,
        trimmed_reads=raw.trimmed_reads,
        poly_trimmed_reads=raw.poly_trimmed_reads,
        adapter_trimmed_reads=raw.adapter_trimmed_reads,
        adapter_dimer_pairs=raw.adapter_dimer_pairs,
        corrected_pairs=raw.corrected_pairs,
        corrected_bases=raw.corrected_bases,
        filtered_reads=raw.filtered_reads,
        failures=failures,
        pairs_merged=raw.pairs_merged,
        gap_overlap_pairs=raw.gap_overlap_pairs,
        insert_size_peak=raw.insert_size_peak,
        insert_size_unknown=raw.insert_size_unknown,
        split_file_count=raw.split_file_count,
    )


def _encode(value: str | Path | None) -> bytes | None:
    """路径 → UTF-8 字节；空串与 ``None`` 都表示"没给这个路径"。"""
    if value is None:
        return None
    text = str(value)
    return text.encode("utf-8") if text else None


def _build_workflow_options(request: WorkflowRequest) -> _WorkflowOptions:
    """把 Python 请求摊平成 C 结构体。

    字符串数组（黑名单、接头表）由调用方持有：本函数把数组对象放进返回值之外
    的容器会立刻被回收，因此返回值里一并带出来，见 ``run_workflow``。
    """
    cut = request.cut
    poly = request.poly
    spec = request.filter
    overlap = request.overlap
    umi = request.umi

    return _WorkflowOptions(
        read1_path=_encode(request.read1_path),
        read2_path=_encode(request.read2_path),
        output1_path=_encode(request.output1_path),
        output2_path=_encode(request.output2_path),
        unpaired_path=_encode(request.unpaired_path),
        failed_out=_encode(request.failed_out),
        merged_out=_encode(request.merged_out),
        overlapped_out=_encode(request.overlapped_out),
        normalize_enabled=1 if request.normalize_enabled else 0,
        input_phred=request.input_phred,
        fix_mgi=1 if request.fix_mgi else 0,
        index_filter_enabled=1 if request.index_filter_enabled else 0,
        index_filter_threshold=request.index_filter_threshold,
        umi_enabled=1 if umi is not None else 0,
        umi_location=umi.location if umi else 0,
        umi_length=umi.length if umi else 0,
        umi_skip=umi.skip if umi else 0,
        umi_prefix=_encode(umi.prefix if umi else ""),
        umi_delimiter=_encode(umi.delimiter if umi else ""),
        quality_trim=_QualityTrimOptions(
            enabled_front=1 if cut.front else 0,
            enabled_right=1 if cut.right else 0,
            enabled_tail=1 if cut.tail else 0,
            window_size_front=cut.window_front,
            window_size_right=cut.window_right,
            window_size_tail=cut.window_tail,
            quality_front=cut.quality_front,
            quality_right=cut.quality_right,
            quality_tail=cut.quality_tail,
            trim_front=cut.trim_front,
            trim_tail=cut.trim_tail,
        ),
        max_length1=request.max_length1,
        max_length2=request.max_length2,
        poly_trim=_PolyTrimOptions(
            enabled_poly_g=1 if poly.g else 0,
            enabled_poly_x=1 if poly.x else 0,
            min_length_poly_g=poly.min_len_g,
            min_length_poly_x=poly.min_len_x,
        ),
        adapter_enabled=1 if request.adapter_enabled else 0,
        adapter_match_required=request.adapter_match_required,
        adapter_allow_one_gap=1 if request.adapter_allow_one_gap else 0,
        adapter_dimer_enabled=1 if request.adapter_dimer_enabled else 0,
        adapter_dimer_max_len=request.adapter_dimer_max_len,
        overlap=_OverlapOptions(
            diff_limit=overlap.diff_limit,
            require=overlap.require,
            diff_percent_bp=overlap.diff_percent_bp,
            allow_gap=1 if overlap.allow_gap else 0,
        ),
        correction_enabled=1 if request.correction_enabled else 0,
        merge_enabled=1 if request.merge_enabled else 0,
        merge_include_unmerged=1 if request.merge_include_unmerged else 0,
        filter=_ReadFilterOptions(
            enabled_quality=1 if spec.quality else 0,
            qualified_quality_phred=spec.qualified_phred,
            unqualified_limit_bp=spec.unqualified_bp,
            n_base_limit=spec.n_base_limit,
            average_qual=spec.average_qual,
            enabled_length=1 if spec.length else 0,
            required_length=spec.required_length,
            max_length=spec.max_length,
            enabled_complexity=1 if spec.complexity else 0,
            complexity_threshold_bp=spec.complexity_threshold_bp,
        ),
        dedup_evaluate=1 if request.dedup_evaluate else 0,
        dedup_enabled=1 if request.dedup_enabled else 0,
        dedup_accuracy_level=request.dedup_accuracy_level,
        dedup_buffer_bytes=request.dedup_buffer_bytes,
        stats_enabled=1 if request.stats_enabled else 0,
        compress=_compress_mode(request.compress),
        split_records=request.split_records,
        split_digits=request.split_digits,
        threads=request.threads,
        max_reads=request.max_reads,
    )


def run_workflow(request: WorkflowRequest) -> NativeWorkflowResult:
    """调用原生实现跑一次完整的 fastp 预处理流程。

    这是本模块**唯一一个会跑完整条处理链**的入口：一次读入、逐条过完所有步骤、
    分批写出，省掉"每个算法各读一遍写一遍"的十几倍 I/O。各步的判定与单独调用
    对应算法时是同一份代码，口径完全一致。

    阶段 A（接头检测、二色判定、总条数）不在这里——它们由调用方先跑、结论填进
    ``request``。理由见 ``include/bio_native.h`` 对 ``bio_run_workflow`` 的说明。

    返回值里的四个质量统计是**工作流自己累加的那一份**（单次扫描得到），
    不是事后重扫文件，因此与 summary 里的计数天然自洽。

    异常：
        ``ValueError``：参数自相矛盾、文件不存在、FASTQ 格式有问题、输出写不了。
        ``RuntimeError``：原生库未编译或无法加载。
    """
    library = load_library()
    options = _build_workflow_options(request)

    # 两个字符串数组必须在调用期间被持有，否则会被回收。
    blacklist1, count1 = _string_array(request.index_blacklist1)
    blacklist2, count2 = _string_array(request.index_blacklist2)
    adapters, adapter_count = _string_array(request.adapters)
    adapters_r2, adapter_count_r2 = _string_array(request.adapters_r2)
    options.index_blacklist1 = blacklist1
    options.index_blacklist1_count = count1
    options.index_blacklist2 = blacklist2
    options.index_blacklist2_count = count2
    options.adapter_sequences = adapters
    options.adapter_count = adapter_count
    options.adapter_sequences_r2 = adapters_r2
    options.adapter_count_r2 = adapter_count_r2

    handle = ctypes.c_void_p()
    report = _StatsReport()
    status = library.bio_run_workflow(
        ctypes.byref(options), ctypes.byref(handle), ctypes.byref(report)
    )
    if status != BIO_OK:
        raise ValueError(_decode_message(report.message))

    try:
        raw = _WorkflowSummary()
        if library.bio_workflow_summary(handle, ctypes.byref(raw)) != BIO_OK:
            raise RuntimeError("原生层无法取回工作流汇总。")

        histogram = _read_workflow_insert_size(library, handle)
        return NativeWorkflowResult(
            summary=_to_workflow_summary(raw),
            pre_stats1=_collect_read_stats(
                library, library.bio_workflow_stats(handle, WORKFLOW_STATS_PRE1)
            ),
            pre_stats2=_collect_read_stats(
                library, library.bio_workflow_stats(handle, WORKFLOW_STATS_PRE2)
            ),
            post_stats1=_collect_read_stats(
                library, library.bio_workflow_stats(handle, WORKFLOW_STATS_POST1)
            ),
            post_stats2=_collect_read_stats(
                library, library.bio_workflow_stats(handle, WORKFLOW_STATS_POST2)
            ),
            insert_size_histogram=histogram,
        )
    finally:
        library.bio_workflow_destroy(handle)


def _read_workflow_insert_size(
    library: ctypes.CDLL, handle: int
) -> tuple[int, ...]:
    """取插入片段直方图（定长，槽位数由 bio_native.h 的常量给定）。"""
    buffer = (ctypes.c_int64 * WORKFLOW_INSERT_SIZE_BUCKETS)()
    written = ctypes.c_int32(0)
    status = library.bio_workflow_insert_size(
        handle, buffer, WORKFLOW_INSERT_SIZE_BUCKETS, ctypes.byref(written)
    )
    if status != BIO_OK:
        raise RuntimeError("原生层无法取回插入片段直方图。")
    return tuple(buffer[: written.value])
