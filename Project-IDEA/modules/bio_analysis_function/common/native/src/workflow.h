/*
 * workflow.h —— fastp 预处理的完整工作流编排
 *
 * ============================================================================
 * 它是什么
 * ============================================================================
 *
 * 把本模块已有的十六个算法按上游 fastp 的顺序串成一条完整的预处理流程，
 * **一次读入、逐条走完整条链、分批写出**。它不重新实现任何算法——
 * 每一步都调用既有算法层的函数（quality_trim / poly_trim / adapter_trim /
 * read_filter / paired_* / normalize / index_filter / dedup / ...）。
 *
 * 按 `开发规则.md` 3.2，它既不是"能独立回答一个问题的算法"（第 1 类，
 * 进 submodules/），也不是"算法必须使用的工具"（第 2 类，进 common/），
 * 而是**编排**：它回答的问题是"这份原始数据经过标准预处理之后变成什么样、
 * 中间掉了多少"，产物是文件集合与一份汇总。
 *
 * ============================================================================
 * 与上游 fastp 的两段式结构对应
 * ============================================================================
 *
 * fastp 在真正处理之前先跑一遍 Evaluator 做决策（读长、条数、是否二色测序、
 * 自动检测接头、过表达序列），结论写回 Options，然后才进单遍主循环。
 * 本实现把这个"阶段 A"留在**上层调用方**（Python 侧），理由有三条：
 *
 *   1. 阶段 A 的每一件事本来就是本模块里**独立可跑的算法**——接头检测有
 *      `adapter_detection`、读长与条数统计有 `read_stats`、过表达序列有
 *      `overrepresented_sequences`。让它们各自跑、结论传进来，比在这里再包
 *      一层更好——中间结论因此对调用方可见，可单独验证、可写进日志。
 *   2. 阶段 A 偏 I/O 采样，与阶段 B 的逐条计算没有共享状态，合在一起只会
 *      让本文件的接口膨胀。
 *   3. 上游那样合在一起，是因为它只有命令行一个入口；本模块的调用方是
 *      Python 编排层，有地方放这段逻辑。
 *
 * 于是本文件的输入是**已经确定下来的配置**（含检测到的接头序列），
 * 主循环里不再回头做任何决策——这一点与上游一致。
 *
 * ============================================================================
 * 逐条处理顺序（照抄上游 PairEndProcessor::processPairEnd / SingleEndProcessor）
 * ============================================================================
 *
 * 顺序本身就是要照抄的东西，别随意调整：
 *
 *   读取端（保序）  过滤前统计 → 按 index 过滤 → 去重判定
 *   计算端（并行）  fixMGI/质量编码规范化 → UMI → 首尾固定修剪 + 滑窗质量剪切
 *                   → polyG → 〔双端：overlap 分析〕→ 碱基校正 → 按 overlap
 *                   裁接头 → 接头二聚体判定 → 重叠区输出 → polyX → 限长截断
 *                   → 〔merge 模式：重算 overlap 并合并〕→ reads 过滤判定
 *   写出端（保序）  过滤后统计 → 主输出 / 落单 / 失败 / 合并 / 重叠区分流
 *
 * 三处容易记错的点：
 *
 *   - **去重判定的对象是原始 read**（还没修剪），所以它在最前面；它在读取端
 *     执行是按本模块的纪律——见下面"为什么去重在读取端"。
 *   - **polyG 在质量剪切之后、接头裁剪之前；polyX 在接头裁剪之后。**
 *   - **overlap 分析一次算好、多处复用，但 merge 之前必须重算**——因为读
 *     到这里 read 已经被前面的步骤改过了（上游为 issue #675 做的修正）。
 *
 * ============================================================================
 * 为什么去重在读取端
 * ============================================================================
 *
 * `DuplicateDetector` 的位图跨 read 累积，"谁先出现谁不算重复"，因此结果与
 * 判定顺序绑定。上游在 worker 线程里带锁判重，于是**同一份数据换个线程数，
 * 报出的重复率就会变**；本模块的纪律是"线程数不影响结果"（见 pipeline.h），
 * 所以判定只能放在单线程、按文件顺序执行的读取端。
 *
 * ============================================================================
 * 分卷
 * ============================================================================
 *
 * 只支持"每卷多少条"这一种口径（上游的 `--split_by_lines` 语义）。上游的
 * `--split`（按文件数）需要估算总条数，本实现把这个除法留给调用方——上层
 * 已经为了别的原因跑过一遍统计，手上就有确切的条数，除一下比在这里估算准。
 *
 * 与上游一致的两条约束：**分卷模式下只写主输出**（落单、失败、合并、重叠区
 * 都不写），且**落单 read 无处可写**。后者由调用方在配置层面挡住，本层只在
 * 分卷生效时忽略那几个输出路径。
 */

#ifndef BIO_WORKFLOW_H
#define BIO_WORKFLOW_H

#include <cstdint>
#include <string>
#include <vector>

#include "adapter_trim.h"
#include "bio_native.h"
#include "index_filter.h"
#include "insert_size.h"
#include "normalize.h"
#include "overlap.h"
#include "poly_trim.h"
#include "quality_trim.h"
#include "read_filter.h"
#include "read_stats.h"
#include "umi_process.h"

