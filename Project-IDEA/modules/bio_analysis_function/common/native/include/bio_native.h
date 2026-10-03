/*
 * bio_native.h —— 生物算法模块的原生层（C ABI）契约
 *
 * ============================================================================
 * 这一层是什么
 * ============================================================================
 *
 * 本模块的算法核心用 C++ 实现（测序数据基数极大，逐碱基扫描需要原生速度），
 * 但**对外只暴露 C ABI**，不使用 pybind11 之类的绑定层。理由有三条：
 *
 * 1. **不绑定 Python ABI**。C 接口只用基本类型与结构体指针，因此编出来的
 *    共享库（Windows 上是 .dll，Linux 上是 .so）与 Python 版本、编译器
 *    厂商都无关——用 MinGW 编的库，Python 官方版（MSVC 编译）也能正常加载。
 * 2. **边界稳定，语言可替换**。C ABI 是通用约定；将来若要把核心换成
 *    Rust / Go / 更快的 C++，只要仍导出这组函数，Python 侧一行都不用改。
 * 3. **够用**。两侧要交换的只是「文件路径 + 几个参数 + 统计数字」，
 *    不需要 C++ 对象与 Python 对象之间的复杂转换。
 *
 * ============================================================================
 * 调用约定
 * ============================================================================
 *
 * - 所有函数线程安全性：**互不共享状态**，不同参数可并发调用。
 * - 字符串一律为 UTF-8 编码的以 NUL 结尾的 C 字符串。
 * - 失败时函数返回非 0 状态码，并把人类可读的原因写入 ``bio_result_t.message``；
 *   调用方**不要**在失败时读取 ``stats``。
 * - 参数里的 ``threads`` 控制工作线程数：``<= 0`` 表示由实现自动决定，
 *   ``1`` 是纯单线程，``> 1`` 显式启用「读取 / 计算 / 写出」三段并行的流水线。
 * - 线程数**不影响结果**：输出文件逐字节相同、统计完全相同，它只影响速度。
 * - **自动值按算法各自的实测来定**，不搞统一规则：质量剪切与 poly 修剪单条 read 的
 *   计算量小、瓶颈在磁盘与 gzip 编解码，实测多线程反而慢 19%，故自动取单线程；
 *   reads 过滤的计算占比明显更高，实测未压缩输入上 8 线程约为单线程的 2.0×，
 *   故自动取硬件并发数（同样数据走 gzip 时无收益，因为瓶颈转移到写出端的
 *   单线程 deflate）。
 * - 本头文件可同时被 C 与 C++ 包含。
 */

#ifndef BIO_NATIVE_H
#define BIO_NATIVE_H

#include <stdint.h>

#ifdef _WIN32
#define BIO_API __declspec(dllexport)
#else
#define BIO_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * 状态码
 *
 * 用明确的枚举而不是 errno：调用方（Python 侧）需要据此区分
 * "用户传错了参数" 与 "数据有问题" 与 "程序内部出错"，三者的处置方式不同。
 * ------------------------------------------------------------------------ */
typedef enum {
    BIO_OK = 0,
    BIO_ERR_INPUT_NOT_FOUND = 1,  /* 输入文件不存在或无法打开 */
    BIO_ERR_INPUT_FORMAT = 2,     /* 输入不是合法的 FASTQ（记录不完整、长度不匹配等） */
    BIO_ERR_OUTPUT = 3,           /* 输出文件无法写入 */
    BIO_ERR_ARGUMENT = 4,         /* 参数越界或自相矛盾 */
    BIO_ERR_INTERNAL = 5          /* 程序内部错误（不应发生，出现即为缺陷） */
} bio_status_t;

/* 错误消息缓冲区长度，含结尾的 NUL。 */
#define BIO_MESSAGE_CAPACITY 512

/* ---------------------------------------------------------------------------
 * 统计结果
 *
 * 各算法的计数口径统一，与模块公共层（Python 的 FastqStreamSummary）一致：
 *
 *     total_reads = kept_reads + dropped_reads
 *     kept_reads >= changed_reads
 *
 * changed_reads 只统计**序列真的被改动**的 read。
 * ------------------------------------------------------------------------ */
typedef struct {
    int64_t total_reads;
    int64_t kept_reads;
    int64_t changed_reads;
    int64_t dropped_reads;
    int64_t bases_before;
    int64_t bases_after;
} bio_stats_t;

/* ---------------------------------------------------------------------------
 * 调用结果
 *
 * 固定大小的结构体，由调用方分配、被调方填充，不涉及任何内存所有权转移，
 * 因此不存在跨语言的释放问题。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t status;                     /* bio_status_t 的值 */
    char message[BIO_MESSAGE_CAPACITY]; /* status != BIO_OK 时的人类可读说明 */
    bio_stats_t stats;
} bio_result_t;

/* ---------------------------------------------------------------------------
 * 输出压缩策略
 * ------------------------------------------------------------------------ */
typedef enum {
    BIO_COMPRESS_FOLLOW_INPUT = -1, /* 跟随输入：输入是 gzip 就压缩输出（默认） */
    BIO_COMPRESS_OFF = 0,
    BIO_COMPRESS_ON = 1
} bio_compress_t;

/* ---------------------------------------------------------------------------
 * 滑窗质量剪切参数
 *
 * 字段含义与语义见 Python 侧 submodules/quality_trimming/quality_trimming.md。
 * 三个模式的窗口与阈值各自独立；上游 fastp 允许它们共享一组值，
 * 那是命令行层的参数继承逻辑，本层要求调用方把最终值填好。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t enabled_front;      /* 是否启用 5' 端质量剪切 */
    int32_t enabled_right;      /* 是否启用激进剪切（与 enabled_tail 同时为真时只执行本项） */
    int32_t enabled_tail;       /* 是否启用 3' 端质量剪切 */
    int32_t window_size_front;
    int32_t window_size_right;
    int32_t window_size_tail;
    int32_t quality_front;      /* Phred 阈值，取值 0~93 */
    int32_t quality_right;
    int32_t quality_tail;
    int32_t trim_front;         /* 固定位置修剪：从头切掉几个碱基 */
    int32_t trim_tail;          /* 固定位置修剪：从尾切掉几个碱基 */
    int32_t threads;            /* 工作线程数；<= 0 表示由实现自行决定 */
    int32_t compress;           /* bio_compress_t */
} bio_quality_trim_options_t;

