/*
 * sequence.h —— 序列层面的公共小工具（反向互补、错配计数）
 *
 * 与 Python 侧 ``common/sequences.py`` 一一对应：这几件事在多个算法里重复出现，
 * 语义必须只有一套，否则"错配怎么数、未知碱基怎么处理"迟早会在两个算法里漂移。
 *
 * 当前使用方：adapter_trim（错配计数）、overlap（三者都用）。
 *
 * 都是 inline 的小函数，放头文件里，调用点不必多一次跳转。
 */

#ifndef BIO_SEQUENCE_H
#define BIO_SEQUENCE_H

#include <cstdint>

namespace bio {

/*
 * 单个碱基的互补。与上游 ``fastp_simd`` 一致：只认 ACGTN（大小写各一套），
 * 其余字符原样返回——上游对未知字符不报错，这里也不报错。
 */
inline char complement_of(char base) {
    switch (base) {
        case 'A': return 'T';
        case 'C': return 'G';
        case 'G': return 'C';
        case 'T': return 'A';
        case 'N': return 'N';
        case 'a': return 't';
        case 'c': return 'g';
        case 'g': return 'c';
        case 't': return 'a';
        case 'n': return 'n';
        default: return base;
    }
}

/*
 * 把 ``length`` 个碱基反向互补写入 ``destination``。
 *
 * ``destination`` 由调用方提供、必须有至少 ``length`` 字节，且可以与 ``source``
 * 不同（本函数不做就地反转）。这样调用方能把缓冲区复用到 thread_local 上，
 * 免得每对 read 都分配一次。
 */
inline void reverse_complement(const char* source, char* destination, int64_t length) {
    for (int64_t index = 0; index < length; ++index) {
        destination[index] = complement_of(source[length - 1 - index]);
    }
}

/*
 * 比对前 ``length`` 个碱基的错配数；一旦超过 ``limit`` 立刻收工。
 *
 * 对应上游 ``fastp_simd::countMismatchesBounded``：返回值的语义是
 * "错配数，但一旦超过 limit 就返回一个 > limit 的数"，因此调用方用
 * ``<= limit`` 判断命中。``length`` 超出任一条串的长度时由调用方保证不发生
 * （上游同样不做边界检查）。
 */
inline int64_t count_mismatches_bounded(const char* left,
                                        const char* right,
                                        int64_t length,
                                        int64_t limit) {
    int64_t mismatches = 0;
    for (int64_t index = 0; index < length; ++index) {
        if (left[index] != right[index]) {
            ++mismatches;
            if (mismatches > limit) {
                return mismatches;
            }
        }
    }
    return mismatches;
}

/*
 * 比对前 ``length`` 个碱基的**完整**错配数（不提前退出）。
 *
 * 上游有地方需要"精确值"而不是"是否超限"：overlap 分析在比对长度超过 50 时
 * 要把真实错配数报出去，哪怕它很大。
 */
inline int64_t count_mismatches(const char* left, const char* right, int64_t length) {
    int64_t mismatches = 0;
    for (int64_t index = 0; index < length; ++index) {
        if (left[index] != right[index]) {
            ++mismatches;
        }
    }
    return mismatches;
}

}  // namespace bio

#endif /* BIO_SEQUENCE_H */
