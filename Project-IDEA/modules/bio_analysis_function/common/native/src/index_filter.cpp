#include "index_filter.h"

#include <memory>

#include "read_names.h"

namespace bio {

bool matches_blacklist(const std::vector<std::string>& blacklist,
                       const std::string& target, int32_t threshold) {
    for (const std::string& entry : blacklist) {
        /* **只比两者中较短的长度**——上游 ``for(s=0; s<len1 && s<len2; s++)``。 */
        const std::size_t length = entry.size() < target.size()
                                       ? entry.size()
                                       : target.size();
        int32_t diff = 0;
        for (std::size_t index = 0; index < length; ++index) {
            if (entry[index] != target[index]) {
                ++diff;
                if (diff > threshold) {
                    break;
                }
            }
        }
        /* 提前退出时 diff 已经大于阈值，所以这里用 <= 判断是安全的。 */
        if (diff <= threshold) {
            return true;
        }
    }
    return false;
}

bool is_index_filtered(const std::string& name1, const std::string* name2,
                       const IndexFilterOptions& options) {
    if (!options.blacklist1.empty() &&
        matches_blacklist(options.blacklist1, first_index(name1),
                          options.threshold)) {
        return true;
    }
    if (name2 != nullptr && !options.blacklist2.empty() &&
        matches_blacklist(options.blacklist2, last_index(*name2),
                          options.threshold)) {
        return true;
    }
    return false;
}

namespace {

/* 两份黑名单都空时什么也不做——此时不必逐条判定。 */
bool filtering_enabled(const IndexFilterOptions& options) {
    return !options.blacklist1.empty() || !options.blacklist2.empty();
}

}  // namespace

IndexFilterStats filter_by_index_single(const std::string& input_path,
                                        const std::string& output_path,
                                        bool compress,
                                        const IndexFilterOptions& options) {
    FastqReader reader(input_path);
    const bool enabled = filtering_enabled(options);

    IndexFilterStats stats;
    try {
        /* writer 声明在 try 内部：抛异常时先随栈展开析构（关闭文件），
         * 上层再删半成品才删得掉。 */
        std::unique_ptr<FastqWriter> writer =
            std::make_unique<FastqWriter>(output_path, compress);

        FastqRecord record;
        while (reader.next(record)) {
            ++stats.total_reads;
            stats.input_bases += static_cast<int64_t>(record.length());
            if (enabled && is_index_filtered(record.name, nullptr, options)) {
                ++stats.filtered_reads;
                continue;
            }
            stats.output_bases += static_cast<int64_t>(record.length());
            writer->write(record);
        }
        writer->close();
    } catch (...) {
        remove_file_if_exists(output_path);
        throw;
    }
    return stats;
}

IndexFilterStats filter_by_index_paired(const std::string& input1_path,
                                        const std::string& output1_path,
                                        const std::string& input2_path,
                                        const std::string& output2_path,
                                        bool compress,
                                        const IndexFilterOptions& options) {
    FastqReader reader1(input1_path);
    FastqReader reader2(input2_path);
    const bool enabled = filtering_enabled(options);

    IndexFilterStats stats;
    try {
        std::unique_ptr<FastqWriter> writer1 =
            std::make_unique<FastqWriter>(output1_path, compress);
        std::unique_ptr<FastqWriter> writer2 =
            std::make_unique<FastqWriter>(output2_path, compress);

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

            /* 统计单位是 **read 对**：一对被一起判定、一起丢弃。 */
            ++stats.total_reads;
            stats.input_bases +=
                static_cast<int64_t>(record1.length() + record2.length());
            if (enabled && is_index_filtered(record1.name, &record2.name, options)) {
                ++stats.filtered_reads;
                continue;
            }
            stats.output_bases +=
                static_cast<int64_t>(record1.length() + record2.length());
            writer1->write(record1);
            writer2->write(record2);
        }
        writer1->close();
        writer2->close();
    } catch (...) {
        remove_file_if_exists(output1_path);
        remove_file_if_exists(output2_path);
        throw;
    }
    return stats;
}

}  // namespace bio