/* ---------------------------------------------------------------------------
 * 滑窗质量剪切：对整个 FASTQ 文件做修剪并写出结果
 *
 * 参数：
 *   input_path   输入 FASTQ 路径（UTF-8）。
 *   output_path  输出 FASTQ 路径；父目录会被创建。
 *   options      参数；传 NULL 表示使用全部默认值（三个模式均关闭、只做零固定修剪）。
 *   result       由调用方分配的结果结构体，函数负责填充。
 *
 * 返回：
 *   bio_status_t 的值，同时写入 result->status。返回 BIO_OK 时 result->stats 有效。
 *
 * 说明：
 *   若中途失败，函数会**删除已写出的部分输出文件**，避免留下看似完整实则残缺的结果。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_quality_trim_fastq(
    const char* input_path,
    const char* output_path,
    const bio_quality_trim_options_t* options,
    bio_result_t* result);

/* ---------------------------------------------------------------------------
 * polyG / polyX 尾部修剪参数
 *
 * polyG 针对 Illumina 双色合成化学的系统性假象：那种化学里 G 被编码为
 * "红绿两个荧光通道都没有信号"，信号变暗的一簇会被误读成一串 G；
 * polyX 处理真实存在的同种碱基尾巴（如 mRNA 的 polyA），
 * 由算法自行判定尾巴是 A/T/C/G 中的哪一种。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t enabled_poly_g;     /* 是否启用 polyG 修剪 */
    int32_t enabled_poly_x;     /* 是否启用 polyX 修剪 */
    int32_t min_length_poly_g;  /* polyG 的最短尾巴长度，默认 10 */
    int32_t min_length_poly_x;  /* polyX 的最短尾巴长度，默认 10 */
    int32_t threads;            /* 工作线程数；<= 0 表示由实现自行决定 */
    int32_t compress;           /* bio_compress_t */
} bio_poly_trim_options_t;

/* ---------------------------------------------------------------------------
 * 尾部 polyG / polyX 修剪：对整个 FASTQ 文件做修剪并写出结果
 *
 * 参数与返回约定同 bio_quality_trim_fastq。
 * 本算法只裁剪、不丢弃 read，因此 stats.dropped_reads 恒为 0。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_poly_trim_fastq(
    const char* input_path,
    const char* output_path,
    const bio_poly_trim_options_t* options,
    bio_result_t* result);

/* ---------------------------------------------------------------------------
 * 过滤结果码
 *
 * 数值取自上游 fastp 的 ``src/common.h``——那里刻意留出间隔（4、8、12……）
 * 以便日后插入新类别，并注明"数字越大表示越差"。照抄数值是为了让统计口径
 * 与报告字段能和 fastp 逐项对上，不是为了好玩。
 * ------------------------------------------------------------------------ */
typedef enum {
    BIO_FILTER_PASS = 0,
    BIO_FILTER_FAIL_N_BASE = 12,     /* N 碱基过多 */
    BIO_FILTER_FAIL_LENGTH = 16,     /* 过短（含长度为 0 的空 read） */
    BIO_FILTER_FAIL_TOO_LONG = 17,   /* 过长 */
    BIO_FILTER_FAIL_QUALITY = 20,    /* 低质量碱基比例过高，或平均质量不足 */
    BIO_FILTER_FAIL_COMPLEXITY = 24  /* 低复杂度 */
} bio_filter_verdict_t;

/* ---------------------------------------------------------------------------
 * reads 过滤参数
 *
 * 两个"比例"类字段用**万分之一**（basis point，1 = 0.01%）表示，不用浮点：
 * 浮点字段的结构体布局依赖编译器对齐规则，整数没有这个隐患；4 位小数也足以
 * 无损表示实际会设的阈值（40% → 4000，30% → 3000）。
 *
 * 字段语义与 Python 侧 submodules/read_filtering 的 ReadFilterConfig 一一对应，
 * 详细说明见 submodules/read_filtering/read_filtering.md。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t enabled_quality;             /* 质量过滤总开关 */
    int32_t qualified_quality_phred;     /* 达标质量（Phred），取值 0~93 */
    int32_t unqualified_limit_bp;        /* 低质量碱基比例上限，万分之一，0~10000 */
    int32_t n_base_limit;                /* N 碱基个数上限 */
    int32_t average_qual;                /* 平均质量下限，0 表示不设要求 */

    int32_t enabled_length;              /* 长度过滤总开关 */
    int32_t required_length;             /* 最短长度 */
    int32_t max_length;                  /* 最长长度，0 表示不限 */

    int32_t enabled_complexity;          /* 低复杂度过滤开关（默认关闭） */
    int32_t complexity_threshold_bp;     /* 复杂度下限，万分之一，0~10000 */

    int32_t threads;                     /* 工作线程数；<= 0 表示由实现自行决定 */
    int32_t compress;                    /* bio_compress_t */
} bio_read_filter_options_t;

/* ---------------------------------------------------------------------------
 * 按原因分类的失败明细
 *
 * 只统计失败的 read，因此各项之和等于 ``stats.dropped_reads``。
 * 字段与 Python 侧的结果码一一对应（见上面的 bio_filter_verdict_t）。
 * ------------------------------------------------------------------------ */
typedef struct {
    int64_t failed_quality;
    int64_t failed_n_base;
    int64_t failed_too_short;
    int64_t failed_too_long;
    int64_t failed_low_complexity;
} bio_filter_breakdown_t;

/* ---------------------------------------------------------------------------
 * 过滤结果
 *
 * 与 bio_result_t 相比多一块分类明细，因此单独定义一个类型而不是去改
 * bio_result_t：后者的布局已经被质量剪切与 poly 修剪两个算法使用，
 * 为一个新算法改共享结构的布局，代价和风险都不值。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t status;                     /* bio_status_t 的值 */
    char message[BIO_MESSAGE_CAPACITY]; /* status != BIO_OK 时的人类可读说明 */
    bio_stats_t stats;
    bio_filter_breakdown_t breakdown;
} bio_filter_result_t;

/* ---------------------------------------------------------------------------
 * reads 过滤：按质量 / N 含量 / 长度 / 复杂度判定去留，只把通过的 read 写出
 *
 * 参数与返回约定同 bio_quality_trim_fastq；额外说明四点：
 *
 * - **不改写序列**：本算法只决定每条 read 的去留，写出的记录与输入逐字节一致，
 *   因此 ``stats.changed_reads`` 恒为 0，失败分类在 ``result->breakdown``。
 * - **空 read 永远失败**：长度为 0 的记录判为 ``BIO_FILTER_FAIL_LENGTH``，
 *   且不受任何过滤开关影响（上游如此）。
 * - **判定是短路的**：一条 read 同时命中多个判据时只报一个原因，
 *   多个原因的相对顺序见 Python 侧 5.4.3 章节。
 * - **``failed_out`` 可以留空**：非空时，被丢弃的 read 会**原样**另写一份到
 *   那个文件，并在名字后追加一个空格与失败原因标签（如 ``failed_too_short``，
 *   取值同上游 ``FAILED_TYPES``）。序列与质量一字不改——它只是"丢了什么"的
 *   存档，写出的条数等于 ``result->stats.dropped_reads``。传 ``NULL`` 或空串
 *   表示不要这份存档。压缩方式与主输出一致（同一份压缩开关）。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_read_filter_fastq(
    const char* input_path,
    const char* output_path,
    const char* failed_out,
    const bio_read_filter_options_t* options,
    bio_filter_result_t* result);

/* ---------------------------------------------------------------------------
 * 接头检测：参数、结果与入口
 *
 * 这一项与其他算法不同：它**不产出文件**，只给出一条结论（接头序列 + 依据），
 * 供下游的接头裁剪使用。
 *
 * 已知接头表**不由本层内置**，而是由调用方通过 `adapters` 传入：表只有一份来源
 * （Python 侧的数据文件），不必在两种语言里各存一份；调用方也能借此传自己的表。
 * ------------------------------------------------------------------------ */
