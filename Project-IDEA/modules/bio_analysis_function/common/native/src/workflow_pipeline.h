/*
 * workflow_pipeline.h —— 工作流专用的"读取 → 计算 → 写出"保序流水线
 *
 * ============================================================================
 * 它和 pipeline.h / paired_pipeline.h 是什么关系
 * ============================================================================
 *
 * 三者的**设计纪律完全相同**（见 pipeline.h 文件头：按批次传递、槽位环、
 * 有界在途），差别只在流水线两端允许做什么：
 *
 *   pipeline.h          单入单出。read 端不做事，写出端固定写一个文件
 *                       （可选再写一个失败输出）。服务"一次变换"的独立算法。
 *   paired_pipeline.h   成对进出。read 端不做事，写出端由算法自己决定。
 *                       服务双端独立算法。
 *   本文件              工作流的完整预处理。read 端要做**有状态、顺序相关**
 *                       的判定（去重、按 index 过滤、过滤前统计），写出端要
 *                       **多路分流**（主输出 / 落单 / 失败 / 合并 / 重叠区）
 *                       并支持分卷。
 *
 * 为什么不为这三条流水线抽一个公共骨架：前两条的契约已经稳定、各自有多个
 * 使用方与常驻测试，为一个新使用方去参数化它们，改动面与风险都不划算。
 * 本文件复用它们的**基础设施**（``pipeline_detail::kBatchRecords``、
 * ``resolve_threads``、``add_stats`` 等），只自己实现槽位环与保序消费。
 * 若将来出现第四条同类流水线，那时才是把骨架提取出来的时机。
 *
 * ============================================================================
 * 三端分工（这是工作流正确性的关键，别随意挪动）
 * ============================================================================
 *
 *     reader（1 线程，按文件顺序）
 *         ├─ 过滤前统计（口径要求：先统计，再判去留）
 *         ├─ 按 index 过滤（命中即整对丢弃，不进任何输出）
 *         └─ 去重判定（依赖全部历史的累积状态，必须保序）
 *              ↓ 批次
 *     worker × N（并行，逐条无状态变换）
 *         └─ 规范化 / UMI / 修剪链 / overlap / 校正 / 合并 / 过滤判定
 *              ↓ 批次
 *     writer（1 线程，按文件顺序）
 *         └─ 过滤后统计 / 各路输出 / 分卷
 *
 * 之所以这么切：**判定顺序会影响结果的步骤只能放在单线程的保序位置**。
 * 去重是典型——它的位图跨 read 累积，"谁先被判为重复"取决于判定顺序，
 * 放进 worker 池就会随线程调度漂移（上游 fastp 正是如此，同一份数据换线程数
 * 报出的重复率会变；本模块不这样）。
 *
 * ``prepare`` 返回 false 的条目不会进入批次，因此也不计入 worker 与 writer
 * 两侧的任何统计。**过滤前统计要在 prepare 里做**，否则口径就与上游对不上。
 */

#ifndef BIO_WORKFLOW_PIPELINE_H
#define BIO_WORKFLOW_PIPELINE_H

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <exception>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

#include "fastq.h"
#include "pipeline.h"

namespace bio {

namespace workflow_pipeline_detail {

/* 一个批次装多少条，与另外两条流水线同口径（理由见 pipeline.h）。 */
constexpr std::size_t kBatchRecords = pipeline_detail::kBatchRecords;

/*
 * 一个槽位：流水线上的一个批次，以及它的状态。
 *
 * 状态迁移只有三条合法路径，每条由持锁的某一方独占完成：
 *
 *     Free ──reader 填入──▶ Filled ──worker 算完──▶ Computed ──writer 写完──▶ Free
 *
 * ``items`` 与 ``outcomes`` 只在"持有该槽"的线程里读写，别的线程只看 ``state``。
 *
 * ``Input`` 是批次的元素类型：单端是 ``FastqRecord``，双端是 ``ReadPair``。
 */
template <typename Input, typename Outcome>
struct Slot {
    std::mutex mutex;
    std::condition_variable cv;

    enum class State { Free, Filled, Computed };

