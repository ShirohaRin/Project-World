/*
 * pipeline.h —— "读取 → 计算 → 写出" 三段并行的流水线
 *
 * ============================================================================
 * 为什么需要它
 * ============================================================================
 *
 * 一个测序样本常是几十 GB，裸的单线程循环把三件事串成了一条链：
 *
 *     读一块（含 gzip 解压） → 算一块 → 写一块（含 gzip 压缩）
 *
 * 三件事互相独立，却被迫排队。这条流水线把它们拆到不同线程上重叠执行，
 * 并把 CPU 密集的逐碱基计算均摊到多个工作线程。
 *
 * ============================================================================
 * 结构
 * ============================================================================
 *
 *     ┌──────────┐        ┌────────────┐        ┌──────────┐
 *     │ reader   │──批次──▶│ worker × N │──批次──▶│ writer   │
 *     │ 1 个线程 │        │ 线程池      │        │ 1 个线程 │
 *     └──────────┘        └────────────┘        └──────────┘
 *          ▲                                           │
 *          └──────────── 反压：未写出批次数有上限 ◀───────┘
 *
 * 三条纪律保证"多线程与单线程输出逐字节一致"：
 *
 * 1. **按批次传递**。worker 处理的最小单位是一批 read（见 kBatchRecords），
 *    批次下标在整个流水线上保持单调，writer 严格按 0、1、2…… 写出，
 *    因此输出顺序永远等于输入顺序——无论线程怎么调度。
 * 2. **槽位环**。批次放在固定数量的槽里（槽数 = worker 数 + 2），
 *    reader 只能填"空"槽，worker 只能取"已填"槽，writer 只能写"已算完"槽。
 *    槽的三种状态由槽自己的互斥量保护，因此任何时刻只有一个线程碰一个批次，
 *    不需要锁住整条流水线。
 * 3. **有界在途**。未写出的批次数有上限，内存占用因此有上界，
 *    与输入文件大小无关——再大的文件也不会把内存吃满。
 *
 * 每批的统计在 worker 里算好，由 writer 按批次顺序累加，
 * 因此统计口径与单线程完全一致（同一批数据、同一个顺序、同一套加法）。
 *
 * ============================================================================
 * 使用
 * ============================================================================
 *
 *     auto stats = bio::run_pipeline(input, output, compress, threads,
 *                                    [&](bio::FastqRecord& record) -> bio::StepOutcome {
 *                                        ...就地改写 record，或者标记丢弃...
 *                                    });
 *
 * ``Step`` 是模板参数而不是 ``std::function``：它每条 read 都要调用一次，
 * 走模板才能让编译器内联进内循环，不必为一次间接调用付代价。
 *
 * ``threads <= 0`` 表示由实现自行决定线程数——具体取哪一个由调用方给出的
 * ``auto_policy`` 决定（按算法各自的实测，理由见 AutoThreads）；
 * ``threads == 1`` 显式走单线程直路，连线程都不创建。
 */

#ifndef BIO_PIPELINE_H
#define BIO_PIPELINE_H

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <exception>
#include <memory>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

#include "bio_native.h"
#include "fastq.h"