typedef struct {
    int64_t max_reads;          /* 采样条数上限，默认 256*1024 */
    int64_t max_bases;          /* 采样碱基数上限，默认 151*256*1024 */
    int32_t min_reads;          /* 少于它不做检测，默认 10000 */
    int32_t key_length;         /* 种子 k-mer 长度，默认 10（4~12） */
    int32_t shift_tail;         /* 扫描/拼接时忽略末尾几个碱基，默认 1 */
    int32_t max_adapter_length; /* 返回序列的截断长度，默认 60（上限见下） */
} bio_adapter_detect_options_t;

/* 结论的来源。 */
typedef enum {
    BIO_ADAPTER_SOURCE_NONE = 0,  /* 没检测到 */
    BIO_ADAPTER_SOURCE_KNOWN = 1, /* 命中已知接头表（拿到的是完整序列） */
    BIO_ADAPTER_SOURCE_KMER = 2   /* 从数据里拼出来的（可能只是接头的一段） */
} bio_adapter_source_t;

/* 返回序列的缓冲区长度（含结尾 NUL）；因此可返回的接头最长 127。 */
#define BIO_ADAPTER_SEQUENCE_CAPACITY 128
/* 未检测到时的原因文本长度（含结尾 NUL）。 */
#define BIO_DETECT_REASON_CAPACITY 256

typedef struct {
    int32_t status;                     /* bio_status_t 的值 */
    char message[BIO_MESSAGE_CAPACITY]; /* status != BIO_OK 时的人类可读说明 */
    char adapter[BIO_ADAPTER_SEQUENCE_CAPACITY];       /* 检测到的接头；空串表示没检到 */
    char seed_sequence[BIO_ADAPTER_SEQUENCE_CAPACITY]; /* 从头检测时的种子 k-mer */
    char reason[BIO_DETECT_REASON_CAPACITY];           /* 没检测到时为什么 */
    int32_t source;                     /* bio_adapter_source_t */
    int64_t sampled_reads;              /* 实际采样的 read 数 */
    int64_t sampled_bases;              /* 实际采样的碱基数 */
    int64_t seed_count;                 /* 命中已知表时是命中条数；从头检测时是种子计数 */
    int64_t seed_fold_milli;            /* 富集倍数 × 1000（命中已知表时是命中率 × 1000） */
} bio_adapter_detect_result_t;

/* ---------------------------------------------------------------------------
 * 从一份 FASTQ 里检测接头序列
 *
 * 参数：
 *   input_path     输入 FASTQ 路径（UTF-8），gzip 按魔数自动识别。
 *   adapters       已知接头表（UTF-8 字符串数组），需按字典序；可为 NULL 表示跳过第一段。
 *   adapter_count  表的长度；与 adapters 同时为 0/NULL 表示跳过第一段。
 *   options        参数；传 NULL 表示全部默认值。
 *   result         由调用方分配的结果结构体。
 *
 * 返回：
 *   bio_status_t 的值；返回 BIO_OK 时 result 的其余字段有效。
 *
 * 说明：
 *   本函数**不写任何文件**，因此没有"失败时删除半成品"这回事；
 *   ``result->adapter`` 为空串表示没检测到，原因在 ``result->reason``。
 *   参数越界（如 key_length 超范围、max_adapter_length 超过 127）报 BIO_ERR_ARGUMENT。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_detect_adapter(
    const char* input_path,
    const char* const* adapters,
    int32_t adapter_count,
    const bio_adapter_detect_options_t* options,
    bio_adapter_detect_result_t* result);

/* ---------------------------------------------------------------------------
 * 接头裁剪：参数与入口
 *
 * 与其它"FASTQ 进、FASTQ 出"的算法一样，统计走通用的 bio_result_t：
 * 保留条数 = 总条数（本算法**不丢 read**，长度被裁到 0 的也原样写出），
 * 而"改动条数"就是被裁过的 read 数。
 *
 * 接头表同样由调用方传入（候选表模式）：表只有一份来源，也允许用户传自己的表。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t match_required; /* 单条接头时的最短匹配长度，必须不小于 1（默认 4） */
    int32_t allow_one_gap;  /* 0 = 只做等长比对；要"允许 1 个插入/缺失"须显式传 1 */
    int32_t compress;       /* bio_compress_mode_t；未给出（0）时按输入判断 */
    int32_t threads;        /* 0 = 自动；1 = 单线程；> 1 = 指定线程数 */
} bio_adapter_trim_options_t;

/* ---------------------------------------------------------------------------
 * 按接头表裁剪整个 FASTQ
 *
 * 参数：
 *   input_path / output_path  输入输出路径（UTF-8）；gzip 按输入魔数识别，
 *                             输出压缩跟随 compress。
 *   adapters                  候选接头表（UTF-8，大写 ACGT），可为 NULL。
 *   adapter_count             表长；0 或 NULL 表示不裁（文件被原样复制）。
 *   options                   参数；传 NULL 表示全部默认值。
 *   result                    由调用方分配的结果结构体（统计见上）。
 *
 * 返回：
 *   bio_status_t 的值；返回 BIO_OK 时 result->stats 有效。
 *   参数越界（如 match_required 为负）报 BIO_ERR_ARGUMENT。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_trim_adapter_fastq(
    const char* input_path,
    const char* output_path,
    const char* const* adapters,
    int32_t adapter_count,
    const bio_adapter_trim_options_t* options,
    bio_result_t* result);

/* ---------------------------------------------------------------------------
 * 双端 overlap 分析：参数、结果与入口
 *
 * 这一项与其它函数不同：它**既不读也不写文件**，一次只处理一对 read，
 * 产出"是否重叠 + 错位量 + 重叠长度 + 错配数"。它是公共层的**工具**，
 * 自身对用户没有意义，必须被进一步处理（按 overlap 裁接头、校正重叠区
 * 低质量碱基、或把两条 read 合并成一条），因此按 `开发规则.md` 3.2 属于
 * 第 2 类，没有独立的算法入口。
 *
 * 关于调用粒度：本入口按"一对 read 一次调用"设计，供跨语言对拍与小批量使用；
 * 满量数据的双端算法会在流水线内部**直接调 C++ 的 bio::analyze_overlap**，
 * 不会一对一次跨语言调用（那样光调用开销就盖过了计算）。
 *
 * 比例的表示与 reads 过滤一致：用**万分之一**整数（2000 = 0.2），不用浮点。
 * C 侧把换算写成 ``bp / 10000.0``，与 Python 侧的浮点字面量是同一个 double
 * （除法是正确舍入的），因此两侧算出的错配上限逐位相同。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t diff_limit;      /* 重叠区允许的最大错配数，默认 5 */
    int32_t require;         /* 认定为重叠所需的最短重叠长度，默认 30（必须不小于 1） */
    int32_t diff_percent_bp; /* 错配比例上限，万分之一，0~10000，默认 2000 = 0.2 */
    int32_t allow_gap;       /* 0 = 只做等长比对；要"允许 1 个插入/缺失"须显式传 1 */
} bio_overlap_options_t;

