#include "paired_adapter_trim.h"

#include <algorithm>
#include <cstdint>
#include <string>

#include "fastq.h"
#include "paired_pipeline.h"

namespace bio {

PairedAdapterTrimOutcome trim_adapter_pair(const FastqRecord& read1,
                                           const FastqRecord& read2,
                                           const OverlapOutcome& overlap,
                                           const PairedAdapterTrimOptions& options) {
    PairedAdapterTrimOutcome outcome;
    outcome.input_bases =
        static_cast<int64_t>(read1.length() + read2.length());

    /*
     * 只有"片段短于读长、两端都读穿"才有接头可裁——上游写的就是 offset < 0。
     * offset >= 0 的两类（片段更长、或长度正好）都没有接头。
     */
    if (!(overlap.overlapped && overlap.offset < 0)) {
        outcome.read1 = read1;
        outcome.read2 = read2;
        return outcome;
    }

    /*
     * 保留长度照上游取，注意两个补偿量是**交叉使用**的：R1 保留多长取决于 R2
     * 头部被剪掉多少（片段右端少了那么多，R1 要保留到的是那个新末端）。
     */
    const std::size_t keep1 = std::min(
        read1.sequence.size(),
        static_cast<std::size_t>(overlap.overlap_len) +
            static_cast<std::size_t>(options.front_trimmed2));
    const std::size_t keep2 = std::min(
        read2.sequence.size(),
        static_cast<std::size_t>(overlap.overlap_len) +
            static_cast<std::size_t>(options.front_trimmed1));

    outcome.trimmed = true;
    outcome.trimmed_bases = static_cast<int64_t>(
        (read1.sequence.size() - keep1) + (read2.sequence.size() - keep2));

    outcome.read1.name = read1.name;
    outcome.read1.sequence = read1.sequence.substr(0, keep1);
    outcome.read1.quality = read1.quality.substr(0, keep1);
    outcome.read2.name = read2.name;
    outcome.read2.sequence = read2.sequence.substr(0, keep2);
    outcome.read2.quality = read2.quality.substr(0, keep2);
    return outcome;
}

PairedAdapterTrimStats trim_paired_adapter_fastq(
    const std::string& read1_path,
    const std::string& read2_path,
    const std::string& output1_path,
    const std::string& output2_path,
    bool compress,
    const PairedAdapterTrimOptions& options) {
    PairedAdapterTrimStats stats;
    /*
     * 两个输出同时打开：一份输入对一对应两份输出，中间不能出现只写了一边的状态。
     * 它们必须声明在 try **内部**——异常时先随栈展开析构（关掉文件句柄），
     * catch 里的删除才会成功（Windows 上删一个仍被打开的文件会静默失败）。
     */
    try {
        FastqWriter writer1(output1_path, compress);
        FastqWriter writer2(output2_path, compress);
        run_paired_pipeline(
            read1_path, read2_path, options.threads, AutoThreads::Hardware,
            [&](const FastqRecord& read1, const FastqRecord& read2)
                -> PairedAdapterTrimOutcome {
                return trim_adapter_pair(
                    read1, read2,
                    analyze_overlap(read1.sequence, read2.sequence, options.overlap),
                    options);
            },
            [&](const PairedAdapterTrimOutcome& outcome) {
                ++stats.total_pairs;
                stats.input_bases += outcome.input_bases;
                if (outcome.trimmed) {
                    ++stats.trimmed_pairs;
                    stats.trimmed_bases += outcome.trimmed_bases;
                }
                stats.output_bases += static_cast<int64_t>(
                    outcome.read1.length() + outcome.read2.length());
                writer1.write(outcome.read1);
                writer2.write(outcome.read2);
            });
        writer1.close();
        writer2.close();
    } catch (...) {
        /* 两个输出是成对的产物，失败时一起删，不留只写了一边的残缺结果。 */
        remove_file_if_exists(output1_path);
        remove_file_if_exists(output2_path);
        throw;
    }
    return stats;
}

}  // namespace bio
