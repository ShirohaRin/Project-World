#include "dedup.h"

#include <memory>
#include <stdexcept>

namespace bio {

namespace {

/* 素数表的每缓冲区长度。上游写法是 ``1 << 9``，即 512。 */
constexpr std::size_t kPrimeArrayLen = 1 << 9;

/* 缓冲区个数的上限：档位 6 是 8 个，位置向量因此最多 8 项。 */
constexpr std::size_t kMaxBufferCount = 8;

/* 试除法判素。范围小（最大到百万级），不需要更聪明的方法。
 *
 * 判据写成 ``divisor * divisor <= number``，与上游的 ``i <= sqrt(number)`` 等价
 * （上游用 double 开方，这里用整数乘法，结果一致且没有浮点参与）。 */
bool is_prime(uint64_t number) {
    for (uint64_t divisor = 2; divisor * divisor <= number; ++divisor) {
        if (number % divisor == 0) {
            return false;
        }
    }
    return true;
}

/* 碱基 → 哈希权重表。这几个数是素数且互不相同——上游没解释来源，
 * 但显然是为了让四种碱基在位运算下尽量分散。它们是算法的一部分，
 * 不能"顺手换成更好看的数"：换了以后同一份数据的重复率就会变。 */
constexpr std::array<uint64_t, 256> make_hash_value_table() {
    std::array<uint64_t, 256> table{};
    for (std::size_t index = 0; index < table.size(); ++index) {
        table[index] = 13;
    }
    table[static_cast<std::size_t>('A')] = 7;
    table[static_cast<std::size_t>('C')] = 74;
    table[static_cast<std::size_t>('G')] = 31;
    table[static_cast<std::size_t>('T')] = 222;
    return table;
}

}  // namespace

const std::array<uint64_t, 256> kDedupHashValue = make_hash_value_table();

const DedupMemoryPlan kDedupMemoryPlan[6] = {
    {1, 2, static_cast<std::size_t>(1) << 29},
    {2, 2, static_cast<std::size_t>(1) << 30},
    {3, 4, static_cast<std::size_t>(1) << 30},
    {4, 4, static_cast<std::size_t>(1) << 31},
    {5, 4, static_cast<std::size_t>(1) << 32},
    {6, 8, static_cast<std::size_t>(1) << 32},
};

DedupMemoryPlan resolve_dedup_plan(const DedupOptions& options) {
    if (options.accuracy_level < 1 || options.accuracy_level > 6) {
        throw std::invalid_argument("accuracy_level 必须在 1 到 6 之间，当前为 " +
                                    std::to_string(options.accuracy_level) + "。");
    }
    DedupMemoryPlan plan = kDedupMemoryPlan[options.accuracy_level - 1];
    if (options.buffer_bytes > 0) {
        plan.buffer_bytes = static_cast<std::size_t>(options.buffer_bytes);
    }
    return plan;
}

std::vector<uint64_t> make_dedup_prime_table(std::size_t count) {
    std::vector<uint64_t> table;
    table.reserve(count);
    uint64_t number = 10000;
    while (table.size() < count) {
        ++number;
        if (is_prime(number)) {
            table.push_back(number);
            number += 10000;
        }
    }
    return table;
}

DuplicateDetector::DuplicateDetector(const DedupOptions& options)
    : plan_(resolve_dedup_plan(options)) {
    offset_mask_ = kPrimeArrayLen * plan_.buffer_count - 1;
    buffer_bits_ = plan_.buffer_bytes * 8;
    primes_ = make_dedup_prime_table(plan_.buffer_count * kPrimeArrayLen);
    bitmap_.assign(plan_.buffer_count * plan_.buffer_bytes, 0);
}

void DuplicateDetector::accumulate(const char* data, std::size_t length,
                                   uint64_t* positions, std::size_t pos_offset) const {
    for (std::size_t position = 0; position < length; ++position) {
        const uint64_t base =
            kDedupHashValue[static_cast<unsigned char>(data[position])];
        const uint64_t shifted = position + pos_offset;
        /* 位置与碱基值耦合（乘的是 hash + 位置，不是只有 hash）：
         * 只按碱基内容哈希的话，AAC 与 ACA 会撞在一起。 */
        const uint64_t weighted = base + shifted;
        for (std::size_t slot = 0; slot < plan_.buffer_count; ++slot) {
            const std::size_t index = (shifted * plan_.buffer_count + slot) & offset_mask_;
            /* uint64 溢出即回绕，与 Python 侧显式取模等价（同一套模运算）。 */
            positions[slot] += primes_[index] * weighted;
        }
    }
}

bool DuplicateDetector::apply_bitmap(const uint64_t* positions) {
    bool is_duplicate = true;
    for (std::size_t slot = 0; slot < plan_.buffer_count; ++slot) {
        const uint64_t bit = positions[slot] % buffer_bits_;
        const std::size_t index =
            slot * plan_.buffer_bytes + static_cast<std::size_t>(bit >> 3);
        const unsigned char flag = static_cast<unsigned char>(1u << (bit & 7));
        const unsigned char previous = bitmap_[index];
        bitmap_[index] = static_cast<unsigned char>(previous | flag);
        /* 注意不能短路：哪怕已经发现是新的，剩下的缓冲区也照样要置位，
         * 否则后面的 read 就看不到这条序列留下的痕迹了。 */
        is_duplicate = is_duplicate && ((previous & flag) != 0);
    }
    return is_duplicate;
}

bool DuplicateDetector::check_read(const std::string& sequence) {
    std::array<uint64_t, kMaxBufferCount> positions{};
    accumulate(sequence.data(), sequence.size(), positions.data(), 0);
    const bool is_duplicate = apply_bitmap(positions.data());
    ++total_reads_;
    if (is_duplicate) {
        ++duplicate_reads_;
    }
    return is_duplicate;
}

bool DuplicateDetector::check_pair(const std::string& read1, const std::string& read2) {
    std::array<uint64_t, kMaxBufferCount> positions{};
    accumulate(read1.data(), read1.size(), positions.data(), 0);
    accumulate(read2.data(), read2.size(), positions.data(), read1.size());
    const bool is_duplicate = apply_bitmap(positions.data());
    ++total_reads_;
    if (is_duplicate) {
        ++duplicate_reads_;
    }
    return is_duplicate;
}

double DuplicateDetector::duplicate_rate() const {
    if (total_reads_ == 0) {
        return 0.0;
    }
    return static_cast<double>(duplicate_reads_) / static_cast<double>(total_reads_);
}

DedupStats deduplicate_single(const std::string& input_path,
                              const std::string& output_path,
                              bool compress,
                              const DedupOptions& options) {
    FastqReader reader(input_path);
    DuplicateDetector detector(options);
    const bool writing = !output_path.empty();

    DedupStats result{};
    result.accuracy_level = detector.accuracy_level();
    bio_stats_t& stats = result.stats;

    try {
        /* writer 声明在 try 内部：抛异常时它先随栈展开析构（关闭文件），
         * 下面的 catch 再去删半成品才删得掉（Windows 上打开中的文件删不掉）。 */
        std::unique_ptr<FastqWriter> writer;
        if (writing) {
            writer = std::make_unique<FastqWriter>(output_path, compress);
        }

        FastqRecord record;
        while (reader.next(record)) {
            ++stats.total_reads;
            stats.bases_before += static_cast<int64_t>(record.length());

            if (detector.check_read(record.sequence)) {
                /* 只评估时"没丢任何东西"，因此 dropped 保持 0；去重时才记。 */
                if (writing) {
                    ++stats.dropped_reads;
                }
                continue;
            }
            stats.bases_after += static_cast<int64_t>(record.length());
            if (writer != nullptr) {
                writer->write(record);
            }
        }
        if (writer != nullptr) {
            writer->close();
        }
    } catch (...) {
        if (writing) {
            remove_file_if_exists(output_path);
        }
        throw;
    }

    stats.kept_reads = stats.total_reads - stats.dropped_reads;
    result.duplicate_reads = detector.duplicate_reads();
    return result;
}

DedupStats deduplicate_paired(const std::string& input1_path,
                              const std::string& output1_path,
                              const std::string& input2_path,
                              const std::string& output2_path,
                              bool compress,
                              const DedupOptions& options) {
    FastqReader reader1(input1_path);
    FastqReader reader2(input2_path);
    DuplicateDetector detector(options);
    const bool writing = !output1_path.empty();

    DedupStats result{};
    result.accuracy_level = detector.accuracy_level();
    bio_stats_t& stats = result.stats;

    try {
        std::unique_ptr<FastqWriter> writer1;
        std::unique_ptr<FastqWriter> writer2;
        if (writing) {
            writer1 = std::make_unique<FastqWriter>(output1_path, compress);
            writer2 = std::make_unique<FastqWriter>(output2_path, compress);
        }

        FastqRecord record1;
        FastqRecord record2;
        while (true) {
            const bool has_first = reader1.next(record1);
            const bool has_second = reader2.next(record2);
            if (!has_first && !has_second) {
                break;
            }
            if (has_first != has_second) {
                throw FastqFormatError("两份配对 FASTQ 的记录数不一致。");
            }

            /* 统计单位是 **read 对**：一对被一起判重、一起丢弃。 */
            ++stats.total_reads;
            stats.bases_before +=
                static_cast<int64_t>(record1.length() + record2.length());

            if (detector.check_pair(record1.sequence, record2.sequence)) {
                if (writing) {
                    ++stats.dropped_reads;
                }
                continue;
            }
            stats.bases_after +=
                static_cast<int64_t>(record1.length() + record2.length());
            if (writer1 != nullptr) {
                writer1->write(record1);
                writer2->write(record2);
            }
        }
        if (writer1 != nullptr) {
            writer1->close();
            writer2->close();
        }
    } catch (...) {
        if (writing) {
            remove_file_if_exists(output1_path);
            remove_file_if_exists(output2_path);
        }
        throw;
    }

    stats.kept_reads = stats.total_reads - stats.dropped_reads;
    result.duplicate_reads = detector.duplicate_reads();
    return result;
}

}  // namespace bio