/*
 * 分析结论。
 *
 * ``offset`` 是 read2 的**反向互补**（rc2）相对 read1 的错位量，符号对应两种
 * 不同的片段几何：``> 0`` 片段**长于**读长（只在中间重叠、无接头）；
 * ``< 0`` 片段**短于**读长（两端都读进接头，此时 ``overlap_len`` 就是片段长度）。
 * 反向互补会翻转顺序，所以 read2 读到的接头落在 **rc2 的开头**。
 * ``overlapped`` 为 0 时其余字段无意义。
 */
typedef struct {
    int32_t status;                     /* bio_status_t 的值 */
    char message[BIO_MESSAGE_CAPACITY]; /* status != BIO_OK 时的人类可读说明 */
    int32_t overlapped;                 /* 1 = 检出重叠 */
    int32_t offset;                     /* 错位量，见上 */
    int32_t overlap_len;                /* 重叠长度（碱基数） */
    int32_t diff;                       /* 重叠区的错配数（比对超过 50 碱基时是整段的真实值） */
    int32_t has_gap;                    /* 1 = 结论来自"允许 1 个插入/缺失"那一轮 */
} bio_overlap_result_t;

/* ---------------------------------------------------------------------------
 * 分析两条 read 是否来自同一片段
 *
 * 参数：
 *   read1 / read2  两条 read 的碱基串（UTF-8，**原始方向**，内部自己取反向互补）。
 *   options        参数；传 NULL 表示全部默认值（与 fastp 命令行一致）。
 *   result         由调用方分配的结果结构体。
 *
 * 返回：
 *   bio_status_t 的值；返回 BIO_OK 时 result 的其余字段有效。
 *   注意返回 BIO_OK 且 ``overlapped == 0`` 是**正常结论**（这对 read 不重叠），
 *   不是失败。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_analyze_overlap(
    const char* read1,
    const char* read2,
    const bio_overlap_options_t* options,
    bio_overlap_result_t* result);

/* ---------------------------------------------------------------------------
 * 双端 FASTQ 合并
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t diff_limit;
    int32_t require;
    int32_t diff_percent_bp;
    int32_t allow_gap;
    int32_t threads;
    int32_t compress;
} bio_paired_merge_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_pairs;
    int64_t merged_pairs;
    int64_t unmerged_pairs;
    int64_t input_bases;
    int64_t output_bases;
    int64_t gap_overlaps;
} bio_paired_merge_result_t;

BIO_API int32_t bio_merge_paired_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output_path,
    const bio_paired_merge_options_t* options,
    bio_paired_merge_result_t* result);

/* ---------------------------------------------------------------------------
 * 双端按 overlap 裁接头
 *
 * 判据与双端合并**同一个来源**（两条 read 的重叠），但只处理"片段短于读长、
 * 两端都读穿接头"那一类（``offset < 0``），把两条 read 各裁到片段末端。
 * 成对读入、**成对写出两份 FASTQ**；没检出接头的 read 对原样写出。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t diff_limit;
    int32_t require;
    int32_t diff_percent_bp;
    int32_t allow_gap;
    int32_t front_trimmed1; /* R1 头部已被剪掉的碱基数，几何补偿用，默认 0 */
    int32_t front_trimmed2; /* R2 头部已被剪掉的碱基数，默认 0 */
    int32_t threads;
    int32_t compress;
} bio_paired_adapter_trim_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_pairs;
    int64_t trimmed_pairs;
    int64_t input_bases;
    int64_t output_bases;
    int64_t trimmed_bases;
} bio_paired_adapter_trim_result_t;

BIO_API int32_t bio_trim_paired_adapter_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_paired_adapter_trim_options_t* options,
    bio_paired_adapter_trim_result_t* result);

/* ---------------------------------------------------------------------------
 * 重叠区碱基校正
 *
 * 用两条 read 重叠区里**高质量一侧**的碱基，改正**低质量一侧**的错配碱基，
 * 并把高质量的质量值一并赋过去。成对读入、**成对写出两份 FASTQ**；
 * 不改长度、不丢 read。
 *
 * 两个质量门槛（可信 ≥ Q30、不可信 ≤ Q14）是上游写死的常量，不由参数传入。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t diff_limit;
    int32_t require;
    int32_t diff_percent_bp;
    int32_t allow_gap; /* 默认 0；打开后若重叠带缺口则跳过校正（与上游一致） */
    int32_t threads;
    int32_t compress;
} bio_paired_correction_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_pairs;
    int64_t corrected_pairs;
    int64_t corrected_reads;
    int64_t corrected_bases;
    int64_t input_bases;
    int64_t output_bases;
} bio_paired_correction_result_t;

BIO_API int32_t bio_correct_paired_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_paired_correction_options_t* options,
    bio_paired_correction_result_t* result);

/* ---------------------------------------------------------------------------
 * UMI 处理
 *
 * 把 UMI 从 read 序列或名字里的 index 取出来、挂到 read 名字上，序列里那一段会被剪掉。
 * **单端与双端共用这一个入口**：``read2_path`` / ``output2_path`` 传 NULL 即为单端，
 * 两者必须同时给出或同时留空。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t location;      /* UMI 来源，取值见下方 bio_umi_location_t */
    int32_t length;        /* UMI 长度（read1/read2/per_read 用） */
    int32_t skip;          /* 剪掉 UMI 之后额外跳过的碱基数 */
    const char* prefix;    /* UMI 前缀；NULL 表示没有（不转移所有权，只读） */
    const char* delimiter; /* 标签分隔符；NULL 或空串表示用默认的 ':' */
    int32_t threads;
    int32_t compress;
} bio_umi_options_t;

/*
 * UMI 来源的取值（与上游 UMI_LOC_* 一致）：
 *   1 = index1   R1 名字里的第一段 index
 *   2 = index2   R2 名字里的最后一段 index
 *   3 = read1    R1 序列开头的 length 个碱基
 *   4 = read2    R2 序列开头的 length 个碱基
 *   5 = per_index 两段 index 拼起来
 *   6 = per_read  两条 read 的序列开头拼起来
 */
typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_reads;     /* 按 read 条数计，双端时一对算两条 */
    int64_t reads_with_umi;
    int64_t trimmed_bases;
    int64_t input_bases;
    int64_t output_bases;
} bio_umi_result_t;

BIO_API int32_t bio_process_umi_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_umi_options_t* options,
    bio_umi_result_t* result);

/* ---------------------------------------------------------------------------
 * 重复序列检测与去重（fastp 的 Duplicate / --dedup）
 *
 * 用**布隆过滤器**判"这条序列之前见过没有"：把序列压成若干 64 位位置向量，
 * 把这些位置对应的比特置 1；一条序列所有位置的比特**都已经置起**就判为重复。
 * 代价是**假阳性**——两条不同的序列可能撞到同一组比特上。这是上游的取舍
 * （省内存换速度），本实现照搬。
 *
 * **判定是顺序相关的**：谁先出现谁"不重复"。本实现严格按文件顺序判定，
 * 因此结果与线程调度无关（本算法也**没有** threads 参数——判定无法并行，
 * 详见 common/native/native.md）。上游在 worker 线程里判重，同一份数据换
 * 线程数报出的重复率会变；本实现不会。
 * ------------------------------------------------------------------------ */

