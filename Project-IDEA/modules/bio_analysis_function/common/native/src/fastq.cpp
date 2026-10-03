#include "fastq.h"

#include <filesystem>
#include <fstream>
#include <system_error>

#include "zlib.h"

namespace bio {

namespace {

constexpr char kGzipMagic0 = '\x1f';
constexpr char kGzipMagic1 = '\x8b';

/*
 * 每次向 zlib 索取的字节数。1 MB 足以摊薄 gzread 的调用开销，
 * 又不至于让内存占用随并发线程数失控。
 */
constexpr std::size_t kBufferSize = 1u << 20;

/*
 * 路径转换：对外一律是 UTF-8 字符串，交给系统调用之前必须先转成宽字符。
 *
 * 为什么必须转：Windows 的 fopen / _open 用的是**当前代码页**（中文系统上是 GBK），
 * 把 UTF-8 字节直接喂进去，含中文的路径就打不开——更麻烦的是它往往不报错，
 * 而是"成功"创建出一个名字面目全非的文件，调用方随后在自己的输出路径上扑空。
 * 先经 u8path 转成宽字符，再走宽字符接口，这条坑才不存在。
 */
std::filesystem::path to_path(const std::string& utf8_path) {
    return std::filesystem::u8path(utf8_path);
}

/*
 * 打开 gzFile。
 *
 * zlib 在 Windows 上额外提供 ``gzopen_w``：其余平台只有窄字符版本
 * （那边的窄字符本来就是 UTF-8，不存在代码页问题）。
 */
gzFile open_gz(const std::filesystem::path& path, const char* mode) {
#if defined(_WIN32)
    return gzopen_w(path.wstring().c_str(), mode);
#else
    return gzopen(path.string().c_str(), mode);
#endif
}

void strip_cr(std::string& line) {
    if (!line.empty() && line.back() == '\r') {
        line.pop_back();
    }
}

/* 构造"记录不完整"的报错文本。三处调用共用，避免措辞漂移。 */
FastqFormatError incomplete_record(int64_t start_line) {
    return FastqFormatError("第 " + std::to_string(start_line) +
                            " 行起的记录不完整：FASTQ 每条记录需要 4 行。");
}

}  // namespace

/* ------------------------------------------------------------------------- */
/* 读取                                                                       */
/* ------------------------------------------------------------------------- */

struct FastqReader::State {
    gzFile file = nullptr;
    std::string chunk;         /* 已解码、等待切分的字节块 */
    std::size_t position = 0;  /* chunk 中尚未消费的位置 */
    bool eof = false;
    int64_t line_number = 0;

