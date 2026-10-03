/*
 * dedup.h —— 重复序列检测与去重（fastp 的 Duplicate / --dedup）
 *
 * ============================================================================
 * 它做什么
 * ============================================================================
 *
 * 用**布隆过滤器**判"这条序列之前见过没有"：不为每条序列存原文，而是把序列
 * 压成若干个 64 位位置向量，把这些位置对应的比特置 1。一条序列**所有**位置上的
 * 比特在本次运行中**都已经被置起**，就判为重复。代价是**假阳性**——两条不同的
 * 序列可能撞到同一组比特上。这是上游的取舍（省内存换速度），本实现照搬。
 *
 * 本文件与 Python 侧 ``submodules/deduplication/algorithm.py`` 逐行对应，
 * 那份 Python 实现是**可审查的规格**，这份是生产实现。
 *
 * ============================================================================
 * 为什么这一条不走 pipeline.h
 * ============================================================================
 *
 * 公共流水线（``pipeline.h``）的模型是"逐条独立变换 + 批次并行"，而本算法的
 * 判定**依赖此前处理过的所有 read**——位图是跨 read 累积的共享状态，
 * 判定顺序不同，谁被判为重复就不同。把有状态判定塞进 worker 池，
 * 结果就会随线程调度漂移，破坏"多线程与单线程逐字节一致"这条纪律。
 *
 * 因此本实现**固定为单线程串行判定**：读（含解压）→ 判重 → 写（含压缩）
 * 依次执行。能并行的只有读取端解压，而这一点留待后续优化——先把口径做对。
 * 与上游的差别要如实说明：上游在 worker 线程里判重，所以**同样一份数据
 * 换个线程数，它报出的重复率会变**；本实现的重复率是数据的确定函数。
 */

#ifndef BIO_DEDUP_H
#define BIO_DEDUP_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "bio_native.h"
#include "fastq.h"

namespace bio {

/* 档位 → (缓冲区个数, 每个缓冲区的字节数)。总内存 = 两者相乘。
 *
 * 数值照抄上游 ``Duplicate::Duplicate``——它是**产品参数**（用户看到的就是
 * "多花多少内存换多少准确度"）：1G / 2G / 4G / 8G / 16G / 32G。 */
struct DedupMemoryPlan {
    int32_t accuracy_level;
    std::size_t buffer_count;
    std::size_t buffer_bytes;
};

/* 支持的内存档位，按档位号排列。 */
extern const DedupMemoryPlan kDedupMemoryPlan[6];

/* 只评估重复率（不去重）时的默认档位。上游同样如此。 */
constexpr int32_t kDefaultAccuracyAnalyze = 1;
/* 真正去重时的默认档位：多花内存降低假阳性，因为判错了要丢数据。上游同样如此。 */
constexpr int32_t kDefaultAccuracyDedup = 3;

struct DedupOptions {
    /* 内存档位 1~6。 */
    int32_t accuracy_level = kDefaultAccuracyAnalyze;
    /* 直接指定每个缓冲区的字节数，覆盖档位默认值；0 表示按档位取值。
     *
     * 两个用途：小内存机器上压低头寸；以及**与 Python 侧对拍时指定同一份配置**
     * ——位图大小决定位置向量怎么取模、进而决定假阳性率，两侧必须同值。 */
    uint64_t buffer_bytes = 0;
    /* 是否真的丢弃重复。false 表示只统计（此时调用方不传输出文件）。 */
    bool dedup = false;
};

/* 把档位解析成实际要用的内存配置（含 ``buffer_bytes`` 覆盖）。 */
DedupMemoryPlan resolve_dedup_plan(const DedupOptions& options);

/* 一次去重运行的统计。 */
struct DedupStats {
    bio_stats_t stats{};
    /* 判为重复的数量。单端时是条数，双端时是**对数**（与 stats.total_reads 同单位）。 */
    int64_t duplicate_reads = 0;
    /* 实际使用的内存档位（经默认值解析之后），供报告如实写出。 */
    int32_t accuracy_level = kDefaultAccuracyAnalyze;
};

/*
 * 布隆过滤器式的重复检测器：逐条喂入，问它"见过没有"。
 *
 * 一个实例就是**一次运行的全部记忆**——换了数据集要新建实例，
 * 否则上一批的比特还留着，会凭空多出重复。
 *
 * 位图是 ``buffer_count * buffer_bytes`` 字节，档位 1 就有 1 GiB，
 * 因此构造开销不小；一个进程里别反复构造同一个档位的检测器。
 */
class DuplicateDetector {
public:
    explicit DuplicateDetector(const DedupOptions& options);