/* 内存档位 → 位图大小。数值照抄上游，它是**产品参数**：
 * 用户在界面上看到的就是"多花多少内存换多少准确度"。
 *
 *   1 → 1 GiB    2 → 2 GiB    3 → 4 GiB
 *   4 → 8 GiB    5 → 16 GiB   6 → 32 GiB
 */
typedef struct {
    int32_t accuracy_level; /* 1~6；<= 0 表示按模式取默认（只评估 1、去重 3） */
    int64_t buffer_bytes;   /* 覆盖每个缓冲区的字节数；0 表示按档位取值 */
    int32_t compress;       /* bio_compress_t，只影响输出文件 */
} bio_dedup_options_t;

/* ---------------------------------------------------------------------------
 * 去重结果
 *
 * ``stats.total_reads`` 单端时是 **read 条数**，双端时是 **read 对数**
 * （一对被一起判重、一起丢弃）；``duplicate_reads`` 与它同单位。
 *
 * 两处与"直觉"不同、但和 Python 侧口径一致的地方：
 *
 * - ``stats.dropped_reads`` **只在真正去重时非零**。只评估时它恒为 0——
 *   没丢任何东西，``stats.kept_reads`` 就等于总数。
 * - ``stats.bases_after`` 是"**保留下来的**碱基数"（输入碱基数减去被判为重复
 *   的那些 read 的碱基数），与是否真的写出文件无关。双端时含 R1 与 R2 两侧。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    bio_stats_t stats;
    int64_t duplicate_reads;  /* 判为重复的数量（单端按条、双端按对） */
    int32_t accuracy_level;   /* 回填实际使用的档位 */
} bio_dedup_result_t;

/*
 * 重复检测与去重。单端与双端共用这一个入口：
 *
 * - ``read2_path`` 为 NULL 或空串 → 单端，只读 ``read1_path``；
 * - ``output1_path`` 为 NULL 或空串 → **只评估**，不写任何文件
 *   （对应 fastp 的默认行为）；给了则是真去重（对应 ``--dedup``）。
 * - 双端时 ``output1_path`` 与 ``output2_path`` 必须同时给或同时不给。
 *
 * 双端把每对的 R1 与 R2 **首尾相接**后一起判重，重复时成对丢弃；
 * 因此一对 (R1, R2) 与另一对调换了 R1/R2 不算重复。两份输入的记录数
 * 必须一致，不一致返回 BIO_ERR_INPUT_FORMAT。
 *
 * 调用失败时，已写出的半成品会被删除（两个输出都删）。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_deduplicate_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_dedup_options_t* options,
    bio_dedup_result_t* result);

/* ---------------------------------------------------------------------------
 * reads 质量统计（fastp 的 Stats）
 *
 * 与前面那些算法的 ABI 形态**不一样**，理由要写清楚：
 *
 * - 它们的产物是"文件 + 几个统计数字"，所以做成"一次调用、填一个结果结构体"；
 * - 本算法的产物是一份**报告**：11 条与读长同长的曲线（每条几百到几千个点）、
 *   1024 个 5-mer 桶、94 项质量直方图、读长分布。这些**长度在运行期才知道**
 *   （取决于数据的最长读长），没法塞进定长结构体，也不该让调用方去猜容量。
 *
 * 因此改用**不透明累加器句柄**：先 create，扫描（可多次累加），
 * 再按需查询。句柄由创建方销毁，跨 ABI 不转移所有权、也不暴露内部布局。
 * ------------------------------------------------------------------------ */

/* 累加器句柄。内部布局不对外暴露，只能通过下面的函数使用。 */
typedef struct bio_stats_handle bio_stats_handle;

/* 只报告状态与消息的轻量返回结构（统计结果本身不是定长的，见上）。 */
typedef struct {
    int32_t status;                     /* bio_status_t 的值 */
    char message[BIO_MESSAGE_CAPACITY]; /* status != BIO_OK 时的人类可读说明 */
} bio_report_t;

BIO_API bio_stats_handle* bio_stats_create(void);
BIO_API void bio_stats_destroy(bio_stats_handle* stats);

/*
 * 流式读完一份 FASTQ 并把结果累加进 ``stats``。
 *
 * 可以对同一个句柄多次调用（把多份文件合起来统计）；也可以先 create 再
 * 反复调用，最后统一查询。路径为 UTF-8、NUL 结尾，可 gzip（按魔数识别）。
 */
BIO_API int32_t bio_stats_scan_fastq(bio_stats_handle* stats,
                                     const char* input_path,
                                     bio_report_t* report);

/* 全局标量汇总。 */
typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_reads;
    int64_t total_bases;
    int64_t q20_bases;   /* Q20 及以上（含 Q30、Q40 那部分） */
    int64_t q30_bases;   /* Q30 及以上 */
    int64_t q40_bases;   /* Q40 及以上 */
    int64_t gc_bases;
    int32_t mean_length; /* 整数除法，与上游一致 */
    int32_t cycles;      /* 曲线长度 = 第一个"所有 read 都结束"的位置 */
    int32_t max_length;  /* 出现过的最大读长（读长分布数组长度减一） */
} bio_stat_summary_t;

BIO_API int32_t bio_stats_summary(bio_stats_handle* stats,
                                  bio_stat_summary_t* result);

/* 曲线种类。取值与 ``read_stats.h`` 的 ``StatCurve`` 一一对应，不要改。 */
typedef enum {
    BIO_STAT_CURVE_QUALITY_MEAN = 0,
    BIO_STAT_CURVE_QUALITY_A = 1,
    BIO_STAT_CURVE_QUALITY_T = 2,
    BIO_STAT_CURVE_QUALITY_C = 3,
    BIO_STAT_CURVE_QUALITY_G = 4,
    BIO_STAT_CURVE_QUALITY_N = 5,
    BIO_STAT_CURVE_CONTENT_A = 6,
    BIO_STAT_CURVE_CONTENT_T = 7,
    BIO_STAT_CURVE_CONTENT_C = 8,
    BIO_STAT_CURVE_CONTENT_G = 9,
    BIO_STAT_CURVE_CONTENT_N = 10,
    BIO_STAT_CURVE_CONTENT_GC = 11
} bio_stat_curve_kind_t;

/*
 * 取一条曲线（长度 = ``summary.cycles``）。
 *
 * ``capacity`` 是 ``values`` 能放多少个 double；不够时返回 BIO_ERR_OUTPUT，
 * 并把**实际需要的长度**写进 ``written``，调用方据此扩容后重试。
 * 成功时 ``written`` 是写入的元素个数。
 */
BIO_API int32_t bio_stats_curve(bio_stats_handle* stats,
                                int32_t kind,
                                double* values,
                                int32_t capacity,
                                int32_t* written);

/* 取质量值分布：下标即 Phred（0~93），需要 94 个 int64 的容量。 */
BIO_API int32_t bio_stats_quality_histogram(bio_stats_handle* stats,
                                            int64_t* counts,
                                            int32_t capacity,
                                            int32_t* written);

