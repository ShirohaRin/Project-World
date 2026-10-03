#include "normalize.h"

#include <array>
#include <memory>
#include <stdexcept>

namespace bio {

namespace {

/* Phred+64 → Phred+33 的位移。 */
constexpr int kPhredShift = kPhredOffset64 - kPhredOffset33;

/* 查表：``max(33, code - 31)``。用 256 项表替代逐字符计算。 */
constexpr std::array<unsigned char, 256> make_phred_table() {
    std::array<unsigned char, 256> table{};
    for (std::size_t code = 0; code < table.size(); ++code) {
        const int shifted = static_cast<int>(code) - kPhredShift;
        table[code] = static_cast<unsigned char>(
            shifted < kPhredOffset33 ? kPhredOffset33 : shifted);
    }
    return table;
}

constexpr std::array<unsigned char, 256> kPhredTable = make_phred_table();

}  // namespace

void normalize_record(FastqRecord& record, const NormalizeOptions& options,
                      bool& renamed, bool& requantified) {
    renamed = false;
    requantified = false;

    if (options.fix_mgi) {
        const std::size_t length = record.name.size();
        if (length >= 2 && record.name[length - 2] == '/' &&
            (record.name[length - 1] == '1' || record.name[length - 1] == '2')) {
            /* 在斜杠前插一个空格：``xxx/1`` → ``xxx /1``。 */
            record.name.insert(length - 2, 1, ' ');
            renamed = true;
        }
    }

    if (options.input_phred == kPhredOffset64) {
        for (char& code : record.quality) {
            code = static_cast<char>(kPhredTable[static_cast<unsigned char>(code)]);
        }
        requantified = true;
    }
}

NormalizeStats normalize_single(const std::string& input_path,
                                const std::string& output_path,
                                bool compress,
                                const NormalizeOptions& options) {
    FastqReader reader(input_path);
    const bool writing = !output_path.empty();

    NormalizeStats stats;
    try {
        /* writer 声明在 try 内部：抛异常时它先随栈展开析构（关闭文件），
         * 上层再去删半成品才删得掉（Windows 上打开中的文件删不掉）。 */
        std::unique_ptr<FastqWriter> writer;
        if (writing) {
            writer = std::make_unique<FastqWriter>(output_path, compress);
        }

        FastqRecord record;
        while (reader.next(record)) {
            bool renamed = false;
            bool requantified = false;
            normalize_record(record, options, renamed, requantified);
            ++stats.total_reads;
            stats.input_bases += static_cast<int64_t>(record.length());
            stats.output_bases += static_cast<int64_t>(record.length());
            if (renamed) {
                ++stats.renamed_reads;
            }
            if (requantified) {
                ++stats.requantified_reads;
            }
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
    return stats;
}

NormalizeStats normalize_paired(const std::string& input1_path,
                                const std::string& output1_path,
                                const std::string& input2_path,
                                const std::string& output2_path,
                                bool compress,
                                const NormalizeOptions& options) {
    FastqReader reader1(input1_path);
    FastqReader reader2(input2_path);
    const bool writing = !output1_path.empty();

    NormalizeStats stats;
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

            bool renamed = false;
            bool requantified = false;
            normalize_record(record1, options, renamed, requantified);
            bool renamed2 = false;
            bool requantified2 = false;
            normalize_record(record2, options, renamed2, requantified2);

            stats.total_reads += 2;
            stats.input_bases +=
                static_cast<int64_t>(record1.length() + record2.length());
            stats.output_bases +=
                static_cast<int64_t>(record1.length() + record2.length());
            stats.renamed_reads += (renamed ? 1 : 0) + (renamed2 ? 1 : 0);
            stats.requantified_reads +=
                (requantified ? 1 : 0) + (requantified2 ? 1 : 0);

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
    return stats;
}

}  // namespace bio