namespace bio {

/* 插入片段直方图的桶数：与上游 ``insertSizeMax`` + 1（最后一项是溢出桶）。 */
constexpr int32_t kWorkflowInsertSizeMax = kDefaultInsertSizeMax;

/* 四个统计槽位（供调用方把内部 ``ReadStats`` 借出来填）。 */
struct WorkflowStatsSink {
    ReadStats* pre1 = nullptr;   /* 过滤前 R1（单端时即唯一的输入） */
    ReadStats* pre2 = nullptr;   /* 过滤前 R2 */
    ReadStats* post1 = nullptr;  /* 过滤后 R1（merge 模式下即合并后的结果） */
    ReadStats* post2 = nullptr;  /* 过滤后 R2 */
};

/* 工作流的完整配置。各子结构体的语义与单独调用那个算法时完全一致。 */
struct WorkflowOptions {
    /* --- 输入 --- */
    std::string read1_path;
    std::string read2_path;  /* 空串 = 单端 */

    /* --- 输出 --- */
    std::string output1_path;
    std::string output2_path;
    std::string unpaired_path;    /* 双端落单 read；空串 = 丢弃 */
    std::string failed_path;      /* 被丢弃的 read（带原因标签）；空串 = 不写 */
    std::string merged_path;      /* 空串 = 不合并 */
    std::string overlapped_path;  /* 重叠区输出；空串 = 不写 */

    /* --- 各步配置（开关在各自的结构体里，这里只放总开关） --- */
    bool normalize_enabled = false;
    NormalizeOptions normalize;

    bool index_filter_enabled = false;
    IndexFilterOptions index_filter;

    bool umi_enabled = false;
    UmiOptions umi;

    QualityCutOptions quality_cut;
    PolyTrimOptions poly_trim;

    /*
     * 限长截断（上游 ``--max_len1/2``）：长于它的 read 从尾部截到该长度。
     * 0 表示不限。**这一项只在工作流层实现**——它是输出整形而不是一个判定，
     * 单独的质量剪切模块没有对应参数，两者不共享这个字段。
     */
    int32_t max_length1 = 0;
    int32_t max_length2 = 0;

    bool adapter_enabled = false;
    AdapterTrimOptions adapter;     /* R1 的候选接头表（阶段 A 已检测好） */
    AdapterTrimOptions adapter_r2;  /* R2 的候选表；空表示不做 R2 的按序列表裁剪 */

    bool adapter_dimer_enabled = false;
    int32_t adapter_dimer_max_len = 2;

    bool correction_enabled = false;
    bool merge_enabled = false;
    bool merge_include_unmerged = false;
    OverlapOptions overlap;

    ReadFilterOptions filter;

    bool dedup_evaluate = false;  /* --dont_eval_duplication 的反面 */
    bool dedup_enabled = false;   /* --dedup */
    int32_t dedup_accuracy_level = 0;
    uint64_t dedup_buffer_bytes = 0;

    bool stats_enabled = true;

    /* --- 输出组织 --- */
    bool compress = false;
    int64_t split_records = 0;  /* > 0：每卷多少条 read；0 = 不分卷 */
    int32_t split_digits = 4;   /* 序号前缀的位数 */

    /* --- 运行 --- */
    int32_t threads = 0;
    int64_t max_reads = 0;  /* --reads_to_process；0 = 全部 */

    /* --- 统计去向 --- */
    WorkflowStatsSink stats;
};

/*
 * 一次工作流的结果。
 *
 * 只带**标量**与直方图；四个质量统计借 `WorkflowStatsSink` 直接写进调用方
 * 提供的 ``ReadStats`` 里（它们是重对象，没必要在这里再存一份）。
 */
struct WorkflowResult {
    /* 全局 */
    int64_t total_reads = 0;   /* 读入的 read 条数（双端按条计，一对算两条） */
    int64_t total_bases = 0;
    int64_t output_reads = 0;  /* 写进主输出的 read 条数 */
    int64_t output_bases = 0;
    int64_t unpaired_reads = 0;
    int64_t pairs_total = 0;

    /* 各步 */
    int64_t normalized_reads = 0;
    int64_t index_filtered_reads = 0;
    int64_t umi_tagged_reads = 0;
    int64_t duplicate_reads = 0;      /* 单端按条、双端按对 */
    int32_t dedup_accuracy_level = 0;
    int64_t trimmed_reads = 0;        /* 首尾修剪或滑窗剪切改过的条数 */
    int64_t poly_trimmed_reads = 0;
    int64_t adapter_trimmed_reads = 0;
    int64_t adapter_dimer_pairs = 0;
    int64_t corrected_pairs = 0;
    int64_t corrected_bases = 0;
    int64_t filtered_reads = 0;
    bio_filter_breakdown_t filter_breakdown{};
    int64_t pairs_merged = 0;
    int64_t gap_overlap_pairs = 0;
    int32_t insert_size_peak = 0;
    int64_t insert_size_unknown = 0;
    int32_t split_file_count = 0;

    /* 长度 = kWorkflowInsertSizeMax + 1，最后一项是溢出桶。单端时全 0。 */
    std::vector<int64_t> insert_size_histogram;
};

/*
 * 跑一次完整工作流。
 *
 * 失败时（输入打不开、格式不对、输出写不了）抛 ``InputFileError`` /
 * ``FastqFormatError`` / ``OutputFileError``，并**删除已写出的半成品**。
 * 参数自相矛盾（分卷与落单输出同时启用、双端缺 R2 等）抛 std::invalid_argument。
 */
WorkflowResult run_workflow(const WorkflowOptions& options);

}  // namespace bio

#endif /* BIO_WORKFLOW_H */
