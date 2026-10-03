#include "overrep.h"

#include <algorithm>
#include <stdexcept>

#include "fastq.h"

namespace bio {

namespace {

/* 候选入选阈值（按长度分档，从上往下判）。上游 ``computeOverRepSeq`` 写死。 */
struct CandidateThreshold {
    int32_t minimum_length;
    int64_t count;
};

constexpr CandidateThreshold kCandidateThresholds[] = {
    {100, 5},
    {40, 20},
    {20, 100},
    {10, 500},
};

/* 剔掉"是别人子串、且自己并不比别人多十倍"的候选。
 *
 * 比值用**整数除法**（上游如此）：换成浮点比较，边界附近的取舍会变。 */
std::map<std::string, int64_t> remove_substrings(
    const std::map<std::string, int64_t>& hot) {
    std::map<std::string, int64_t> kept;
    for (const auto& item : hot) {
        const std::string& sequence = item.first;
        const int64_t count = item.second;
        bool is_substring = false;
        for (const auto& other : hot) {
            if (other.first == sequence) {
                continue;
            }
            if (other.first.find(sequence) == std::string::npos) {
                continue;
            }
            if (count / other.second < 10) {
                is_substring = true;
                break;
            }
        }
        if (!is_substring) {
            kept.emplace(sequence, count);
        }
    }
    return kept;
}

}  // namespace

std::vector<int32_t> overrep_steps(int32_t seq_length) {
    return {10, 20, 40, 100, std::min(150, seq_length - 2)};
}

bool overrep_candidate_passes(int32_t length, int64_t count, int32_t seq_length) {
    if (length >= seq_length - 1) {
        return count >= 3;
    }
    for (const CandidateThreshold& threshold : kCandidateThresholds) {
        if (length >= threshold.minimum_length) {
            return count >= threshold.count;
        }
    }
    return false;
}

bool overrep_report_passes(int32_t length, int64_t count, int32_t sampling) {
    const int64_t scaled = static_cast<int64_t>(sampling) * count;
    switch (length) {
        case 10:
            return scaled > 500;
        case 20:
            return scaled > 200;
        case 40:
            return scaled > 100;
        case 100:
            return scaled > 50;
        default:
            return scaled > 20;
    }
}

OverrepResult find_overrepresented_sequences(const std::string& input_path,
                                             const OverrepOptions& options) {
    if (options.sampling < kMinOverrepSampling ||
        options.sampling > kMaxOverrepSampling) {
        throw std::invalid_argument("sampling 必须在 1 到 10000 之间，当前为 " +
                                    std::to_string(options.sampling) + "。");
    }
    if (options.base_limit < 1) {
        throw std::invalid_argument("base_limit 必须为正，当前为 " +
                                    std::to_string(options.base_limit) + "。");
    }
    if (options.seq_length_sample < 1) {
        throw std::invalid_argument("seq_length_sample 必须为正，当前为 " +
                                    std::to_string(options.seq_length_sample) + "。");
    }

    OverrepResult result;
    result.sampling = options.sampling;

    /* ---- 第一步：看前若干条确定"读长" ---- */
    {
        const std::string path(input_path);
        FastqReader reader(path);
        FastqRecord record;
        int32_t seen = 0;
        while (seen < options.seq_length_sample && reader.next(record)) {
            result.seq_length = std::max(
                result.seq_length, static_cast<int32_t>(record.length()));
            ++seen;
        }
    }

    /* ---- 第二步：在开头一段数据上找出候选 ---- */
    std::map<std::string, int64_t> candidates;
    {
        const std::string path(input_path);
        FastqReader reader(path);
        FastqRecord record;
        const std::vector<int32_t> steps = overrep_steps(result.seq_length);
        int64_t bases = 0;
        while (reader.next(record)) {
            bases += static_cast<int64_t>(record.length());
            const std::string& sequence = record.sequence;
            for (const int32_t step : steps) {
                if (step <= 0 ||
                    static_cast<std::size_t>(step) > sequence.size()) {
                    continue;
                }
                const std::size_t limit = sequence.size() - step;
                for (std::size_t start = 0; start < limit; ++start) {
                    ++candidates[sequence.substr(start, static_cast<std::size_t>(step))];
                }
            }
            /* 让总量越过上限的那一条**也是被处理的**——上游同样是处理完再检查。 */
            if (bases >= options.base_limit) {
                break;
            }
        }
    }

    std::map<std::string, int64_t> hot;
    for (const auto& item : candidates) {
        if (overrep_candidate_passes(static_cast<int32_t>(item.first.size()),
                                     item.second, result.seq_length)) {
            hot.emplace(item.first, item.second);
        }
    }
    hot = remove_substrings(hot);

    /* ---- 第三步：在全文件上按采样量化这些候选 ---- */
    std::map<std::string, int64_t> counts;
    std::map<std::string, std::vector<int64_t>> distributions;
    for (const auto& item : hot) {
        counts.emplace(item.first, 0);
        distributions.emplace(
            item.first, std::vector<int64_t>(static_cast<std::size_t>(result.seq_length), 0));
    }

    const std::vector<int32_t> steps = overrep_steps(result.seq_length);
    {
        const std::string path(input_path);
        FastqReader reader(path);
        FastqRecord record;
        int64_t index = 0;
        while (reader.next(record)) {
            ++result.total_reads;
            result.total_bases += static_cast<int64_t>(record.length());

            if (!hot.empty() && index % options.sampling == 0) {
                ++result.sampled_reads;
                const std::string& sequence = record.sequence;
                const std::size_t length = sequence.size();
                for (const int32_t step : steps) {
                    if (step <= 0 || static_cast<std::size_t>(step) > length) {
                        continue;
                    }
                    const std::size_t limit = length - step;
                    std::size_t position = 0;
                    while (position < limit) {
                        const std::string piece =
                            sequence.substr(position, static_cast<std::size_t>(step));
                        auto found = counts.find(piece);
                        if (found != counts.end()) {
                            ++found->second;
                            std::vector<int64_t>& distribution = distributions[piece];
                            /* 命中之后多跳一个片段长度：否则同一段 DNA 会被它在
                             * 不同起点的重叠窗口重复计数。 */
                            const std::size_t stop = std::min(
                                position + static_cast<std::size_t>(step),
                                static_cast<std::size_t>(result.seq_length));
                            for (std::size_t p = position; p < stop; ++p) {
                                ++distribution[p];
                            }
                            position += static_cast<std::size_t>(step);
                        }
                        ++position;
                    }
                }
            }
            ++index;
        }
    }

    /* ---- 组装并按上游的报告阈值过滤 ---- */
    for (const auto& item : hot) {
        const std::string& sequence = item.first;
        const int64_t count = counts[sequence];
        if (!overrep_report_passes(static_cast<int32_t>(sequence.size()), count,
                                   options.sampling)) {
            continue;
        }
        OverrepEntry entry;
        entry.sequence = sequence;
        entry.count = count;
        entry.estimated_count = count * static_cast<int64_t>(options.sampling);
        entry.base_percent =
            result.total_bases == 0
                ? 0.0
                : 100.0 * static_cast<double>(count) *
                      static_cast<double>(sequence.size()) *
                      static_cast<double>(options.sampling) /
                      static_cast<double>(result.total_bases);
        entry.distribution = distributions[sequence];
        result.entries.push_back(std::move(entry));
    }

    /* 出现得多的排前面；并列时短的在前、再并列按字典序。 */
    std::sort(result.entries.begin(), result.entries.end(),
              [](const OverrepEntry& left, const OverrepEntry& right) {
                  if (left.count != right.count) {
                      return left.count > right.count;
                  }
                  if (left.sequence.size() != right.sequence.size()) {
                      return left.sequence.size() < right.sequence.size();
                  }
                  return left.sequence < right.sequence;
              });
    return result;
}

}  // namespace bio
