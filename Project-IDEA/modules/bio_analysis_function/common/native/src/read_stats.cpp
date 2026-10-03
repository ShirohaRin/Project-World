#include "read_stats.h"

#include <array>

namespace bio {

namespace {

/* 单碱基 → 2 bit 编码的表。255 是"不是合法碱基"的哨兵。 */
constexpr std::array<unsigned char, 256> make_base_encoding() {
    std::array<unsigned char, 256> table{};
    for (std::size_t code = 0; code < table.size(); ++code) {
        table[code] = 255;
    }
    table[static_cast<std::size_t>('A')] = 0;
    table[static_cast<std::size_t>('T')] = 1;
    table[static_cast<std::size_t>('C')] = 2;
    table[static_cast<std::size_t>('G')] = 3;
    return table;
}

constexpr std::array<unsigned char, 256> kBaseEncoding = make_base_encoding();

/* 曲线里按这个顺序排列的碱基（与上游 ``alphabets[5]`` 一致）。 */
constexpr std::array<char, 5> kCurveBases = {'A', 'T', 'C', 'G', 'N'};

/* Phred+33 的偏移。 */
constexpr int kPhredOffset = 33;

/* k-mer 的位掩码：保留低 10 位（5 个碱基 × 2 bit）。 */
constexpr int kKmerMask = 0x3FC;

}  // namespace

int stat_base_code(unsigned char base) {
    const unsigned char value = kBaseEncoding[base];
    return value == 255 ? -1 : static_cast<int>(value);
}

void ReadStats::ensure_capacity(std::size_t length) {
    if (length <= capacity_) {
        return;
    }
    /* 与上游一致：一次多给一截，避免长读长数据里反复扩容。 */
    const std::size_t grown = length + 100 > length * 3 / 2 ? length + 100 : length * 3 / 2;
    for (std::size_t bucket = 0; bucket < kStatBaseBuckets; ++bucket) {
        q20_bases_[bucket].resize(grown, 0);
        q30_bases_[bucket].resize(grown, 0);
        base_contents_[bucket].resize(grown, 0);
        base_quality_[bucket].resize(grown, 0);
    }
    total_base_.resize(grown, 0);
    total_quality_.resize(grown, 0);
    capacity_ = grown;
}

void ReadStats::add(std::string_view sequence, std::string_view quality) {
    const std::size_t length = sequence.size();
    ensure_capacity(length);

    const char* bases = sequence.data();
    const char* qualities = quality.data();

    int kmer = 0;
    bool need_full_compute = true;
    for (std::size_t index = 0; index < length; ++index) {
        const unsigned char base = static_cast<unsigned char>(bases[index]);
        const unsigned char quality_code = static_cast<unsigned char>(qualities[index]);
        /* = bucket_of(base)：低 3 位。内联以免在内层循环里付一次函数调用。 */
        const std::size_t bucket = static_cast<std::size_t>(base & 0x07);

        ++quality_histogram_[quality_code];

        if (quality_code >= kStatQ30Code) {
            ++q30_bases_[bucket][index];
            ++q20_bases_[bucket][index];
        } else if (quality_code >= kStatQ20Code) {
            ++q20_bases_[bucket][index];
        }

        ++base_contents_[bucket][index];
        base_quality_[bucket][index] += quality_code - kPhredOffset;
        ++total_base_[index];
        total_quality_[index] += quality_code - kPhredOffset;

        /* 下面这段 k-mer 增量更新是整份代码里最绕的一处，逐行照抄上游：
         * 先看是不是 N，再看够不够 5 个碱基，最后决定"接着上一次的结果平移
         * 一格"还是"从头重算 5 个"。 */
        if (base == 'N') {
            need_full_compute = true;
            continue;
        }
        if (index < 4) {
            continue;
        }

        if (!need_full_compute) {
            const int value = stat_base_code(base);
            if (value < 0) {
                need_full_compute = true;
                continue;
            }
            kmer = ((kmer << 2) & kKmerMask) | value;
            ++kmer_counts_[static_cast<std::size_t>(kmer)];
        } else {
            bool valid = true;
            kmer = 0;
            for (std::size_t offset = 0; offset < 5; ++offset) {
                const int value = stat_base_code(
                    static_cast<unsigned char>(bases[index - 4 + offset]));
                if (value < 0) {
                    valid = false;
                    break;
                }
                kmer = ((kmer << 2) & kKmerMask) | value;
            }
            if (!valid) {
                need_full_compute = true;
                continue;
            }
            ++kmer_counts_[static_cast<std::size_t>(kmer)];
            need_full_compute = false;
        }
    }

    ++total_reads_;
    length_sum_ += static_cast<int64_t>(length);
    if (length_counts_.size() <= length) {
        length_counts_.resize(length + 1, 0);
    }
    ++length_counts_[length];
    /* 新数据进来了，上一次的汇总结果作废——否则"扫描多份文件"时会拿着
     * 第一份的曲线去回答第二份的问题。 */
    summarized_ = false;
}

void ReadStats::summarize() {
    if (summarized_) {
        return;
    }

    /* cycle 数 = 第一个"所有 read 都结束"的位置。 */
    cycles_ = capacity_;
    for (std::size_t cycle = 0; cycle < capacity_; ++cycle) {
        if (total_base_[cycle] == 0) {
            cycles_ = cycle;
            break;
        }
    }

    total_bases_ = 0;
    for (std::size_t cycle = 0; cycle < cycles_; ++cycle) {
        total_bases_ += total_base_[cycle];
    }

    std::array<int64_t, kStatBaseBuckets> q20_per_base{};
    std::array<int64_t, kStatBaseBuckets> q30_per_base{};
    std::array<int64_t, kStatBaseBuckets> contents_per_base{};
    for (std::size_t bucket = 0; bucket < kStatBaseBuckets; ++bucket) {
        for (std::size_t cycle = 0; cycle < cycles_; ++cycle) {
            q20_per_base[bucket] += q20_bases_[bucket][cycle];
            q30_per_base[bucket] += q30_bases_[bucket][cycle];
            contents_per_base[bucket] += base_contents_[bucket][cycle];
        }
    }
    for (std::size_t bucket = 0; bucket < kStatBaseBuckets; ++bucket) {
        q20_total_ += q20_per_base[bucket];
        q30_total_ += q30_per_base[bucket];
    }

    /* Q40：上游直接从字符码直方图里把 Q40~Q93 加起来。 */
    q40_total_ = 0;
    for (int phred = kStatQ40Phred; phred <= kStatMaxPhred; ++phred) {
        q40_total_ += quality_histogram_[static_cast<std::size_t>(phred + kPhredOffset)];
    }

    /* 整体质量均值曲线。 */
    std::vector<double>& mean_curve = curves_[static_cast<std::size_t>(StatCurve::QualityMean)];
    mean_curve.assign(cycles_, 0.0);
    for (std::size_t cycle = 0; cycle < cycles_; ++cycle) {
        mean_curve[cycle] = total_base_[cycle] == 0
                                ? 0.0
                                : static_cast<double>(total_quality_[cycle]) /
                                      static_cast<double>(total_base_[cycle]);
    }

    for (std::size_t index = 0; index < kCurveBases.size(); ++index) {
        const char base = kCurveBases[index];
        const std::size_t bucket = static_cast<std::size_t>(base & 0x07);
        std::vector<double>& quality_curve =
            curves_[static_cast<std::size_t>(StatCurve::QualityA) + index];
        std::vector<double>& content_curve =
            curves_[static_cast<std::size_t>(StatCurve::ContentA) + index];
        quality_curve.assign(cycles_, 0.0);
        content_curve.assign(cycles_, 0.0);
        for (std::size_t cycle = 0; cycle < cycles_; ++cycle) {
            const int64_t count = base_contents_[bucket][cycle];
            /* 该位置没有这个碱基时（如 N 很少见），取整体均值兜底，
             * 而不是记 0——否则曲线会在没有数据的位置掉到底。 */
            quality_curve[cycle] =
                count == 0
                    ? mean_curve[cycle]
                    : static_cast<double>(base_quality_[bucket][cycle]) /
                          static_cast<double>(count);
            content_curve[cycle] =
                total_base_[cycle] == 0
                    ? 0.0
                    : static_cast<double>(count) / static_cast<double>(total_base_[cycle]);
        }
    }

    const std::size_t g_bucket = static_cast<std::size_t>('G' & 0x07);
    const std::size_t c_bucket = static_cast<std::size_t>('C' & 0x07);
    std::vector<double>& gc_curve = curves_[static_cast<std::size_t>(StatCurve::ContentGC)];
    gc_curve.assign(cycles_, 0.0);
    for (std::size_t cycle = 0; cycle < cycles_; ++cycle) {
        gc_curve[cycle] =
            total_base_[cycle] == 0
                ? 0.0
                : static_cast<double>(base_contents_[g_bucket][cycle] +
                                      base_contents_[c_bucket][cycle]) /
                      static_cast<double>(total_base_[cycle]);
    }

    gc_bases_ = contents_per_base[g_bucket] + contents_per_base[c_bucket];
    summarized_ = true;
}

int32_t ReadStats::mean_length() const {
    if (total_reads_ == 0) {
        return 0;
    }
    return static_cast<int32_t>(length_sum_ / total_reads_);
}

const double* ReadStats::curve(StatCurve kind) const {
    const std::size_t index = static_cast<std::size_t>(kind);
    if (index >= curves_.size()) {
        return nullptr;
    }
    return curves_[index].data();
}

}  // namespace bio