/* 取 5-mer 计数：需要 1024 个 int64 的容量。下标即编码——编码规则是
 * A=0、T=1、C=2、G=3，**高位在前**（下标 0 是 "AAAAA"，1023 是 "GGGGG"）。 */
BIO_API int32_t bio_stats_kmer(bio_stats_handle* stats,
                               int64_t* counts,
                               int32_t capacity,
                               int32_t* written);

/* 取读长分布：**下标即读长**，需要 ``summary.max_length + 1`` 个 int64 的容量。 */
BIO_API int32_t bio_stats_lengths(bio_stats_handle* stats,
                                  int64_t* counts,
                                  int32_t capacity,
                                  int32_t* written);

/* ---------------------------------------------------------------------------
 * 双端插入片段长度分布（fastp 的 insert size histogram）
 *
 * 逐对对两条 read 做 overlap 分析，把推算出的片段长度汇成直方图并找峰值。
 * 片段长度是**推算**出来的（靠重叠关系倒推），判不出来的一对进溢出桶，
 * 而不是丢掉。**只读不写**，不产出任何文件。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t max_size;          /* 直方图上限；<= 0 表示默认 512 */
    int32_t diff_limit;        /* overlap 参数：最大错配数 */
    int32_t require;           /* overlap 参数：最短重叠长度 */
    int32_t diff_percent_bp;   /* overlap 参数：错配比例上限，万分之一；<= 0 表示默认 2000 */
    int32_t allow_gap;         /* overlap 参数：是否允许 1 个插入/缺失 */
} bio_insert_size_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_pairs;
    int64_t overlapped_pairs;  /* 能判出片段长度的对数 */
    int32_t peak_size;         /* 峰值只在上限之内找 */
    int32_t max_size;          /* 回填实际使用的上限 */
} bio_insert_size_result_t;

/*
 * 统计一对（或一批）双端数据的插入片段长度分布。
 *
 * 直方图由**调用方分配**（长度需要 ``max_size + 1`` 个 int64，
 * 下标即片段长度，最后一项是溢出桶），实际写入多少由 ``histogram_length`` 回填。
 * 两份输入的记录数不一致时返回 BIO_ERR_INPUT_FORMAT。
 */
BIO_API int32_t bio_insert_size_fastq(
    const char* read1_path,
    const char* read2_path,
    const bio_insert_size_options_t* options,
    bio_insert_size_result_t* result,
    int64_t* histogram,
    int32_t histogram_capacity,
    int32_t* histogram_length);

/* ---------------------------------------------------------------------------
 * 过表达序列分析（fastp 的 over-representation analysis）
 *
 * 两步走：先在文件**开头一段**数据上找出"异常高频的片段"（候选），
 * 再在**全文件**上按采样量化它们。两步的数据范围不同是上游的刻意设计。
 *
 * 也用不透明累加器，但理由与统计不同：这里的问题是**条数未知**——
 * 检出多少条候选取决于数据本身，同样塞不进定长结构体。
 * ------------------------------------------------------------------------ */

/* 累加器句柄。内部布局不对外暴露。 */
typedef struct bio_overrep_handle bio_overrep_handle;

typedef struct {
    int32_t sampling;           /* 采样率；<= 0 表示默认 20 */
    int64_t base_limit;         /* 候选扫描的碱基上限；<= 0 表示默认 1510000 */
    int32_t seq_length_sample;  /* 确定读长时看前多少条；<= 0 表示默认 1000 */
} bio_overrep_options_t;

BIO_API bio_overrep_handle* bio_overrep_create(const bio_overrep_options_t* options);
BIO_API void bio_overrep_destroy(bio_overrep_handle* handle);

/* 跑完一份 FASTQ（内部会读两遍文件，见模块文档）。 */
BIO_API int32_t bio_overrep_scan_fastq(bio_overrep_handle* handle,
                                       const char* input_path,
                                       bio_report_t* report);

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_reads;
    int64_t total_bases;
    int64_t sampled_reads;
    int32_t seq_length;          /* 报告里位置分布的长度 */
    int32_t sampling;
    int32_t sequence_count;      /* 检出多少条 */
    int32_t max_sequence_length; /* 最长的那条有多长（配 buffer 用） */
} bio_overrep_summary_t;

BIO_API int32_t bio_overrep_summary(bio_overrep_handle* handle,
                                    bio_overrep_summary_t* result);

/*
 * 取第 ``index`` 条检出结果。
 *
 * ``sequence`` 是调用方分配的 NUL 结尾缓冲区（容量 ``sequence_capacity``）；
 * 分布写进 ``distribution``（容量 ``distribution_capacity``，长度由
 * ``distribution_length`` 回填）。容量不够时返回 BIO_ERR_OUTPUT。
 */
BIO_API int32_t bio_overrep_sequence(bio_overrep_handle* handle,
                                     int32_t index,
                                     char* sequence,
                                     int32_t sequence_capacity,
                                     int64_t* count,
                                     int64_t* estimated_count,
                                     double* base_percent,
                                     int64_t* distribution,
                                     int32_t distribution_capacity,
                                     int32_t* distribution_length);

/* ---------------------------------------------------------------------------
 * reads 规范化（质量编码转换 + MGI 名字修复）
 *
 * 处理链最前面的一步。**只动质量字符与名字**，不改碱基、不丢 read。
 * 单端与双端共用同一个入口（read2 传 NULL 即单端）。
 * ------------------------------------------------------------------------ */
typedef struct {
    int32_t input_phred;   /* 输入的质量编码偏移：33 或 64；<= 0 表示默认 33 */
    int32_t fix_mgi;       /* 是否把 xxx/1 改成 xxx /1 */
    int32_t compress;      /* bio_compress_t，只影响输出 */
} bio_normalize_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_reads;          /* 单端按 read 条数，双端亦按条（一对算两条） */
    int64_t renamed_reads;
    int64_t requantified_reads;
    int64_t input_bases;
    int64_t output_bases;         /* 恒等于 input_bases：规范化不改长度 */
} bio_normalize_result_t;

/*
 * reads 规范化。单端与双端共用：
 *
 * - ``read2_path`` 为 NULL 或空串 → 单端；
 * - 双端时两对路径（输入/输出）都必须给全，记录数必须一致。
 *
 * 调用失败时，已写出的半成品会被删除（双端两个都删）。
 */
BIO_API int32_t bio_normalize_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_normalize_options_t* options,
    bio_normalize_result_t* result);

/* ---------------------------------------------------------------------------
 * 按 index（barcode）黑名单过滤 reads
 *
 * 一次 run 里通常混着多个样本，靠 index 区分。命中黑名单的 read（双端时任一端
 * 命中就整对）被丢掉。**不改碱基、不改名字**，只决定去留。
 *
 * 判定的一个陷阱要记住：比较时**只比两者中较短的长度**，因此名字里取不到
 * index（空串）时与任何非空黑名单都算命中——那一批 read 会被全丢。这是上游
 * ``Filter::match`` 的行为，本实现照抄。
 * ------------------------------------------------------------------------ */
