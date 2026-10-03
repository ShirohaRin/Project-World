#include "paired_merge.h"

#include <algorithm>
#include <cstdint>
#include <string>

#include "fastq.h"
#include "paired_pipeline.h"
#include "sequence.h"

namespace bio {

PairedMergeOutcome merge_read_pair(const FastqRecord& read1,
                                   const FastqRecord& read2,
                                   const OverlapOutcome& overlap) {
    PairedMergeOutcome outcome;
    outcome.input_bases =
        static_cast<int64_t>(read1.length() + read2.length());

    if (!overlap.overlapped) {
        return outcome;
    }
    outcome.merged = true;
    outcome.gap = overlap.has_gap;

    std::string reverse2(read2.sequence.size(), '\0');
    reverse_complement(read2.sequence.data(), reverse2.data(),
                       static_cast<int64_t>(read2.sequence.size()));
    std::string reverse2_quality = read2.quality;
    std::reverse(reverse2_quality.begin(), reverse2_quality.end());

    /* 上游取 max(0, offset)：offset <= 0 时保留长度就是重叠长度本身。 */
    const std::size_t len1 = static_cast<std::size_t>(
        overlap.overlap_len + std::max<int32_t>(0, overlap.offset));
    const std::size_t len2 =
        overlap.offset > 0
            ? read2.sequence.size() - static_cast<std::size_t>(overlap.overlap_len)
            : 0;

    FastqRecord& result = outcome.record;
    result.name = read1.name + " merged_" + std::to_string(len1) + "_" +
                  std::to_string(len2);
    result.sequence = read1.sequence.substr(0, len1);
    result.quality = read1.quality.substr(0, len1);
    if (overlap.offset > 0) {
        result.sequence.append(reverse2, static_cast<std::size_t>(overlap.overlap_len),
                               len2);
        result.quality.append(reverse2_quality,
                              static_cast<std::size_t>(overlap.overlap_len), len2);
    }
    return outcome;
}

PairedMergeStats merge_paired_fastq(const std::string& read1_path,
                                    const std::string& read2_path,
                                    const std::string& output_path,
                                    bool compress,
                                    const PairedMergeOptions& options) {
    PairedMergeStats stats;
    /*
     * writer 声明在 try **内部**：异常时它会先随栈展开析构（关掉文件句柄），
     * catch 里的删除才会成功——Windows 上删一个仍被打开的文件会静默失败
     * （`remove_file_if_exists` 是尽力而为、不报错的，所以这一点必须靠作用域保证）。
     */
    try {
        FastqWriter writer(output_path, compress);
        run_paired_pipeline(
            read1_path, read2_path, options.threads, AutoThreads::Hardware,
            [&](const FastqRecord& read1, const FastqRecord& read2)
                -> PairedMergeOutcome {
                return merge_read_pair(
                    read1, read2,
                    analyze_overlap(read1.sequence, read2.sequence, options.overlap));
            },
            [&](const PairedMergeOutcome& outcome) {
                ++stats.total_pairs;
                stats.input_bases += outcome.input_bases;
                if (!outcome.merged) {
                    ++stats.unmerged_pairs;
                    return;
                }
                ++stats.merged_pairs;
                stats.output_bases += static_cast<int64_t>(outcome.record.length());
                if (outcome.gap) {
                    ++stats.gap_overlaps;
                }
                writer.write(outcome.record);
            });
        writer.close();
    } catch (...) {
        remove_file_if_exists(output_path);
        throw;
    }
    return stats;
}

}  // namespace bio