    State state = State::Free;
    std::vector<Input> items;
    std::vector<Outcome> outcomes;
};

/* 收工时统一置位并唤醒所有等待者。 */
template <typename Input, typename Outcome>
void fail_slots(std::vector<Slot<Input, Outcome>>& slots) {
    for (Slot<Input, Outcome>& slot : slots) {
        slot.cv.notify_all();
    }
}

/*
 * 单线程直路：不创建任何线程，读一条、判一条、算一条、消费一条。
 *
 * 它与多线程版必须给出**完全相同**的结果，因此两侧的调用顺序严格对齐：
 * prepare 在 step 之前、step 在 consume 之前，逐条推进。
 */
template <typename Input, typename Outcome, typename ReadOne, typename Prepare,
          typename Step, typename Consume>
void run_serial(ReadOne& read_one,
                int64_t max_records,
                Prepare& prepare,
                Step& step,
                Consume& consume) {
    Input item;
    int64_t count = 0;
    while (true) {
        if (max_records > 0 && count >= max_records) {
            break;
        }
        if (!read_one(item)) {
            break;
        }
        ++count;
        if (!prepare(item)) {
            continue;
        }
        consume(step(item));
    }
}

/*
 * 多线程流水线。线程归属：
 *   - producer：只读输入、只判 prepare、只填槽（解压在这里发生）；
 *   - worker × N：只算、不改输入顺序、不写文件；
 *   - 调用线程：充当 writer，按批次下标严格顺序消费（写文件与压缩在这里发生）。
 *
 * 任一环节抛异常都会立刻置 aborted 并唤醒所有等待者；异常被捕获到 ``error``，
 * 等所有线程 join 之后再原样抛出，因此调用方看到的错误与单线程版完全一样。
 */
template <typename Input, typename Outcome, typename ReadOne, typename Prepare,
          typename Step, typename Consume>
void run_parallel(ReadOne& read_one,
                  int worker_count,
                  int64_t max_records,
                  Prepare& prepare,
                  Step& step,
                  Consume& consume) {
    /* 槽数必须大于 worker 数：否则所有槽都落在"已填/已算完"手里，
     * reader 没有可填的槽，流水线会互相等死。 */
    const std::size_t slot_count = static_cast<std::size_t>(worker_count) + 2;
    const int64_t outstanding_limit =
        static_cast<int64_t>(std::max(4, worker_count * 4));

    std::vector<Slot<Input, Outcome>> slots(slot_count);

    std::mutex state_mutex;
    std::condition_variable work_cv;   /* worker 等"有已填好的批次" */
    std::condition_variable space_cv;  /* producer 等"在途批次降到上限以下" */

    std::deque<int64_t> ready;
    /*
     * 读取端是否已经收工。用原子量而不是普通 bool：写出端要在**不持锁**的情况下
     * 看它来决定"还要不要等下一批"，普通变量的可见性没有保证。
     */
    std::atomic<bool> reader_finished{false};

    std::atomic<int64_t> issued{0};
    std::atomic<int64_t> written{0};
    std::atomic<bool> aborted{false};
    std::exception_ptr error;

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
        fail_slots(slots);
    };