typedef struct {
    const char* const* blacklist1; /* 与 R1 第一段 index 比对；NULL 表示空 */
    int32_t blacklist1_count;
    const char* const* blacklist2; /* 与 R2 最后一段 index 比对（单端时忽略） */
    int32_t blacklist2_count;
    int32_t threshold;             /* 允许的错配数；< 0 表示默认 0 */
    int32_t compress;              /* bio_compress_t，只影响输出 */
} bio_index_filter_options_t;

typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];
    int64_t total_reads;    /* 单端按 read 条数，双端按 read 对数 */
    int64_t filtered_reads;
    int64_t input_bases;
    int64_t output_bases;
} bio_index_filter_result_t;

/*
 * 按 index 过滤。单端与双端共用（read2/output2 传 NULL 即单端）；
 * 两份黑名单都空时不过滤任何 read，只是把数据抄一遍。
 * 失败时已写出的半成品会被删除（双端两个都删）。
 */
BIO_API int32_t bio_index_filter_fastq(
    const char* read1_path,
    const char* read2_path,
    const char* output1_path,
    const char* output2_path,
    const bio_index_filter_options_t* options,
    bio_index_filter_result_t* result);

/* ---------------------------------------------------------------------------
 * 二色合成化学测序仪的判定（上游 Evaluator::isTwoColorSystem）
 *
 * 只看**第一条 read 的名字前缀**，认仪器型号；不读碱基、不做统计。用途是决定
 * 要不要自动开 polyG 修剪——那种化学里 G 是"两个荧光通道都没信号"，信号变暗
 * 的一簇会被读成成片的假 G。
 *
 * 与"接头检测"一样，它是工作流**阶段 A（预扫描）**的一环，由调用方单独调用、
 * 把结论作为配置传进工作流（理由见 workflow.h 文件头）。
 * ------------------------------------------------------------------------ */
BIO_API int32_t bio_detect_two_color_system(const char* read1_path,
                                            int32_t* is_two_color,
                                            bio_report_t* report);

/* ---------------------------------------------------------------------------
 * 工作流：fastp 预处理的完整处理链
 *
 * ============================================================================
 * 它做什么
 * ============================================================================
 *
 * 把前面那些算法按上游 fastp 的顺序串成一条流程：**一次读入、逐条走完整条链、
 * 分批写出**，省掉"每个算法各读一遍写一遍"的十几倍 I/O。它不重新实现任何
 * 判定——每一步调的都是上面那些函数的**内部实现**（同一份代码，不是重写）。
 *
 * 处理链（顺序本身就是要照抄的东西）：
 *
 *   读取端（保序）  过滤前统计 → 按 index 过滤 → 去重判定
 *   计算端（并行）  规范化 → UMI → 首尾修剪 + 滑窗剪切 → polyG
 *                   → 〔双端：overlap 分析 → 碱基校正 → 按 overlap 裁接头
 *                     → 接头二聚体判定〕→ 重叠区输出 → polyX → 限长截断
 *                   → 〔合并模式：重算 overlap 并合并〕→ reads 过滤判定
 *   写出端（保序）  过滤后统计 → 主输出 / 落单 / 失败 / 合并 / 重叠区分流
 *
 * 三端的分工不是随意切的：**判定顺序会影响结果的步骤只能放在单线程的保序
 * 位置**。去重就是典型——它的位图跨 read 累积，"谁先出现谁不算重复"，
 * 放进并行的计算端就会随线程调度漂移（上游正是如此，同一份数据换个线程数
 * 报出的重复率会变；本实现不会）。因此过滤前统计也一并放在读取端，
 * 与上游"先统计、再判去留"的口径对齐。
 *
 * ============================================================================
 * 阶段 A 不在这里
 * ============================================================================
 *
 * 接头检测（``bio_detect_adapter``）、二色判定（``bio_detect_two_color_system``）、
 * 总条数（``bio_stats_summary``）这三件"先看数据再决定怎么处理"的事由调用方
 * 先跑，结论作为配置传进来。理由见 workflow.h 文件头：它们本来就是本模块里
 * 独立可跑的算法，合在一起只会让这一层接口膨胀，而且中间结论对调用方不可见了。
 *
 * ============================================================================
 * 分卷
 * ============================================================================
 *
 * ``split_records > 0`` 时按"每卷多少条"滚动切分，产物名为序号前缀加原文件名
 * （``0001.out.fq``）。**分卷模式下只写主输出**——落单 / 失败 / 合并 / 重叠区
 * 四个输出无处安放，同时给它们会被判为参数矛盾（上游是静默不写并打一句警告；
 * 本实现选择报错，理由是用户明确要过的东西不该无声消失）。
 *
 * 上游的 ``--split``（按文件数）需要估算总条数，这里没有对应的参数：调用方
 * 已经为别的目的跑过一遍统计、手上有确切条数，除一下比在这里估算准。
 * ------------------------------------------------------------------------ */

/* 四个质量统计槽位（工作流跑完后按槽位取句柄，见 bio_workflow_stats）。 */
typedef enum {
    BIO_WORKFLOW_STATS_PRE1 = 0,  /* 过滤前 R1（单端时就是唯一的输入） */
    BIO_WORKFLOW_STATS_PRE2 = 1,  /* 过滤前 R2 */
    BIO_WORKFLOW_STATS_POST1 = 2, /* 过滤后 R1（合并模式下是合并后的结果） */
    BIO_WORKFLOW_STATS_POST2 = 3  /* 过滤后 R2 */
} bio_workflow_stats_slot_t;

/* 插入片段直方图的桶数（含最后那个溢出桶）。 */
#define BIO_WORKFLOW_INSERT_SIZE_MAX 512
#define BIO_WORKFLOW_INSERT_SIZE_BUCKETS (BIO_WORKFLOW_INSERT_SIZE_MAX + 1)

/*
 * 工作流的参数。
 *
 * 字段分为三组：路径、各步配置、运行方式。
 *
 * **各步的子结构体含义与单独调用那个算法时完全一致**，只有一点差别：
 * 子结构体里的 ``threads`` 与 ``compress`` 被忽略——工作流的线程数与压缩
 * 方式是全局的（见 ``threads`` / ``compress``），逐步子结构体里的同名字段
 * 只是为了复用既有类型，调用方不必填。
 *
 * 传 NULL 表示使用全部默认值；默认值是"什么都不做、只把数据抄一遍"，
 * 这不是个有用的配置，调用方至少要给出输入输出路径。
 */
