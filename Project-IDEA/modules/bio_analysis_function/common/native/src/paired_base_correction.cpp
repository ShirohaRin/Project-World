#include "paired_base_correction.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>

#include "fastq.h"
#include "paired_pipeline.h"
#include "sequence.h"

namespace bio {
namespace {

/*
 * 上游写死的两个质量门槛（``BaseCorrector`` 里的局部常量 ``num2qual(30)`` 与
 * ``num2qual(14)``）。达到"可信"才算高，低到"不可信"才算低；中间那一段不参与修正。
 */
constexpr char kGoodQuality = static_cast<char>(30 + 33);
constexpr char kBadQuality = static_cast<char>(14 + 33);

}  // namespace

PairedCorrectionOutcome correct_pair_bases(const FastqRecord& read1,
                                          const FastqRecord& read2,
                                          const OverlapOutcome& overlap) {
    PairedCorrectionOutcome outcome;
    outcome.input_bases =
        static_cast<int64_t>(read1.length() + read2.length());

    /* 上游的短路：没有错配、或压根没重叠，就一个字节都不改。 */
    if (!overlap.overlapped || overlap.diff == 0) {
        outcome.read1 = read1;
        outcome.read2 = read2;
        return outcome;
    }
    /* 带缺口的重叠里位置对应不可靠，上游在调用点明确跳过。 */
    if (overlap.has_gap) {
        outcome.read1 = read1;
        outcome.read2 = read2;
        return outcome;
    }

    std::string sequence1 = read1.sequence;
    std::string quality1 = read1.quality;
    std::string sequence2 = read2.sequence;
    std::string quality2 = read2.quality;

    const int start1 = std::max(0, overlap.offset);
    const int start2 = static_cast<int>(read2.sequence.size()) -
                       std::max(0, -overlap.offset) - 1;

    int64_t corrected = 0;
    bool corrected1 = false;
    bool corrected2 = false;
    for (int32_t index = 0; index < overlap.overlap_len; ++index) {
        const std::size_t position1 =
            static_cast<std::size_t>(start1 + index);
        const std::size_t position2 =
            static_cast<std::size_t>(start2 - index);

        if (sequence1[position1] == complement_of(sequence2[position2])) {
            continue;  /* 这一位两边一致，不用管 */
        }

        const char quality_at_1 = quality1[position1];
        const char quality_at_2 = quality2[position2];
        if (quality_at_1 >= kGoodQuality && quality_at_2 <= kBadQuality) {
            /* R1 可信、R2 不可信 → 用 R1 改 R2，并把 R1 的质量值一并赋过去。 */
            sequence2[position2] = complement_of(sequence1[position1]);
            quality2[position2] = quality_at_1;
            ++corrected;
            corrected2 = true;
        } else if (quality_at_2 >= kGoodQuality &&
                   quality_at_1 <= kBadQuality) {
            /* 反过来：R2 可信、R1 不可信 → 用 R2 改 R1。 */
            sequence1[position1] = complement_of(sequence2[position2]);
            quality1[position1] = quality_at_2;
            ++corrected;
            corrected1 = true;
        }
        /* 其余情况（两边都可信 / 两边都不可信）不修正：说不清哪边对。 */
    }

    if (corrected == 0) {
        outcome.read1 = read1;
        outcome.read2 = read2;
        return outcome;
    }

    outcome.corrected = corrected;
    outcome.corrected_reads = (corrected1 ? 1 : 0) + (corrected2 ? 1 : 0);
    outcome.read1 = FastqRecord{read1.name, std::move(sequence1),
                                std::move(quality1)};
    outcome.read2 = FastqRecord{read2.name, std::move(sequence2),
                                std::move(quality2)};
    return outcome;
}

PairedCorrectionStats correct_paired_fastq(
    const std::string& read1_path,
    const std::string& read2_path,
    const std::string& output1_path,
    const std::string& output2_path,
    bool compress,
    const PairedCorrectionOptions& options) {
    PairedCorrectionStats stats;
    /*
     * 两个输出同时打开；它们必须声明在 try **内部**——异常时先随栈展开析构
     * （关掉文件句柄），catch 里的删除才会成功。
     */
    try {
        FastqWriter writer1(output1_path, compress);
        FastqWriter writer2(output2_path, compress);
        run_paired_pipeline(
            read1_path, read2_path, options.threads, AutoThreads::Hardware,
            [&](const FastqRecord& read1, const FastqRecord& read2)
                -> PairedCorrectionOutcome {
                return correct_pair_bases(
                    read1, read2,
                    analyze_overlap(read1.sequence, read2.sequence, options.overlap));
            },
            [&](const PairedCorrectionOutcome& outcome) {
                ++stats.total_pairs;
                stats.input_bases += outcome.input_bases;
                stats.output_bases += static_cast<int64_t>(
                    outcome.read1.length() + outcome.read2.length());
                if (outcome.corrected > 0) {
                    ++stats.corrected_pairs;
                    stats.corrected_reads += outcome.corrected_reads;
                    stats.corrected_bases += outcome.corrected;
                }
                writer1.write(outcome.read1);
                writer2.write(outcome.read2);
            });
        writer1.close();
        writer2.close();
    } catch (...) {
        remove_file_if_exists(output1_path);
        remove_file_if_exists(output2_path);
        throw;
    }
    return stats;
}

}  // namespace bio
