/*
 * paired_pipeline.h —— 双端"成对读取 → 计算 → 写出"的三段并行流水线
 *
 * ============================================================================
 * 与单端的 pipeline.h 是什么关系
 * ============================================================================
 *
 * `pipeline.h` 一次只喂一条 read，装不下"必须成对处理"的算法。双端算法（按 overlap
 * 裁接头、合并）要**同步读 R1 与 R2**、每步拿到一对 read、还要保持成对顺序，因此
 * 这里是一条独立的专用流水线。它复用 `pipeline.h` 的三条纪律与基础设施：
 *
 *   1. **按批次传递**：worker 的最小单位是一批 read 对（kBatchRecords 对），
 *      批次下标单调，写出方严格按 0、1、2…… 消费 —— 于是输出顺序永远等于输入顺序，
 *      与线程调度无关；
 *   2. **槽位环**：批次放在固定数量的槽里（槽数 = worker 数 + 2），槽只有
 *      `Free → Filled → Computed → Free` 三种状态，任何时刻只有一个线程持有它；
 *   3. **有界在途**：未消费的批次数有上限，内存占用与输入大小无关。
 *
 * 两条流水线共用 `pipeline_detail::kBatchRecords` 与 `resolve_threads()`，因此
 * 批量大小与线程数的口径不会各自漂移。
 *
 * ============================================================================
 * 用法
 * ============================================================================
 *
 *     bio::run_paired_pipeline(read1_path, read2_path, threads, policy,
 *         [&](const bio::FastqRecord& r1, const bio::FastqRecord& r2) -> MyOutcome {
 *             ...处理一对 read，返回自己的结果类型...
 *         },
 *         [&](const MyOutcome& outcome) {
 *             ...在写出线程里按序消费——写文件、累加统计都放这里...
 *         });
 *
 * 两个回调都是模板参数而不是 `std::function`：它们每对 read 就要调用一次，
 * 走模板才能内联进内循环。
 *
 * **结果类型由算法决定**：流水线不规定 Outcome 长什么样，只负责把它从 worker
 * 搬到写出方。因此"裁接头要写两个文件、合并只写一个"这类差异都留在算法自己的
 * 回调里，公共层不必知道。
 *
 * **半成品清理不在这里做**：只有调用方知道输出路径，失败时删哪些文件由它决定。
 */

#ifndef BIO_PAIRED_PIPELINE_H
#define BIO_PAIRED_PIPELINE_H

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

#include "bio_native.h"
#include "fastq.h"
#include "pipeline.h"

namespace bio {

/* 一对 read（两条都按原始方向保存）。 */
struct ReadPair {
    FastqRecord read1;
    FastqRecord read2;
};

namespace paired_pipeline_detail {

/* 一个槽位：流水线上的一个批次，以及它的处理结果。 */
template <typename Outcome>
struct Slot {
    std::mutex mutex;
    std::condition_variable cv;

    enum class State { Free, Filled, Computed };

    State state = State::Free;
    std::vector<ReadPair> pairs;
    std::vector<Outcome> outcomes;
};

/* 单线程直路：不创建任何线程，逐对读、算、交给调用方消费。 */
template <typename Outcome, typename Step, typename Consume>
void run_serial(const std::string& read1_path,
                const std::string& read2_path,
                Step& step,
                Consume& consume) {
    FastqReader reader1(read1_path);
    FastqReader reader2(read2_path);

    FastqRecord record1;
    FastqRecord record2;
    while (true) {
        const bool has1 = reader1.next(record1);
        const bool has2 = reader2.next(record2);
        if (has1 != has2) {
            throw FastqFormatError("两份配对 FASTQ 的记录数不一致。");
        }
        if (!has1) {
            break;
        }
        consume(step(record1, record2));
    }
}

/*
 * 多线程流水线。线程归属与单端那条一致：
 *   - producer：只读输入、只填槽（解压在这里发生）；
 *   - worker × N：只算，不改动输入顺序，也不写文件；
 *   - 调用线程：充当写出方，按批次下标严格顺序消费（压缩在这里发生）。
 */
template <typename Outcome, typename Step, typename Consume>
void run_parallel(const std::string& read1_path,
                  const std::string& read2_path,
                  int worker_count,
                  Step& step,
                  Consume& consume) {
    FastqReader reader1(read1_path);
    FastqReader reader2(read2_path);

    const std::size_t slot_count = static_cast<std::size_t>(worker_count) + 2;
    const int64_t outstanding_limit =
        static_cast<int64_t>(std::max(4, worker_count * 4));

    std::vector<Slot<Outcome>> slots(slot_count);

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
        for (Slot<Outcome>& slot : slots) {
            slot.cv.notify_all();
        }
    };