    std::thread producer([&] {
        try {
            int64_t count = 0;
            Input item;
            while (true) {
                std::vector<Input> buffer;
                buffer.reserve(kBatchRecords);
                while (buffer.size() < kBatchRecords) {
                    if (max_records > 0 && count >= max_records) {
                        break;
                    }
                    if (!read_one(item)) {
                        break;
                    }
                    ++count;
                    if (!prepare(item)) {
                        continue;
                    }
                    buffer.push_back(std::move(item));
                }
                if (buffer.empty()) {
                    break;  /* 读完，或被上限拦住 */
                }

                const int64_t index = issued.load();
                {
                    /* 反压：在途批次太多就先等一等，内存占用因此有上界。 */
                    std::unique_lock<std::mutex> lock(state_mutex);
                    space_cv.wait(lock, [&] {
                        return aborted.load() ||
                               issued.load() - written.load() < outstanding_limit;
                    });
                    if (aborted.load()) {
                        return;
                    }
                }

                Slot<Input, Outcome>& slot =
                    slots[static_cast<std::size_t>(index) % slot_count];
                {
                    std::unique_lock<std::mutex> lock(slot.mutex);
                    slot.cv.wait(lock, [&] {
                        return slot.state == Slot<Input, Outcome>::State::Free ||
                               aborted.load();
                    });
                    if (aborted.load()) {
                        return;
                    }
                    slot.items = std::move(buffer);
                    slot.outcomes.clear();
                    slot.state = Slot<Input, Outcome>::State::Filled;
                }
                issued.store(index + 1);

                {
                    std::lock_guard<std::mutex> lock(state_mutex);
                    ready.push_back(index);
                }
                work_cv.notify_one();
            }

            reader_finished.store(true);
            work_cv.notify_all();
            /*
             * 还要唤醒每个槽的等待者。写出端等的是**槽的**条件变量，不是 work_cv；
             * 读取端一个批次都没发出就收工（空输入）时，写出端正卡在 0 号槽上，
             * 只通知 work_cv 它永远醒不过来。
             */
            fail_slots(slots);
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
                        return;  /* 队列空且读完了：收工 */
                    }
                    index = ready.front();
                    ready.pop_front();
                }

                Slot<Input, Outcome>& slot =
                    slots[static_cast<std::size_t>(index) % slot_count];
                slot.outcomes.reserve(slot.items.size());
                for (const Input& item : slot.items) {
                    slot.outcomes.push_back(step(item));
                }

                {
                    std::lock_guard<std::mutex> lock(slot.mutex);
                    slot.state = Slot<Input, Outcome>::State::Computed;
                }
                slot.cv.notify_all();
            }
        } catch (...) {
            fail(std::current_exception());
        }
    };

    std::vector<std::thread> workers;
    workers.reserve(static_cast<std::size_t>(worker_count));
    for (int index = 0; index < worker_count; ++index) {
        workers.emplace_back(worker_body);
    }

    int64_t index = 0;
    /*
     * 写出阶段的异常必须在这里就地收住：此时 producer 与 workers 还在运行，
     * 异常若提前离开本函数，`std::thread` 析构会因为线程仍 joinable 而终止进程。
     *
     * 等待谓词里必须带上"读取端已收工且这一批不存在"这一条：**输入为空**时
     * 读取端一个批次都没发出就收工，写出端卡在 0 号槽上，只有它能让写出端醒着
     * 离开——否则通知到了、谓词看一眼又睡回去，整个流程就停在那里了。
     */
    try {
        while (true) {
            if (reader_finished.load() && index >= issued.load()) {
                break;
            }
            Slot<Input, Outcome>& slot =
                slots[static_cast<std::size_t>(index) % slot_count];
            {
                std::unique_lock<std::mutex> lock(slot.mutex);
                slot.cv.wait(lock, [&] {
                    return slot.state == Slot<Input, Outcome>::State::Computed ||
                           aborted.load() ||
                           (reader_finished.load() && index >= issued.load());
                });
                if (aborted.load()) {
                    break;
                }
                if (slot.state != Slot<Input, Outcome>::State::Computed) {
                    break;  /* 读取端已收工，这一批根本不存在 */
                }
                for (const Outcome& outcome : slot.outcomes) {
                    consume(outcome);
                }
                slot.state = Slot<Input, Outcome>::State::Free;
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
        std::rethrow_exception(error);
    }
}

}  // namespace workflow_pipeline_detail

/*
 * 工作流流水线入口。
 *
 * ``Input`` 必须显式给出（单端 ``FastqRecord``、双端 ``ReadPair``）：它没法从
 * ``ReadOne`` 的签名推导出来，而 ``Outcome`` 由 ``Step`` 的返回类型决定。
 *
 * ``read_one(item)`` 在 reader 端调用：读入下一个元素，成功返回 true；
 * 双端时若两份输入的记录数不一致，应在这里抛 ``FastqFormatError``。
 * ``prepare(item)`` 在同一线程、同一顺序下紧随其后调用，返回 false 表示丢弃。
 * ``step(item)`` 在 worker 线程里调用。
 * ``consume(outcome)`` 在写出线程里**按输入顺序**调用，写文件与累加统计都放这里。
 *
 * ``threads <= 0`` 表示由实现按 ``auto_policy`` 自行决定（理由见 pipeline.h 的
 * AutoThreads）；``threads == 1`` 显式走单线程直路，连线程都不创建。
 *
 * 失败时异常原样抛出；**已写出的半成品由调用方清理**（只有调用方知道输出路径）。
 */
template <typename Input, typename ReadOne, typename Prepare, typename Step,
          typename Consume>
void run_workflow_pipeline(ReadOne read_one,
                           int threads,
                           AutoThreads auto_policy,
                           int64_t max_records,
                           Prepare prepare,
                           Step step,
                           Consume consume) {
    using Outcome = decltype(step(std::declval<const Input&>()));
    const int worker_count = pipeline_detail::resolve_threads(threads, auto_policy);
    if (worker_count <= 1) {
        workflow_pipeline_detail::run_serial<Input, Outcome>(read_one, max_records,
                                                             prepare, step, consume);
        return;
    }
    workflow_pipeline_detail::run_parallel<Input, Outcome>(
        read_one, worker_count, max_records, prepare, step, consume);
}

}  // namespace bio

#endif /* BIO_WORKFLOW_PIPELINE_H */