namespace bio {

/*
 * 逐条变换的结果。
 *
 * ``verdict`` 是给"过滤类"算法用的结果码：被判丢弃时说明**为什么**丢的
 * （数值定义见 bio_native.h 的 bio_filter_verdict_t，0 表示无特别原因）。
 * 只改序列的算法（质量剪切、poly 修剪）不填它，保持 0。
 */
struct StepOutcome {
    bool dropped = false;
    bool changed = false;
    int32_t verdict = 0;
};

/* 结果码的槽位数，与上游 fastp 的 FILTER_RESULT_TYPES 一致。 */
constexpr int kVerdictSlots = 32;

/*
 * 流水线的返回值。
 *
 * ``verdict_counts`` 按结果码计数（下标即结果码），只有被丢弃的 read 才计入。
 * 过滤类算法靠它得到"按原因分类"的失败明细；其余算法这些数字恒为 0。
 * 由每条 read **自己的处理器**（worker 线程或单线程直路）累加，
 * 不用原子量——每个批次只有一个线程在写，批次之间由 writer 按序合并。
 */
struct PipelineStats {
    bio_stats_t stats{};
    int64_t verdict_counts[kVerdictSlots] = {};
};

/*
 * ``threads <= 0``（自动）时该取什么，按算法各自的实测决定，不搞统一规则。
 *
 * - ``Single``：计算量小、瓶颈在磁盘与 gzip 编解码。质量剪切与 poly 修剪实测如此
 *   （100 万条 151bp：单线程 0.66s，16 线程 0.79s，反而慢 19%）。
 * - ``Hardware``：计算占比高，并行确实有收益。reads 过滤实测如此
 *   （30 万条 151bp 未压缩：单线程 240ms、2 线程 169ms、4 线程 132ms、8 线程 122ms，
 *   约 2.0×；同样数据走 gzip 时无收益，因为瓶颈转移到写出端的单线程 deflate）。
 *
 * 两个值都不影响结果，只影响速度——线程数与输出字节、统计数字无关，有常驻测试守着。
 */
enum class AutoThreads {
    Single,
    Hardware,
};

namespace pipeline_detail {

/*
 * 一个批次装多少条 read。
 *
 * 取 1024：批次越大，同步与调度的固定开销被摊得越薄；但批次太大时
 * 各线程的负载均衡变差（一批还没算完，别的线程已经闲着），内存峰值也更高。
 * 1024 条 × 150 bp 大约 0.5 MB，两侧都留有余地。
 */
constexpr std::size_t kBatchRecords = 1024;

/* 自动选择线程数时的上限。逐碱基计算是内存带宽敏感型，核数再多收益也趋平。 */
constexpr int kMaxAutoThreads = 16;

/*
 * 一个槽位：流水线上的一个批次，以及它的状态。
 *
 * 状态迁移只有三条合法路径，每条都由持锁的某一方独占完成：
 *
 *     Free ──reader 填入──▶ Filled ──worker 算完──▶ Computed ──writer 写出──▶ Free
 *
 * ``records`` 与 ``outcome`` 只在"持有该槽"的线程里读写，别的线程只读 ``state``，
 * 因此它们不需要额外的锁保护。
 */
struct Slot {
    std::mutex mutex;
    std::condition_variable cv;

    enum class State { Free, Filled, Computed };