    /*
     * 从解压后的字节流里读出一行（不含换行符），写入 line。
     * 返回 false 表示到达结尾。行可以跨缓冲区，因此需要累积。
     */
    bool read_line(const std::string& path, std::string& line);
};

bool FastqReader::State::read_line(const std::string& path, std::string& line) {
    line.clear();
    while (true) {
        const std::size_t newline = chunk.find('\n', position);
        if (newline != std::string::npos) {
            line.append(chunk, position, newline - position);
            position = newline + 1;
            strip_cr(line);
            return true;
        }

        // 当前块里没有换行符：把剩余部分并入本行，然后取下一块。
        line.append(chunk, position, chunk.size() - position);
        position = chunk.size();

        if (eof) {
            if (line.empty()) {
                return false;  // 正常到达结尾
            }
            strip_cr(line);
            return true;  // 文件末尾没有换行符，最后一行仍然有效
        }

        chunk.resize(kBufferSize);
        const int got = gzread(file, &chunk[0], static_cast<unsigned>(kBufferSize));
        if (got < 0) {
            int error_number = 0;
            const char* detail = gzerror(file, &error_number);
            throw FastqFormatError("读取或解压失败：" + path + "（" +
                                   (detail != nullptr ? detail : "未知错误") + "）");
        }
        if (got == 0) {
            eof = true;
            chunk.clear();
        } else {
            chunk.resize(static_cast<std::size_t>(got));
        }
        position = 0;
    }
}

FastqReader::FastqReader(const std::string& path) : path_(path) {
    const std::filesystem::path file_path = to_path(path);
    std::error_code error;
    const bool exists = std::filesystem::exists(file_path, error);
    if (error || !exists) {
        throw InputFileError("文件不存在：" + path);
    }
    state_ = std::make_unique<State>();
    // "rb" 由 zlib 自行判断：输入是 gzip 就解压，不是就直接读。
    state_->file = open_gz(file_path, "rb");
    if (state_->file == nullptr) {
        throw InputFileError("无法打开文件：" + path);
    }
    gzbuffer(state_->file, static_cast<unsigned>(kBufferSize));
}

FastqReader::~FastqReader() {
    if (state_ != nullptr && state_->file != nullptr) {
        gzclose(state_->file);
        state_->file = nullptr;
    }
}

bool FastqReader::next(FastqRecord& record) {
    State& state = *state_;
    std::string line;  // 复用同一个字符串承载 4 行

    if (!state.read_line(path_, line)) {
        return false;
    }
    const int64_t start_line = ++state.line_number;
    if (line.empty() || line[0] != '@') {
        throw FastqFormatError("第 " + std::to_string(start_line) +
                               " 行的名字行不以 '@' 开头。");
    }
    record.name.assign(line.begin() + 1, line.end());

    if (!state.read_line(path_, line)) {
        throw incomplete_record(start_line);
    }
    ++state.line_number;
    record.sequence = line;

    if (!state.read_line(path_, line)) {
        throw incomplete_record(start_line);
    }
    ++state.line_number;
    if (line.empty() || line[0] != '+') {
        throw FastqFormatError("第 " + std::to_string(state.line_number) +
                               " 行的分隔行不以 '+' 开头。");
    }

    if (!state.read_line(path_, line)) {
        throw incomplete_record(start_line);
    }
    ++state.line_number;
    record.quality = line;

    if (record.sequence.size() != record.quality.size()) {
        throw FastqFormatError(
            "第 " + std::to_string(start_line) + " 行起的记录中，序列长度（" +
            std::to_string(record.sequence.size()) + "）与质量长度（" +
            std::to_string(record.quality.size()) + "）不一致。");
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* 写出                                                                       */
/* ------------------------------------------------------------------------- */

struct FastqWriter::State {
    gzFile file = nullptr;
    std::string buffer;  /* 复用的写出缓冲，避免每条记录一次堆分配 */
};

FastqWriter::FastqWriter(const std::string& path, bool compress) : path_(path) {
    create_parent_directories(path);
    state_ = std::make_unique<State>();
    // "wb6" 以 gzip 格式写出；"wbT" 是 zlib 的透明写模式，即不压缩。
    const char* mode = compress ? "wb6" : "wbT";
    state_->file = open_gz(to_path(path), mode);
    if (state_->file == nullptr) {
        throw OutputFileError("无法创建输出文件：" + path);
    }
    gzbuffer(state_->file, static_cast<unsigned>(kBufferSize));
}

FastqWriter::~FastqWriter() {
    if (state_ != nullptr && state_->file != nullptr) {
        gzclose(state_->file);
        state_->file = nullptr;
    }
}

void FastqWriter::write(const FastqRecord& record) {
    State& state = *state_;
    std::string& buffer = state.buffer;
    buffer.clear();
    buffer.reserve(record.name.size() + record.sequence.size() +
                   record.quality.size() + 4);
    buffer.push_back('@');
    buffer.append(record.name);
    buffer.push_back('\n');
    buffer.append(record.sequence);
    buffer.push_back('\n');
    buffer.push_back('+');
    buffer.push_back('\n');
    buffer.append(record.quality);
    buffer.push_back('\n');

    const int written =
        gzwrite(state.file, buffer.data(), static_cast<unsigned>(buffer.size()));
    if (written != static_cast<int>(buffer.size())) {
        throw OutputFileError("写入输出文件失败：" + path_);
    }
    ++written_;
}

void FastqWriter::close() {
    if (state_ == nullptr || state_->file == nullptr) {
        return;
    }
    gzFile file = state_->file;
    state_->file = nullptr;  // 先清空，避免关闭失败时重复关闭
    if (gzclose(file) != Z_OK) {
        throw OutputFileError("关闭输出文件失败：" + path_);
    }
}

/* ------------------------------------------------------------------------- */
/* 工具                                                                       */
/* ------------------------------------------------------------------------- */

bool is_gzip_file(const std::string& path) {
    // 用宽字符路径打开，理由见 to_path 的注释。
    std::ifstream stream(to_path(path), std::ios::binary);
    if (!stream.is_open()) {
        return false;
    }
    char magic[2] = {0, 0};
    stream.read(magic, 2);
    return stream.gcount() == 2 && magic[0] == kGzipMagic0 && magic[1] == kGzipMagic1;
}

void create_parent_directories(const std::string& path) {
    const std::filesystem::path file_path = to_path(path);
    const std::filesystem::path parent = file_path.parent_path();
    if (parent.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(parent, error);
    if (error) {
        // 报错文本用原始的 UTF-8 路径：宽字符路径转回窄字符可能再失败一次，
        // 没必要在错误处理里引入新的不确定因素。
        throw OutputFileError("无法创建输出目录：" + path);
    }
}

void remove_file_if_exists(const std::string& path) {
    std::error_code error;
    std::filesystem::remove(to_path(path), error);
    // 故意忽略 error：清理是尽力而为，不应覆盖掉真正的失败原因。
}

}  // namespace bio