    DuplicateDetector(const DuplicateDetector&) = delete;
    DuplicateDetector& operator=(const DuplicateDetector&) = delete;

    /* 单端：这条序列之前见过没有。统计随之更新。 */
    bool check_read(const std::string& sequence);

    /* 双端：把 R1 与 R2 **首尾相接**后问"这一对见过没有"。
     *
     * 因此一对 (R1, R2) 与另一对**调换了 R1/R2** 不算重复（位置不同）。 */
    bool check_pair(const std::string& read1, const std::string& read2);

    int64_t total_reads() const { return total_reads_; }
    int64_t duplicate_reads() const { return duplicate_reads_; }
    double duplicate_rate() const;

    int32_t accuracy_level() const { return plan_.accuracy_level; }
    std::size_t bitmap_bytes() const { return plan_.buffer_count * plan_.buffer_bytes; }

private:
    /* 把一段序列累加进位置向量。``pos_offset`` 给双端用：R2 的位置从 len(R1) 起算。 */
    void accumulate(const char* data, std::size_t length, uint64_t* positions,
                    std::size_t pos_offset) const;

    /* 把位置向量对应的比特全部置 1；返回"置之前是否全都已经是 1"。 */
    bool apply_bitmap(const uint64_t* positions);

    DedupMemoryPlan plan_;
    /* 位置向量在素数表里的下标用"与掩码"取值而不是取模：素数表长度
     * 512 × 缓冲区个数 恒为 2 的幂，与掩码等价且更快。 */
    std::size_t offset_mask_ = 0;
    std::size_t buffer_bits_ = 0;
    std::vector<uint64_t> primes_;
    std::vector<unsigned char> bitmap_;
    int64_t total_reads_ = 0;
    int64_t duplicate_reads_ = 0;
};

/*
 * 单端：读一份 FASTQ，判重，可选地写出不重复的那些。
 *
 * ``output_path`` 为空表示**只评估**——不写任何文件，只回统计。
 * ``compress`` 只影响输出（输入是否 gzip 由 FastqReader 自己识别）。
 */
DedupStats deduplicate_single(const std::string& input_path,
                              const std::string& output_path,
                              bool compress,
                              const DedupOptions& options);

/*
 * 双端：读一对 FASTQ，把每对的 R1+R2 首尾相接后判重，可选地成对写出。
 *
 * 两份输入的记录数必须一致，不一致抛 ``FastqFormatError``。
 * ``output1_path`` / ``output2_path`` 同时为空表示只评估。
 */
DedupStats deduplicate_paired(const std::string& input1_path,
                              const std::string& output1_path,
                              const std::string& input2_path,
                              const std::string& output2_path,
                              bool compress,
                              const DedupOptions& options);

/* 素数表：给定长度，返回"每万个数的区间里取第一个素数"得到的表。
 *
 * **它不是"前 N 个素数"**。这一点很容易看漏——按"前 N 个连续素数"实现，
 * 哈希位置会全错，而错的结果**看起来仍然像合理的随机哈希**（重复率数字依然
 * 合理），因此必须有对照向量才能发现。暴露出来供测试直接核对。 */
std::vector<uint64_t> make_dedup_prime_table(std::size_t count);

/* 碱基 → 哈希权重。取值照抄上游 SEQ_HASH_VAL：A=7、C=74、G=31、T=222，
 * 其余字符（含 N、小写碱基与非碱基字符）一律 13。 */
extern const std::array<uint64_t, 256> kDedupHashValue;

}  // namespace bio

#endif /* BIO_DEDUP_H */
