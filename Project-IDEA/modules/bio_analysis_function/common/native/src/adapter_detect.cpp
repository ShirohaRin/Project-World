#include "adapter_detect.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <utility>

#include "fastq.h"
#include "nucleotide_tree.h"

namespace bio {

namespace {

/* ---------------------------------------------------------------------------
 * 上游常量（值一个不改，名字改成可读写法）
 * ------------------------------------------------------------------------ */

constexpr int32_t kSeedScanStart = 20;          /* 从第 20 个碱基起扫 */
constexpr int32_t kMaxSearchLength = 500;       /* 建树时扫描位置的上限 */
constexpr int32_t kTopSeeds = 10;               /* 候选种子个数 */
constexpr int64_t kFoldThreshold = 20;          /* 富集倍数下限 */
constexpr int64_t kMinSeedCount = 10;           /* 种子出现次数下限 */
constexpr int32_t kMinSeedDifferences = 3;      /* 种子自身的复杂度下限 */

constexpr int32_t kKnownMatchMinLength = 8;     /* 最短匹配长度 */
constexpr int32_t kKnownMismatchPerBases = 16;  /* 每 16 个碱基允许 1 个错配 */
constexpr std::size_t kKnownMinReturnLength = 8;/* 返回长度要 > 它 */
constexpr int64_t kKnownMaxReads = 100000;      /* 第一段最多看多少条 read */
constexpr int64_t kKnownMaxBases = 100000LL * 1000;
constexpr int64_t kKnownMaxHit = 1000;          /* 命中这么多就整段收工 */
constexpr int64_t kKnownPruneAfter = 20;        /* 最大命中数超过它之后开始剪枝 */
constexpr int64_t kKnownPruneRatio = 10;        /* 剪枝条件：命中数 < 最大值/10 */

/* 碱基 → 2 bit：A=0、T=1、C=2、G=3（与上游 int2seq 的 bases[4] 同序）。 */
const std::array<int8_t, 256>& code_table() {
    static const std::array<int8_t, 256> table = [] {
        std::array<int8_t, 256> codes{};
        codes.fill(-1);
        codes[static_cast<unsigned char>('A')] = 0;
        codes[static_cast<unsigned char>('T')] = 1;
        codes[static_cast<unsigned char>('C')] = 2;
        codes[static_cast<unsigned char>('G')] = 3;
        return codes;
    }();
    return table;
}

/*
 * 围绕种子延伸出接头，返回 ``(接头, reached_leaf)``。
 *
 * ``reached_leaf`` 的语义照上游原样保留：它是一个**复用**的标记，前向树写一次、
 * 后向树再写一次，而 ``dominant_path`` 只会把它置 false、从不置回 true——
 * 因此最终含义是"**两棵树都没有遇到非占优分支**"。
 * 拼出来的接头为空串表示这个种子不可用（上层继续试下一个种子）。
 */
std::pair<std::string, bool> adapter_with_seed(int32_t seed,
                                               const std::vector<std::string>& reads,
                                               const std::vector<std::string>& adapters,
                                               const AdapterDetectOptions& options) {
    const int32_t key_length = options.key_length;
    const int32_t shift_tail = options.shift_tail;
    const std::string seed_sequence = sequence_of_key(seed, key_length);

    NucleotideTree forward_tree;
    NucleotideTree backward_tree;

    for (const std::string& sequence : reads) {
        const int64_t length = static_cast<int64_t>(sequence.size());
        const int64_t last_position =
            std::min(length - key_length - shift_tail,
                     static_cast<int64_t>(kMaxSearchLength) - 1);
        int32_t last_key = -1;
        for (int64_t position = kSeedScanStart; position <= last_position; ++position) {
            last_key = key_at(sequence, position, key_length, last_key);
            if (last_key != seed) {
                continue;
            }

            /* 前向：种子之后直到（去掉末尾 shift_tail 个碱基）的那一段 */
            const int64_t suffix_start = position + key_length;
            const int64_t suffix_end = length - shift_tail;
            if (suffix_end > suffix_start) {
                forward_tree.add(std::string_view(sequence).substr(
                    static_cast<std::size_t>(suffix_start),
                    static_cast<std::size_t>(suffix_end - suffix_start)));
            }

            /* 后向：种子之前那一段**反过来**挂树（树往缺口左边生长） */
            std::string reversed_prefix(
                sequence.begin(),
                sequence.begin() + static_cast<std::ptrdiff_t>(position));
            std::reverse(reversed_prefix.begin(), reversed_prefix.end());
            backward_tree.add(reversed_prefix);
        }
    }

    bool reached_leaf = true;
    const std::string forward_path = forward_tree.dominant_path(reached_leaf);
    /* 注意：这里**不重置** reached_leaf——上游也没有重置，语义见函数头。 */
    const std::string backward_path = backward_tree.dominant_path(reached_leaf);

    std::string adapter;
    adapter.reserve(backward_path.size() + seed_sequence.size() + forward_path.size());
    adapter.append(backward_path.rbegin(), backward_path.rend());
    adapter.append(seed_sequence);
    adapter.append(forward_path);
    if (static_cast<int32_t>(adapter.size()) > options.max_adapter_length) {
        adapter.resize(static_cast<std::size_t>(options.max_adapter_length));
    }

    const std::string matched = match_known_adapter(adapter, adapters);
    if (!matched.empty()) {
        return {matched, reached_leaf};
    }
    if (reached_leaf) {
        return {adapter, true};
    }
    return {std::string(), false};
}

}  // namespace

/* ------------------------------------------------------------------------- */
/* 工具                                                                       */
/* ------------------------------------------------------------------------- */

int32_t base_code(char base) {
    return code_table()[static_cast<unsigned char>(base)];
}

int32_t key_at(std::string_view sequence,
               int64_t position,
               int32_t key_length,
               int32_t last_key) {
    if (last_key >= 0) {
        const int32_t mask = (1 << (key_length * 2)) - 1;
        const int32_t code =
            base_code(sequence[static_cast<std::size_t>(position + key_length - 1)]);
        if (code < 0) {
            return -1;
        }
        return ((last_key << 2) & mask) + code;
    }

    int32_t key = 0;
    for (int64_t index = position; index < position + key_length; ++index) {
        const int32_t code = base_code(sequence[static_cast<std::size_t>(index)]);
        if (code < 0) {
            return -1;
        }
        key = (key << 2) + code;
    }
    return key;
}

std::string sequence_of_key(int32_t key, int32_t key_length) {
    static constexpr char kBases[4] = {'A', 'T', 'C', 'G'};
    std::string sequence(static_cast<std::size_t>(key_length), 'N');
    int32_t done = 0;
    while (done < key_length) {
        sequence[static_cast<std::size_t>(key_length - done - 1)] = kBases[key & 0x03];
        key >>= 2;
        ++done;
    }
    return sequence;
}

std::string match_known_adapter(std::string_view sequence,
                                const std::vector<std::string>& adapters) {
    for (const std::string& adapter : adapters) {
        if (sequence.size() < adapter.size()) {
            continue;
        }
        if (sequence.compare(0, adapter.size(), adapter) == 0) {
            return adapter;
        }
    }
    return std::string();
}

/* ------------------------------------------------------------------------- */
/* 采样                                                                       */
/* ------------------------------------------------------------------------- */

std::vector<std::string> sample_sequences(const std::string& fastq_path,
                                          const AdapterDetectOptions& options) {
    FastqReader reader(fastq_path);  /* 文件不存在会抛 InputFileError */

    std::vector<std::string> sequences;
    /* 预留容量但设上限：采样规模本来就有界，不必按用户给的上限一次性要内存。 */
    sequences.reserve(static_cast<std::size_t>(std::min<int64_t>(options.max_reads, 1 << 16)));

    int64_t bases = 0;
    FastqRecord record;
    while (reader.next(record)) {
        bases += static_cast<int64_t>(record.length());
        sequences.push_back(std::move(record.sequence));
        if (static_cast<int64_t>(sequences.size()) >= options.max_reads ||
            bases >= options.max_bases) {
            break;
        }
    }
    return sequences;
}

/* ------------------------------------------------------------------------- */
/* 第一段：已知接头表                                                          */
/* ------------------------------------------------------------------------- */

std::string check_known_adapters(const std::vector<std::string>& reads,
                                 const std::vector<std::string>& adapters,
                                 const AdapterDetectOptions& options,
                                 int64_t& checked_reads,
                                 int64_t& hits) {
    (void)options;  /* 这一段的阈值都是上游写死的常量 */
    checked_reads = 0;
    hits = 0;
    if (adapters.empty()) {
        return std::string();
    }

    /*
     * 上游用 std::map<string,string> 存这张表，遍历顺序天然是字典序；
     * "多个接头命中数并列时返回哪一个"依赖这个顺序。这里排一份本地副本，
     * 好让结果与调用方传入的顺序无关（Python 侧同样做了排序）。
     */
    std::vector<std::string> sorted(adapters);
    std::sort(sorted.begin(), sorted.end());

    std::vector<int64_t> possible_counts(sorted.size(), 0);
    std::vector<int64_t> mismatches(sorted.size(), 0);
    int64_t current_max = 0;
    int64_t checked_bases = 0;

    for (const std::string& sequence : reads) {
        const int64_t read_length = static_cast<int64_t>(sequence.size());
        /* 与上游一致：先计数再判断上限，因此"踩线的那一条"会计入 checked_reads。 */
        ++checked_reads;
        checked_bases += read_length;
        if (checked_reads > kKnownMaxReads || checked_bases > kKnownMaxBases) {
            break;
        }
        if (current_max > kKnownMaxHit) {
            break;
        }

        for (std::size_t index = 0; index < sorted.size(); ++index) {
            const std::string& adapter = sorted[index];
            const int64_t adapter_length = static_cast<int64_t>(adapter.size());
            if (adapter_length >= read_length) {
                continue;
            }
            if (current_max > kKnownPruneAfter &&
                possible_counts[index] < current_max / kKnownPruneRatio) {
                continue;
            }

            for (int64_t position = 0; position < read_length - kKnownMatchMinLength;
                 ++position) {
                const int64_t compare_length =
                    std::min(read_length - position, adapter_length);
                const int64_t allowed = compare_length / kKnownMismatchPerBases;
                int64_t mismatch = 0;
                bool matched = true;
                for (int64_t offset = 0; offset < compare_length; ++offset) {
                    if (adapter[static_cast<std::size_t>(offset)] !=
                        sequence[static_cast<std::size_t>(position + offset)]) {
                        ++mismatch;
                        if (mismatch > allowed) {
                            matched = false;
                            break;
                        }
                    }
                }
                if (matched) {
                    ++possible_counts[index];
                    if (current_max < possible_counts[index]) {
                        current_max = possible_counts[index];
                    }
                    mismatches[index] += mismatch;
                    break;
                }
            }
        }
    }

    std::size_t best_index = sorted.size();
    int64_t max_count = 0;
    for (std::size_t index = 0; index < possible_counts.size(); ++index) {
        if (possible_counts[index] > max_count) {
            max_count = possible_counts[index];
            best_index = index;
        }
    }
    if (best_index >= sorted.size()) {
        return std::string();
    }

    /* 两处除法都是整数除法（上游如此）。 */
    if (max_count > checked_reads / 50 ||
        (max_count > checked_reads / 200 && mismatches[best_index] < checked_reads)) {
        hits = max_count;
        return sorted[best_index];
    }
    return std::string();
}

/* ------------------------------------------------------------------------- */
/* 第二段：k-mer 富集 + 前缀树延伸                                             */
/* ------------------------------------------------------------------------- */

AdapterDetection detect_from_kmers(const std::vector<std::string>& reads,
                                   const std::vector<std::string>& adapters,
                                   const AdapterDetectOptions& options) {
    AdapterDetection result;
    result.sampled_reads = static_cast<int64_t>(reads.size());
    for (const std::string& sequence : reads) {
        result.sampled_bases += static_cast<int64_t>(sequence.size());
    }

    const int32_t key_length = options.key_length;
    const int32_t size = 1 << (key_length * 2);
    std::vector<uint32_t> counts(static_cast<std::size_t>(size), 0);

    /* ---- 1. 数 k-mer ---- */
    for (const std::string& sequence : reads) {
        const int64_t length = static_cast<int64_t>(sequence.size());
        const int64_t last_position = length - key_length - options.shift_tail;
        int32_t last_key = -1;
        for (int64_t position = kSeedScanStart; position <= last_position; ++position) {
            last_key = key_at(sequence, position, key_length, last_key);
            if (last_key >= 0) {
                ++counts[static_cast<std::size_t>(last_key)];
            }
        }
    }
    counts[0] = 0;  /* 甩掉 AAAAAAAAAA */

    /* ---- 2. 过滤噪声 + 取计数最高的 10 个 ---- */
    std::array<int32_t, kTopSeeds> top_keys{};
    top_keys.fill(0);
    int64_t total = 0;
    for (int32_t key = 0; key < size; ++key) {
        int32_t atcg[4] = {0, 0, 0, 0};
        for (int32_t offset = 0; offset < key_length; ++offset) {
            ++atcg[(key >> (offset * 2)) & 0x03];
        }
        bool low_complexity = false;
        for (int32_t base = 0; base < 4; ++base) {
            if (atcg[base] >= key_length - 4) {
                low_complexity = true;
                break;
            }
        }
        if (low_complexity) {
            continue;
        }
        if (atcg[2] + atcg[3] >= key_length - 2) {
            continue;
        }
        /* 以 GGGG 开头：上游写成 k >> 12（写死 k=10），这里写成 k >> (k-4)*2 */
        if (key >> ((key_length - 4) * 2) == 0xFF) {
            continue;
        }

        const uint32_t value = counts[static_cast<std::size_t>(key)];
        total += value;
        for (int32_t slot = kTopSeeds - 1; slot >= 0; --slot) {
            if (value < counts[static_cast<std::size_t>(top_keys[slot])]) {
                if (slot < kTopSeeds - 1) {
                    for (int32_t move = kTopSeeds - 1; move > slot + 1; --move) {
                        top_keys[move] = top_keys[move - 1];
                    }
                    top_keys[slot + 1] = key;
                }
                break;
            }
            if (slot == 0) {
                for (int32_t move = kTopSeeds - 1; move > 0; --move) {
                    top_keys[move] = top_keys[move - 1];
                }
                top_keys[0] = key;
            }
        }
    }

    /* ---- 3. 逐个试种子 ---- */
    for (int32_t slot = 0; slot < kTopSeeds; ++slot) {
        const int32_t key = top_keys[slot];
        if (key == 0) {
            continue;
        }
        const int64_t count = counts[static_cast<std::size_t>(key)];
        /* 上游是 break：候选按计数降序试，一旦不达标，后面的只会更差。 */
        if (count < kMinSeedCount ||
            count * size < total * kFoldThreshold) {
            break;
        }

        const std::string seed_sequence = sequence_of_key(key, key_length);
        int32_t differences = 0;
        for (std::size_t index = 0; index + 1 < seed_sequence.size(); ++index) {
            if (seed_sequence[index] != seed_sequence[index + 1]) {
                ++differences;
            }
        }
        if (differences < kMinSeedDifferences) {
            continue;
        }

        auto [adapter, reached_leaf] = adapter_with_seed(key, reads, adapters, options);
        (void)reached_leaf;
        if (adapter.empty()) {
            continue;
        }

        result.detected = true;
        result.adapter = adapter;
        result.source = kAdapterSourceKmer;
        result.seed_sequence = seed_sequence;
        result.seed_count = count;
        result.seed_fold = total > 0
                               ? static_cast<double>(count) * static_cast<double>(size) /
                                     static_cast<double>(total)
                               : 0.0;
        return result;
    }

    result.reason = "已知接头表未命中，且没有找到显著富集的 k-mer 种子";
    return result;
}

/* ------------------------------------------------------------------------- */
/* 入口                                                                       */
/* ------------------------------------------------------------------------- */

AdapterDetection detect_adapter(const std::vector<std::string>& reads,
                                const std::vector<std::string>& adapters,
                                const AdapterDetectOptions& options) {
    AdapterDetection result;
    result.sampled_reads = static_cast<int64_t>(reads.size());
    for (const std::string& sequence : reads) {
        result.sampled_bases += static_cast<int64_t>(sequence.size());
    }

    /* 上游：样本少于 10000 条就整体放弃检测。 */
    if (static_cast<int64_t>(reads.size()) < options.min_reads) {
        result.reason = "样本不足：只有 " + std::to_string(reads.size()) +
                        " 条 read，需要 " + std::to_string(options.min_reads) + " 条";
        return result;
    }

    if (!adapters.empty()) {
        int64_t checked_reads = 0;
        int64_t hits = 0;
        const std::string known =
            check_known_adapters(reads, adapters, options, checked_reads, hits);
        if (known.size() > kKnownMinReturnLength) {
            result.detected = true;
            result.adapter = known;
            result.source = kAdapterSourceKnown;
            result.seed_count = hits;
            result.seed_fold = checked_reads > 0
                                   ? static_cast<double>(hits) /
                                         static_cast<double>(checked_reads)
                                   : 0.0;
            return result;
        }
    }

    return detect_from_kmers(reads, adapters, options);
}

}  // namespace bio
