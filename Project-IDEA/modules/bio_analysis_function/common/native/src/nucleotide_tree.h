/*
 * nucleotide_tree.h —— 碱基前缀树（上游 fastp 的 NucleotideTree）
 *
 * **这是工具，不是算法**：它没有自己的输入输出契约，只是"把若干序列挂在树上、
 * 再取占优路径"这件事本身。接头检测用它围绕种子向两侧延伸，后续的序列分析
 * 也用得上同一套东西，因此放在公共层。
 *
 * 与上游逐位对齐的两处行为：
 *
 * - 子节点按 ``碱基 & 0x07`` 索引（8 个槽）。A/C/G/T 的 ASCII 低三位互不相同，
 *   因此不会撞车；遇到 ``N`` 直接停止挂载（上游行为）。
 * - ``count`` 记的是"经过该节点的序列条数"，占优判据用
 *   ``count / 该层所有子节点计数之和``，阈值 0.95、节点总数下限 50。
 *
 * 占比 ≥ 0.95 意味着同一层里至多只有一个子节点占优，因此遍历槽位的顺序
 * 不影响结果（测试里固定了这一点）。
 */

#ifndef BIO_NUCLEOTIDE_TREE_H
#define BIO_NUCLEOTIDE_TREE_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

namespace bio {

class NucleotideTree {
public:
    NucleotideTree() : root_(std::make_unique<Node>()) {}

    /* 占优判据：子节点占比达到它才算"占优"。 */
    static constexpr double kRatioThreshold = 0.95;
    /* 占优判据：当前层的子节点计数之和低于它就不再延伸。 */
    static constexpr int64_t kCountThreshold = 50;

    /* 把一条序列挂到树上；遇到 'N' 停止（上游行为，N 不参与判定）。 */
    void add(std::string_view sequence) {
        Node* node = root_.get();
        for (const char base : sequence) {
            if (base == 'N') {
                break;
            }
            const std::size_t slot =
                static_cast<std::size_t>(static_cast<unsigned char>(base) & 0x07);
            Node* child = node->children[slot].get();
            if (child == nullptr) {
                node->children[slot] = std::make_unique<Node>();
                child = node->children[slot].get();
                child->base = base;
            }
            ++child->count;
            node = child;
        }
    }

    /*
     * 从根出发一路走"占优"子节点，返回走出来的路径。
     *
     * ``reached_leaf`` 的语义与上游一致：路径因为**没有占优子节点**而停下时置 false
     * （说明前方是随机序列，拼出来的东西不可信）；因为**计数太少**而停下时保持 true。
     */
    std::string dominant_path(bool& reached_leaf) const {
        std::string path;
        const Node* node = root_.get();
        while (true) {
            int64_t total = 0;
            for (const auto& child : node->children) {
                if (child != nullptr) {
                    total += child->count;
                }
            }
            if (total < kCountThreshold) {
                break;
            }
            const Node* chosen = nullptr;
            for (const auto& child : node->children) {
                if (child != nullptr &&
                    static_cast<double>(child->count) / static_cast<double>(total) >=
                        kRatioThreshold) {
                    chosen = child.get();
                    break;
                }
            }
            if (chosen == nullptr) {
                reached_leaf = false;
                break;
            }
            path.push_back(chosen->base);
            node = chosen;
        }
        return path;
    }

private:
    struct Node {
        int64_t count = 0;
        char base = 'N';
        std::array<std::unique_ptr<Node>, 8> children{};
    };

    std::unique_ptr<Node> root_;
};

}  // namespace bio

#endif /* BIO_NUCLEOTIDE_TREE_H */
