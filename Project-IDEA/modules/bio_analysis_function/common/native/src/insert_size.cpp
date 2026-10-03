#include "insert_size.h"

#include <stdexcept>

#include "fastq.h"

namespace bio {

int32_t insert_size_from(const OverlapOutcome& outcome,
                         std::size_t read1_length,
                         std::size_t read2_length) {
    if (!outcome.overlapped) {
        return -1;
    }
    /* 两个分支照抄上游 ``PairEndProcessor::statInsertSize``：
     * ``offset > 0`` 时片段比读长长，两条 read 只在中间重叠；
     * 否则两端读穿，重叠长度本身就是片段长度。 */
    if (outcome.offset > 0) {
        return static_cast<int32_t>(read1_length + read2_length) -
               outcome.overlap_len;
    }
    return outcome.overlap_len;
}

InsertSizeStats analyze_insert_size(const std::string& read1_path,
                                    const std::string& read2_path,
                                    const InsertSizeOptions& options) {
    if (options.max_size < 1) {
        throw std::invalid_argument("max_size 必须为正，当前为 " +
                                    std::to_string(options.max_size) + "。");
    }

    FastqReader reader1(read1_path);
    FastqReader reader2(read2_path);

    InsertSizeStats stats;
    stats.histogram.assign(static_cast<std::size_t>(options.max_size) + 1, 0);

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

        ++stats.total_pairs;
        const OverlapOutcome outcome =
            analyze_overlap(record1.sequence, record2.sequence, options.overlap);
        const int32_t size =
            insert_size_from(outcome, record1.length(), record2.length());
        if (size >= 0) {
            ++stats.overlapped_pairs;
        }

        /* 判不出 → 溢出桶；算出来但超过上限也并入同一个桶（判据是严格大于，
         * 所以恰好等于上限仍留在自己的桶里——上游如此）。 */
        int32_t bucket = options.max_size;
        if (size >= 0 && size <= options.max_size) {
            bucket = size;
        }
        ++stats.histogram[static_cast<std::size_t>(bucket)];
    }

    /* 峰值只在上限之内找：溢出桶里混着"判不出"的，把它算成峰值没有意义。
     * 并列时取较小的长度（严格大于比较，先遇到的胜出）。 */
    int32_t peak = 0;
    int64_t best = 0;
    for (int32_t index = 0; index < options.max_size; ++index) {
        const int64_t count = stats.histogram[static_cast<std::size_t>(index)];
        if (count > best) {
            best = count;
            peak = index;
        }
    }
    stats.peak_size = peak;
    return stats;
}

}  // namespace bio