    State state = State::Free;
    std::vector<FastqRecord> records;
    /* 被丢弃的 read（仅在调用方要求写失败输出时才收集）。 */
    std::vector<FastqRecord> dropped;
    PipelineStats outcome{};
};

/*
 * 把 threads 归一成实际要用的工作线程数。
 *
 * ``threads <= 0`` 表示"由实现决定"，取哪一个由 ``policy`` 给定（理由见 AutoThreads）；
 * ``threads == 1`` 显式单线程，``> 1`` 显式多线程。
 */
inline int resolve_threads(int threads, AutoThreads policy) {
    if (threads > 0) {
        return std::min(threads, kMaxAutoThreads);
    }
    if (policy == AutoThreads::Single) {
        return 1;
    }
    const unsigned int hardware = std::thread::hardware_concurrency();
    const int count = hardware == 0 ? 1 : static_cast<int>(hardware);
    return std::min(count, kMaxAutoThreads);
}

/* 累加两批统计。字段口径见 bio_native.h。 */
inline void add_stats(PipelineStats& total, const PipelineStats& part) {
    total.stats.total_reads += part.stats.total_reads;
    total.stats.kept_reads += part.stats.kept_reads;
    total.stats.changed_reads += part.stats.changed_reads;
    total.stats.dropped_reads += part.stats.dropped_reads;
    total.stats.bases_before += part.stats.bases_before;
    total.stats.bases_after += part.stats.bases_after;
    for (int slot = 0; slot < kVerdictSlots; ++slot) {
        total.verdict_counts[slot] += part.verdict_counts[slot];
    }
}

/* 记一条被丢弃的 read：累加统计，并按结果码计数（结果码 0 不计）。 */
inline void note_dropped(PipelineStats& outcome, int32_t verdict) {
    ++outcome.stats.dropped_reads;
    if (verdict > 0 && verdict < kVerdictSlots) {
        ++outcome.verdict_counts[verdict];
    }
}

/*
 * 被丢弃的 read 在失败输出（failed_out）里的原因标签。
 *
 * 取值与上游 ``src/common.h`` 的 ``FAILED_TYPES`` 同口径——那张表按结果码索引、
 * 每隔 4 个槽位放一个名字（中间是空串占位）。未识别的结果码给 "passed"，
 * 与上游表里下标 0 的写法一致。
 */
inline const char* verdict_label(int32_t verdict) {
    switch (verdict) {
        case 4:  return "failed_polyx_filter";
        case 8:  return "failed_bad_overlap";
        case 12: return "failed_too_many_n_bases";
        case 16: return "failed_too_short";
        case 17: return "failed_too_long";
        case 20: return "failed_quality_filter";
        case 24: return "failed_low_complexity";
        case 28: return "failed_adapter_dimer";
        default: return "passed";
    }
}

/*
 * 单线程直路：不创建任何线程，逐条读、算、写。
 *
 * ``dropped_path`` 非空时，被丢弃的 read 会原样写进那个文件（名字后追加失败原因），
 * 用于回溯"丢掉了什么、为什么丢"；留空则只写通过的那些。
 */
template <typename Step>
PipelineStats run_serial(const std::string& input_path,
                         const std::string& output_path,
                         bool compress,
                         Step& step,
                         const std::string& dropped_path) {
    FastqReader reader(input_path);
    FastqWriter writer(output_path, compress);
    std::unique_ptr<FastqWriter> dropped_writer;
    if (!dropped_path.empty()) {
        dropped_writer = std::make_unique<FastqWriter>(dropped_path, compress);
    }

    PipelineStats outcome{};
    bio_stats_t& stats = outcome.stats;
    FastqRecord record;
    while (reader.next(record)) {
        ++stats.total_reads;
        stats.bases_before += static_cast<int64_t>(record.length());

        const StepOutcome step_outcome = step(record);
        if (step_outcome.dropped) {
            note_dropped(outcome, step_outcome.verdict);
            if (dropped_writer != nullptr) {
                /* 序列与质量原样保留，只在名字后追加原因（与上游一致）。 */
                record.name += ' ';
                record.name += verdict_label(step_outcome.verdict);
                dropped_writer->write(record);
            }
            continue;
        }
        if (step_outcome.changed) {
            ++stats.changed_reads;
        }
        ++stats.kept_reads;
        stats.bases_after += static_cast<int64_t>(record.length());
        writer.write(record);
    }
    writer.close();
    if (dropped_writer != nullptr) {
        dropped_writer->close();
    }
    return outcome;
}

/*
 * 多线程流水线。
 *
 * 线程归属：
 *   - producer：只读输入、只填槽（解压在这里发生）；
 *   - worker × N：只算，不改动输入顺序，也不写文件；
 *   - 调用线程：充当 writer，按批次下标严格顺序写出（压缩在这里发生）。
 *
 * 任一环节抛异常都会立刻置 aborted 并唤醒所有等待者，让各线程尽快退出；
 * 异常被捕获到 ``error``，等所有线程 join 之后再原样抛出，
 * 因此调用方看到的错误与单线程完全一样。
 */
template <typename Step>
PipelineStats run_parallel(const std::string& input_path,
                           const std::string& output_path,
                           bool compress,
                           int worker_count,
                           Step& step,
                           const std::string& dropped_path) {
    FastqReader reader(input_path);
    FastqWriter writer(output_path, compress);
    std::unique_ptr<FastqWriter> dropped_writer;
    if (!dropped_path.empty()) {
        dropped_writer = std::make_unique<FastqWriter>(dropped_path, compress);
    }

    /* 槽数必须大于 worker 数：否则所有槽都在"已填/已算完"手里，
     * reader 没有可填的槽，流水线会互相等死。 */
    const std::size_t slot_count = static_cast<std::size_t>(worker_count) + 2;
    const int64_t outstanding_limit =
        static_cast<int64_t>(std::max(4, worker_count * 4));

    std::vector<Slot> slots(slot_count);

    std::mutex state_mutex;
    std::condition_variable work_cv;   /* worker 等"有已填好的批次" */
    std::condition_variable space_cv;  /* reader 等"在途批次降到上限以下" */

    std::deque<int64_t> ready;        /* 已填好、等待计算的槽位下标 */
    bool reader_finished = false;     /* reader 正常读完（受 state_mutex 保护） */

    std::atomic<int64_t> issued{0};   /* reader 已发出的批次数 */
    std::atomic<int64_t> written{0};  /* writer 已写出的批次数 */
    std::atomic<bool> reader_done{false};
    std::atomic<bool> aborted{false};
    std::exception_ptr error;

    /* 出错：记录第一个异常，唤醒所有线程，让它们在最近的检查点退出。 */
    auto fail = [&](std::exception_ptr captured) {
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            if (error == nullptr) {
                error = std::move(captured);
            }
        }
        aborted.store(true);
        work_cv.notify_all();
        space_cv.notify_all();
        for (Slot& slot : slots) {
            slot.cv.notify_all();
        }
    };

