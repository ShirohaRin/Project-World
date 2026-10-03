/*
 * fastq.h —— FASTQ 流式读写（支持 gzip）
 *
 * 压缩处理交给 zlib 的 gzFile 接口（见 fastq.cpp）。选它的理由是：
 *
 * - ``gzopen`` / ``gzread`` **自动识别并按需解压**，对非压缩文件直接读取，
 *   因此上层不需要分支处理"压缩 / 不压缩"两条路径；
 * - 它自动处理**多 member** 的 gzip 文件（pigz、bgzip 等工具的常见输出），
 *   自己用 inflate 写就必须处理成员边界，容易出错；
 * - 它是真正的**流式**接口，几十 GB 的输入不必整体进内存。
 *
 * 这里没有采用 libdeflate：它的解压接口要求输入缓冲里至少包含一个完整的
 * gzip 成员，而常见的 ``.gz`` 是单成员，那等于要求整个文件进内存。
 *
 * 本文件用 pimpl 把 zlib 类型挡在实现里，头文件不暴露第三方依赖。
 *
 * 路径一律按 **UTF-8** 解释：Windows 上会先转成宽字符再调系统接口，
 * 因此含中文的路径可以正常读写（若直接用窄字符接口，中文路径会打不开，
 * 甚至悄悄建出一个名字乱码的文件）。
 */

#ifndef BIO_FASTQ_H
#define BIO_FASTQ_H

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>

namespace bio {

/* ---------------------------------------------------------------------------
 * 异常类型
 *
 * 用不同的类型而不是统一的 std::runtime_error：C ABI 层需要据此把失败
 * 翻译成不同的 bio_status_t。对调用方来说，"路径写错了"与"数据格式不对"
 * 是完全不同的问题，需要给出不同的提示。
 * ------------------------------------------------------------------------ */

/* 输入文件不存在或无法打开。 */
class InputFileError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/* 输入不是合法的 FASTQ，或压缩数据损坏。 */
class FastqFormatError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/* 输出文件无法创建或写入。 */
class OutputFileError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/* 一条 FASTQ 记录。``name`` 不含行首的 '@'。 */
struct FastqRecord {
    std::string name;
    std::string sequence;
    std::string quality;

    std::size_t length() const { return sequence.size(); }

    void clear() {
        name.clear();
        sequence.clear();
        quality.clear();
    }
};

/*
 * 流式读取器。
 *
 * 用法：
 *     FastqReader reader(path);
 *     FastqRecord record;
 *     while (reader.next(record)) { ...处理 record... }
 *
 * 路径可以是未压缩 FASTQ，也可以是 gzip 压缩的（按魔数自动判断）。
 * 格式错误会抛出 FastqFormatError，消息中带行号。
 */
class FastqReader {
public:
    explicit FastqReader(const std::string& path);
    ~FastqReader();

    FastqReader(const FastqReader&) = delete;
    FastqReader& operator=(const FastqReader&) = delete;

    /* 读取下一条记录，成功返回 true，读到结尾返回 false。 */
    bool next(FastqRecord& record);

    const std::string& path() const { return path_; }

private:
    struct State;
    std::unique_ptr<State> state_;
    std::string path_;
};

/*
 * 写出器。
 *
 * ``compress`` 为真时写 gzip，否则写纯文本。两种情况下都会创建父目录。
 * 按 FASTQ 规范写 4 行：``@名字`` / 序列 / ``+`` / 质量。
 */
class FastqWriter {
public:
    FastqWriter(const std::string& path, bool compress);
    ~FastqWriter();

    FastqWriter(const FastqWriter&) = delete;
    FastqWriter& operator=(const FastqWriter&) = delete;

    void write(const FastqRecord& record);

    /* 刷新并关闭；重复调用是安全的。 */
    void close();

    int64_t written() const { return written_; }

private:
    struct State;
    std::unique_ptr<State> state_;
    std::string path_;
    int64_t written_ = 0;
};

/* 按魔数判断文件是否为 gzip，不看扩展名（与上游 fastp 的做法一致）。 */
bool is_gzip_file(const std::string& path);

/*
 * 递归创建路径的父目录。
 * 输出路径形如 ``a/b/c.fq`` 时创建 ``a/b``；路径不含目录分隔符时什么都不做。
 */
void create_parent_directories(const std::string& path);

/* 删除文件；文件不存在时静默返回。用于失败时清理半成品输出。 */
void remove_file_if_exists(const std::string& path);

}  // namespace bio

#endif /* BIO_FASTQ_H */