typedef struct {
    /* --- 输入 --- */
    const char* read1_path;
    const char* read2_path; /* NULL 或空串 = 单端 */

    /* --- 输出 --- */
    const char* output1_path;
    const char* output2_path;
    const char* unpaired_path;   /* 双端落单 read 合并写这一个文件；NULL = 丢弃 */
    const char* failed_out;      /* 被丢弃的 read（名字后带原因标签）；NULL = 不写 */
    const char* merged_out;      /* NULL = 不合并；非空即进入合并模式 */
    const char* overlapped_out;  /* 无错配的重叠区；NULL = 不写 */

    /* --- reads 规范化 --- */
    int32_t normalize_enabled;
    int32_t input_phred; /* 33 或 64；<= 0 表示默认 33 */
    int32_t fix_mgi;     /* 是否把 xxx/1 改成 xxx /1 */

    /* --- 按 index 过滤 --- */
    int32_t index_filter_enabled;
    const char* const* index_blacklist1;
    int32_t index_blacklist1_count;
    const char* const* index_blacklist2;
    int32_t index_blacklist2_count;
    int32_t index_filter_threshold; /* 允许的错配数；< 0 表示默认 0 */

    /* --- UMI --- */
    int32_t umi_enabled;
    int32_t umi_location;  /* 取值见 bio_umi_options_t 上的说明 */
    int32_t umi_length;
    int32_t umi_skip;
    const char* umi_prefix;    /* NULL = 没有前缀 */
    const char* umi_delimiter; /* NULL 或空串 = 默认的 ':' */

    /* --- 首尾修剪 + 滑窗质量剪切（threads / compress 忽略） --- */
    bio_quality_trim_options_t quality_trim;

    /* --- 限长截断：长于它就从尾部截到该长度；0 = 不限 --- */
    int32_t max_length1;
    int32_t max_length2;

    /* --- polyG / polyX 尾部修剪（threads / compress 忽略） --- */
    bio_poly_trim_options_t poly_trim;

    /* --- 接头裁剪：候选表由调用方给（阶段 A 已检测好） --- */
    int32_t adapter_enabled;
    /* R1 的候选表。双端时先用 overlap 判，判不出接头再退化为按这里的序列匹配。 */
    const char* const* adapter_sequences;
    int32_t adapter_count;
    /* R2 的候选表。留空表示不对 R2 做按序列的匹配（上游 ``--adapter_sequence_r2``
     * 的默认值就是跟随 R1，所以通常两个表一样）。 */
    const char* const* adapter_sequences_r2;
    int32_t adapter_count_r2;
    int32_t adapter_match_required;  /* < 1 表示默认 4 */
    int32_t adapter_allow_one_gap;
    int32_t adapter_dimer_enabled;   /* 接头二聚体判定（双端） */
    int32_t adapter_dimer_max_len;   /* < 0 表示默认 2 */

    /* --- overlap / 碱基校正 / 合并（diff_percent_bp 用万分之一） --- */
    bio_overlap_options_t overlap;
    int32_t correction_enabled;
    int32_t merge_enabled;           /* 与 merged_out 非空等价，二者都给亦可 */
    int32_t merge_include_unmerged;  /* 未合并的 read 也写进合并输出 */

    /* --- reads 过滤（threads / compress 忽略） --- */
    bio_read_filter_options_t filter;

    /* --- 去重 --- */
    int32_t dedup_evaluate;      /* 只评估重复率（对应 --dont_eval_duplication 的反面） */
    int32_t dedup_enabled;       /* 真去重（--dedup），会丢数据 */
    int32_t dedup_accuracy_level; /* 1~6；<= 0 表示按模式取默认 */
    int64_t dedup_buffer_bytes;  /* 覆盖档位默认值；0 = 按档位 */

    /* --- 统计（关掉可以省一点时间，但报告里就没有曲线了） --- */
    int32_t stats_enabled;

    /* --- 输出组织 --- */
    int32_t compress;      /* bio_compress_t */
    int64_t split_records; /* > 0：每卷多少条 read；0 = 不分卷 */
    int32_t split_digits;  /* 序号前缀位数；< 0 表示默认 4 */

    /* --- 运行 --- */
    int32_t threads;      /* <= 0 表示由实现决定；1 = 单线程 */
    int64_t max_reads;    /* --reads_to_process；0 = 全部 */
} bio_workflow_options_t;

/*
 * 工作流的结果。**不透明句柄**——理由与统计器相同：产物里有长度在运行期才
 * 知道的曲线与直方图，塞不进定长结构体。
 *
 * 生命周期：``bio_run_workflow`` 成功时创建一个，调用方用完调
 * ``bio_workflow_destroy``。``bio_workflow_stats`` 返回的统计句柄**归它所有**，
 * 不要单独销毁、也不要在销毁结果之后再使用。
 */
typedef struct bio_workflow_result bio_workflow_result_t;

/* 汇总标量。字段含义逐条见 workflow.h 的 WorkflowResult。 */
typedef struct {
    int32_t status;
    char message[BIO_MESSAGE_CAPACITY];

    int64_t total_reads;  /* 读入的 read 条数（双端按条计，一对算两条） */
    int64_t total_bases;
    int64_t output_reads; /* 写进主输出的 read 条数（含落单 read） */
    int64_t output_bases;
    int64_t unpaired_reads;
    int64_t pairs_total;

    int64_t normalized_reads;
    int64_t index_filtered_reads;
    int64_t umi_tagged_reads;
    int64_t duplicate_reads; /* 单端按条、双端按对 */
    int32_t dedup_accuracy_level;

    int64_t trimmed_reads;
    int64_t poly_trimmed_reads;
    int64_t adapter_trimmed_reads;
    int64_t adapter_dimer_pairs;
    int64_t corrected_pairs;
    int64_t corrected_bases;

    int64_t filtered_reads;
    bio_filter_breakdown_t filter_breakdown;

    int64_t pairs_merged;
    int64_t gap_overlap_pairs;

    int32_t insert_size_peak;
    int64_t insert_size_unknown;
    int32_t split_file_count;
} bio_workflow_summary_t;

/*
 * 跑一次工作流。
 *
 * 参数：
 *   options  参数；NULL 表示全部默认值（那样会因为缺输入输出路径而失败）。
 *   result   成功时被指向一个新建的结果句柄；失败时保持不变。
 *   report   状态与消息（可为 NULL，但那样失败时拿不到原因）。
 *
 * 返回：
 *   bio_status_t 的值。失败时会**删除已写出的半成品**（含分卷产物）。
 */
BIO_API int32_t bio_run_workflow(const bio_workflow_options_t* options,
                                 bio_workflow_result_t** result,
                                 bio_report_t* report);

BIO_API void bio_workflow_destroy(bio_workflow_result_t* result);

/* 取某个槽位的质量统计句柄；未启用统计或槽位不适用时返回 NULL（不是错误）。 */
BIO_API bio_stats_handle* bio_workflow_stats(bio_workflow_result_t* result,
                                             int32_t slot);

/* 取汇总标量。 */
BIO_API int32_t bio_workflow_summary(bio_workflow_result_t* result,
                                     bio_workflow_summary_t* summary);

/*
 * 取插入片段直方图。单端时各项恒为 0。
 *
 * ``histogram`` 由调用方分配，需要 ``BIO_WORKFLOW_INSERT_SIZE_BUCKETS`` 个
 * int64；``written`` 回填实际写入的个数。容量不够返回 BIO_ERR_OUTPUT。
 */
BIO_API int32_t bio_workflow_insert_size(bio_workflow_result_t* result,
                                         int64_t* histogram,
                                         int32_t capacity,
                                         int32_t* written);

/* ---------------------------------------------------------------------------
 * 原生库版本
 *
 * 返回静态字符串（如 "0.7.0"），供 Python 侧在加载后校验，避免加载到过期的库。
 * ------------------------------------------------------------------------ */
BIO_API const char* bio_native_version(void);

#ifdef __cplusplus
}
#endif

#endif /* BIO_NATIVE_H */