    std::thread producer([&] {
        try {
            while (true) {
                std::vector<ReadPair> buffer;
                buffer.reserve(pipeline_detail::kBatchRecords);
                while (buffer.size() < pipeline_detail::kBatchRecords) {
                    FastqRecord record1;
                    FastqRecord record2;
                    const bool has1 = reader1.next(record1);
                    const bool has2 = reader2.next(record2);
                    if (has1 != has2) {
                        /* 一端先读完而另一端还有记录：配对关系已被破坏。 */
                        throw FastqFormatError("两份配对 FASTQ 的记录数不一致。");
                    }
                    if (!has1) {
                        break;
                    }
                    buffer.push_back(ReadPair{std::move(record1), std::move(record2)});
                }
                if (buffer.empty()) {
                    break;
                }

                const int64_t index = issued.load();
                {
                    std::unique_lock<std::mutex> lock(state_mutex);
                    space_cv.wait(lock, [&] {
                        return aborted.load() ||
                               issued.load() - written.load() < outstanding_limit;
                    });
                    if (aborted.load()) {
                        return;
                    }
                }

                Slot<Outcome>& slot =
                    slots[static_cast<std::size_t>(index) % slot_count];
                {
                    std::unique_lock<std::mutex> lock(slot.mutex);
                    slot.cv.wait(lock, [&] {
                        return slot.state == Slot<Outcome>::State::Free ||
                               aborted.load();
                    });
                    if (aborted.load()) {
                        return;
                    }
                    slot.pairs = std::move(buffer);
                    slot.outcomes.clear();
                    slot.state = Slot<Outcome>::State::Filled;
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
            work_cv.notify_all();
            /*
             * 还要唤醒每个槽的等待者。写出端等的是**槽的**条件变量，不是 work_cv；
             * 输入为空时读取端一个批次都没发出就收工，写出端正卡在 0 号槽上，
             * 只通知 work_cv 它永远醒不过来。
             */
            for (Slot<Outcome>& slot : slots) {
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
                        return;  /* 队列空且读完了：收工 */
                    }
                    index = ready.front();
                    ready.pop_front();
                }

                Slot<Outcome>& slot =
                    slots[static_cast<std::size_t>(index) % slot_count];
                slot.outcomes.reserve(slot.pairs.size());
                for (const ReadPair& pair : slot.pairs) {
                    slot.outcomes.push_back(step(pair.read1, pair.read2));
                }

                {
                    std::lock_guard<std::mutex> lock(slot.mutex);
                    slot.state = Slot<Outcome>::State::Computed;
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
            Slot<Outcome>& slot = slots[static_cast<std::size_t>(index) % slot_count];
            {
                std::unique_lock<std::mutex> lock(slot.mutex);
                slot.cv.wait(lock, [&] {
                    return slot.state == Slot<Outcome>::State::Computed ||
                           aborted.load() ||
                           (reader_finished.load() && index >= issued.load());
                });
                if (aborted.load()) {
                    break;
                }
                if (slot.state != Slot<Outcome>::State::Computed) {
                    break;  /* 读取端已收工，这一批根本不存在 */
                }
                for (const Outcome& outcome : slot.outcomes) {
                    consume(outcome);
                }
                slot.state = Slot<Outcome>::State::Free;
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

}  // namespace paired_pipeline_detail

/*
 * 双端成对流水线入口。
 *
 * ``threads <= 0`` 表示由实现按 ``auto_policy`` 自行决定（理由见 pipeline.h 的
 * AutoThreads）；``threads == 1`` 显式走单线程直路，连线程都不创建。
 *
 * ``step(read1, read2)`` 在 worker 线程里调用，返回值类型即本流水线的 Outcome；
 * ``consume(outcome)`` 在写出线程里**按输入顺序**调用，写文件与累加统计都放这里。
 *
 * 失败时异常原样抛出（与单线程走法看到的一致）；**已写出的半成品由调用方清理**。
 */
template <typename Step, typename Consume>
void run_paired_pipeline(const std::string& read1_path,
                         const std::string& read2_path,
                         int threads,
                         AutoThreads auto_policy,
                         Step step,
                         Consume consume) {
    using Outcome = decltype(step(std::declval<const FastqRecord&>(),
                                  std::declval<const FastqRecord&>()));
    const int worker_count = pipeline_detail::resolve_threads(threads, auto_policy);
    if (worker_count <= 1) {
        paired_pipeline_detail::run_serial<Outcome>(read1_path, read2_path, step,
                                                    consume);
        return;
    }
    paired_pipeline_detail::run_parallel<Outcome>(read1_path, read2_path,
                                                  worker_count, step, consume);
}

}  // namespace bio

#endif /* BIO_PAIRED_PIPELINE_H */
