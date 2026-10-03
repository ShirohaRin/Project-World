/*
 * read_names.h —— read 名字的解析（取 index 段）
 *
 * **这是工具，不是算法**：它没有自己的输入输出契约，只是别的算法里重复出现的
 * 那几行。目前的使用方：UMI 提取（按 index 取 UMI）、按 index 过滤
 * （拿 index 与黑名单比对）。
 *
 * 与 Python 侧 ``common/read_names.py`` 逐行对应。
 *
 * 名字的格式假设完全照抄上游：``... 1:N:0:TATAGCCT+GGTCCCGA`` 这类
 * Illumina 风格的 read 名。上游的 ``Read::firstIndex`` / ``lastIndex`` 是
 * 按"倒数第 3 个字符往左扫"实现的，不依赖任何字段位置解析。
 */

#ifndef BIO_READ_NAMES_H
#define BIO_READ_NAMES_H

#include <cstddef>
#include <string>

namespace bio {

/* 名字里 index 段的分隔符（上游解析时找的就是这两个字符）。 */
inline bool is_index_separator(char ch) {
    return ch == ':' || ch == '+';
}

/*
 * 取 read 名里第一段 index（上游 ``Read::firstIndex``）。
 *
 * 从倒数第 3 个字符往左扫：遇到 '+' 记下右边界，再遇到 ':' 就返回两段之间的部分。
 * 上游的 ``substr(i+1, end-i)`` 结束于 ``end``，所以这里长度取 ``end - index``。
 */
inline std::string first_index(const std::string& name) {
    /* 上游的 mName 带行首 '@'，而它的长度判断依赖这一点，所以补回去再解析。 */
    const std::string text = "@" + name;
    const int length = static_cast<int>(text.size());
    if (length < 5) {
        return "";
    }
    int end = length;
    for (int index = length - 3; index >= 0; --index) {
        const std::size_t position = static_cast<std::size_t>(index);
        if (text[position] == '+') {
            end = index - 1;
        }
        if (text[position] == ':') {
            return text.substr(position + 1, static_cast<std::size_t>(end - index));
        }
    }
    return "";
}

/* 取 read 名里最后一段 index（上游 ``Read::lastIndex``）。 */
inline std::string last_index(const std::string& name) {
    const std::string text = "@" + name;
    const int length = static_cast<int>(text.size());
    if (length < 5) {
        return "";
    }
    for (int index = length - 3; index >= 0; --index) {
        const std::size_t position = static_cast<std::size_t>(index);
        if (is_index_separator(text[position])) {
            return text.substr(position + 1);
        }
    }
    return "";
}

}  // namespace bio

#endif /* BIO_READ_NAMES_H */
