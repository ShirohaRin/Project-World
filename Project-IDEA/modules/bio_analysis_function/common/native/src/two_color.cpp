#include "two_color.h"

#include <cstring>

#include "fastq.h"

namespace bio {
namespace {

/*
 * 双色测序仪的名字前缀，逐条照抄上游 `Evaluator::isTwoColorSystem` 的表
 * （含注释里的型号）。**顺序也照抄**——上游按这个顺序比对，命中就返回。
 *
 * 表里有几条是别的前缀的前缀（"NS" 之于 "NS500..."、"A" 之于 "A00123..."），
 * 但判定结果只有"是/否"两种，谁先命中不影响结论。
 */
constexpr const char* kTwoColorPrefixes[] = {
    "FS",         /* iSeq 100 */
    "MN", "SH",   /* MiniSeq */
    "NS", "NB",   /* NextSeq 500/550 */
    "NDX",        /* NextSeq 550DX */
    "VL", "VH",   /* NextSeq 1000/2000 */
    "A", "NA",    /* NovaSeq 6000 */
    "LH",         /* NovaSeq X */
};

/* 前缀表按 C 字符串存，比对是常数时间；不必为它引一个通用工具。 */
bool starts_with(const std::string& text, const char* prefix) {
    const std::size_t length = std::strlen(prefix);
    return text.size() >= length && text.compare(0, length, prefix) == 0;
}

}  // namespace

bool is_two_color_system(const std::string& read1_path) {
    FastqReader reader(read1_path);
    FastqRecord record;
    if (!reader.next(record)) {
        return false;
    }
    for (const char* prefix : kTwoColorPrefixes) {
        if (starts_with(record.name, prefix)) {
            return true;
        }
    }
    return false;
}

}  // namespace bio