    std::thread producer([&] {
        try {
            std::vector<FastqRecord> buffer;
            buffer.reserve(kBatchRecords);
            while (true) {
                buffer.clear();
                FastqRecord record;
                while (buffer.size() < kBatchRecords && reader.next(record)) {
                    buffer.push_back(record);
                }
                if (buffer.empty()) {
                    break;  /* 读到结尾 */
                }

                const int64_t index = issued.load();
                {
                    /* 反压：在途批次太多就先等一等，避免内存随文件大小增长。 */
                    std::unique_lock<std::mutex> lock(state_mutex);
                    space_cv.wait(lock, [&] {
                        return aborted.load() || issued.load() - written.load() < outstanding_limit;
                    });
                    if (aborted.load()) {
                        return;
                    }
                }

                Slot& slot = slots[static_cast<std::size_t>(index) % slot_count];
                {
                    std::unique_lock<std::mutex> lock(slot.mutex);
                    slot.cv.wait(lock, [&] {
                        return slot.state == Slot::State::Free || aborted.load();
                    });
                    if (aborted.load()) {
                        return;
                    }
                    slot.records = std::move(buffer);
                    slot.dropped.clear();
                    slot.outcome = PipelineStats{};
                    slot.state = Slot::State::Filled;
                }
                issued.store(index + 1);

                {
                    std::lock_guard<std::mutex> lock(state_mutex);
                    ready.push_back(index);
                }
                work_cv.notify_one();
            }

            {
                std::lock_guard<std::mutex> lock(state_mutex);
                reader_finished = true;
            }
            reader_done.store(true);
            work_cv.notify_all();
            /*
             * 还要唤醒每个槽的等待者。写出端等的是**槽的**条件变量，不是 work_cv；
             * 输入为空时读取端一个批次都没发出就收工，写出端正卡在 0 号槽上，
             * 只通知 work_cv 它永远醒不过来。
             */
            for (Slot& slot : slots) {
                slot.cv.notify_all();
            }
        } catch (...) {
            fail(std::current_exception());
        }
    });

    auto worker_body = [&] {
        try {
            while (true) {
                int64_t index = 0;
                {
                    std::unique_lock<std::mutex> lock(state_mutex);
                    work_cv.wait(lock, [&] {
                        return aborted.load() || !ready.empty() || reader_finished;
                    });
                    if (aborted.load()) {
                        return;
                    }
                    if (ready.empty()) {
                        if (reader_finished) {
                            return;  /* 队列空且读完了：收工 */
                        }
                        continue;
                    }
                    index = ready.front();
                    ready.pop_front();
                }

                Slot& slot = slots[static_cast<std::size_t>(index) % slot_count];
                PipelineStats outcome{};
                bio_stats_t& stats = outcome.stats;
                std::size_t kept = 0;
                for (std::size_t position = 0; position < slot.records.size(); ++position) {
                    FastqRecord& record = slot.records[position];
                    ++stats.total_reads;
                    stats.bases_before += static_cast<int64_t>(record.length());

                    const StepOutcome step_outcome = step(record);
                    if (step_outcome.dropped) {
                        note_dropped(outcome, step_outcome.verdict);
                        if (dropped_writer != nullptr) {
                            /* 收集起来交给写出方；压掉的话就再也拿不到了。 */
                            record.name += ' ';
                            record.name += verdict_label(step_outcome.verdict);
                            slot.dropped.push_back(std::move(record));
                        }
                        continue;
                    }
                    if (step_outcome.changed) {
                        ++stats.changed_reads;
                    }
                    ++stats.kept_reads;
                    stats.bases_after += static_cast<int64_t>(record.length());

                    /* 丢弃的 read 就地压掉，写出时不必再判断一遍。 */
                    if (kept != position) {
                        slot.records[kept] = std::move(record);
                    }
                    ++kept;
                }
                if (kept != slot.records.size()) {
                    slot.records.resize(kept);
                }
                slot.outcome = outcome;

                {
                    std::lock_guard<std::mutex> lock(slot.mutex);
                    slot.state = Slot::State::Computed;
                }
                slot.cv.notify_all();
            }
        } catch (...) {
            fail(std::current_exception());
        }
    };

    std::vector<std::thread> workers;
    workers.reserve(static_cast<std::size_t>(worker_count));
    for (int i = 0; i < worker_count; ++i) {
        workers.emplace_back(worker_body);
    }

    PipelineStats total{};
    int64_t index = 0;
    /*
     * 写出阶段的异常必须在这里就地收住，不能直接往外抛：
     * 此时 producer 与 workers 还在运行，若异常提前离开本函数，
     * `std::thread` 析构时会因为线程仍 joinable 而直接 terminate 整个进程。
     *
     * 等待谓词里必须带上"读取端已收工且这一批不存在"这一条：**输入为空**时
     * 读取端一个批次都没发出就收工，写出端卡在 0 号槽上，只有它能让写出端醒着
     * 离开——否则通知到了、谓词看一眼又睡回去，整个流程就停在那里了。
     */
    try {
        while (true) {
            if (reader_done.load() && index >= issued.load()) {
                break;  /* 输入读完且本批已写出 */
            }
            Slot& slot = slots[static_cast<std::size_t>(index) % slot_count];
            {
                std::unique_lock<std::mutex> lock(slot.mutex);
                slot.cv.wait(lock, [&] {
                    return slot.state == Slot::State::Computed || aborted.load() ||
                           (reader_done.load() && index >= issued.load());
                });
                if (aborted.load()) {
                    break;
                }
                if (slot.state != Slot::State::Computed) {
                    break;  /* 读取端已收工，这一批根本不存在 */
                }
                for (const FastqRecord& record : slot.records) {
                    writer.write(record);
                }
                if (dropped_writer != nullptr) {
                    for (const FastqRecord& record : slot.dropped) {
                        dropped_writer->write(record);
                    }
                }
                add_stats(total, slot.outcome);
                slot.state = Slot::State::Free;
            }
            slot.cv.notify_all();
            written.store(index + 1);
            space_cv.notify_all();
            ++index;
        }
    } catch (...) {
        fail(std::current_exception());
    }

    producer.join();
    for (std::thread& worker : workers) {
        worker.join();
    }

    if (error != nullptr) {
        /* 交给调用方统一处理：局部对象析构会关闭输出文件，上层再删半成品。 */
        std::rethrow_exception(error);
    }
    writer.close();
    if (dropped_writer != nullptr) {
        dropped_writer->close();
    }
    return total;
}

}  // namespace pipeline_detail

/* 见文件头的"使用"。单线程与多线程的差别只在这一层选择。 */
template <typename Step>
PipelineStats run_pipeline(const std::string& input_path,
                           const std::string& output_path,
                           bool compress,
                           int threads,
                           AutoThreads auto_policy,
                           Step step,
                           const std::string& dropped_path = std::string()) {
    const int worker_count = pipeline_detail::resolve_threads(threads, auto_policy);
    try {
        if (worker_count <= 1) {
            return pipeline_detail::run_serial(input_path, output_path, compress, step,
                                               dropped_path);
        }
        return pipeline_detail::run_parallel(input_path, output_path, compress,
                                             worker_count, step, dropped_path);
    } catch (...) {
        /*
         * 中途失败：删掉已写出的半成品（两个输出都要删）。
         * 此时 writer 已经离开作用域（或已被关闭），文件句柄不再被占用。
         */
        remove_file_if_exists(output_path);
        if (!dropped_path.empty()) {
            remove_file_if_exists(dropped_path);
        }
        throw;
    }
}

}  // namespace bio

#endif /* BIO_PIPELINE_H */
