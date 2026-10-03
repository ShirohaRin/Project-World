# alignment_io：比对结果读写（SAM）

把比对结果读进来、写出去。它是模块**公共层的工具**：自身不做任何比对判定
（不筛映射质量、不算编辑距离、不挑最佳命中），因此**没有算法广场入口**。

两个方向各有用途：

- **写出**：我们的比对器产出 SAM——IGV 等工具能直接打开，也能拿来做人工核查；
- **读入**：别人的比对结果能读进来。这一点直接服务于 5.5.3 第 1 条决策：
  比对器无法承诺与 bowtie2 逐位一致，**唯一可靠的验证是"拿同一份比对结果往下走，
  看变异调用是否一致"**，而读上游 SAM 就是这条路的入口。

**BAM 不在本层**（同一套记录的二进制形态，要单独做），见待办。

## 1. 输入与输出

| 项 | 内容 |
| --- | --- |
| 输入 | SAM 文本文件（明文或 gzip，按魔数识别）；头部可缺（无头部也算合法） |
| 输出 | `SamHeader`（原始 `@` 行 + 解析出的参考序列表）与若干 `SamRecord` |
| 另可用 | `parse_cigar(text)` 单独解析 CIGAR；`parse_sam_record(line)` 解析单行 |

## 2. Python API 用法

```python
from modules.bio_analysis_function.common.alignment_io import (
    SamHeader,
    SamRecord,
    open_sam,
    parse_cigar,
    read_sam,
    write_sam,
)

# 读：头部立即可用，记录流式迭代（大文件不必整个读进内存）
with open_sam("aligned.sam") as reader:
    print(reader.header.sequences)                 # (@SQ 解析出的名字与长度)
    for record in reader:
        if record.is_unmapped or record.is_secondary or record.is_supplementary:
            continue                               # 这三个判断由调用方按需做，本层不替你做
        print(record.query_name, record.reference_name, record.position,
              record.cigar, record.reference_end, record.tag("NM"))

# 小文件一次读完
header, records = read_sam("aligned.sam")

# 写：给比对器用（头部只依赖"名字 + 长度"二元组，不需要认识参考资料的数据结构）
header = SamHeader.from_sequences([("NC_001422", 5386)], sort_order="unsorted")
write_sam("out.sam", header, records, compress=False)

# CIGAR 单独用
cigar = parse_cigar("10S90M")
print(cigar.query_length, cigar.reference_length, cigar.has_soft_clip, cigar.is_simple)
```

## 3. 三条必须知道的约定

1. **CIGAR 的消费关系**。`SEQ` 的长度必须等于**消费 query** 的操作之和，
   这条在构造记录时就校验——写错的 CIGAR 会让下游所有坐标错位，而且不报错。

   | 操作 | 含义 | 消费 query | 消费 reference |
   | --- | --- | --- | --- |
   | `M` | 比对（可能含错配） | 是 | 是 |
   | `I` | 相对参考的插入 | 是 | 否 |
   | `D` | 相对参考的缺失 | 否 | 是 |
   | `N` | 跳过的参考区（如内含子） | 否 | 是 |
   | `S` | 软剪裁（序列仍在） | 是 | 否 |
   | `H` | 硬剪裁（序列已不在记录里） | 否 | 否 |
   | `P` | 填充 | 否 | 否 |
   | `=` / `X` | 完全匹配 / 错配 | 是 | 是 |

2. **坐标沿用 SAM 自己的口径**：`position` 是 1-based，`0` 表示未比对；
   `mapping_quality` 取 0~255，`255` 表示"不可用"。本层不把它换算成 0-based——
   换算发生在取用坐标的地方，不在这里埋一次隐式转换。

3. **头部原样保留**。`SamHeader.lines` 是读进来的原始 `@` 行，写出去时**逐字节还原**；
   `@SQ` 额外解析成 `SamSequence` 供查询。这样往返不会悄悄重排或改写头部。

## 4. 支持范围

- 11 个必填字段 + 任意个可选标签（`name:type:value`，值为文本原样保存，
  另提供 `tag()` / `tag_int()` 两个取用口）。
- FLAG 的 12 个位都有具名属性：`is_paired` / `is_proper_pair` / `is_unmapped` /
  `is_mate_unmapped` / `is_reverse` / `is_read1` / `is_read2` / `is_secondary` /
  `is_supplementary` / `is_duplicate`（另有 `reference_end` 给出覆盖区间右端）。
- 无头部文件、空行、gzip。
- **构造时校验**：FLAG 范围、POS / PNEXT 非负、MAPQ 范围、已比对记录必须有参考名与
  ≥1 的 POS、未比对的 `*` 语义（`SEQ` 为 `*` 时 `QUAL` 必须也是 `*`）、
  `SEQ` 与 CIGAR 的 query 长度一致、`QUAL` 与 `SEQ` 等长。

**不支持**：BAM（二进制）、CRAM、SAM 里的 `B` 数组标签的类型级解析
（值按文本保留）、CIGAR 与坐标互算的辅助函数（见待办）。

## 5. 怎么跑测试

```bash
python -m pytest modules/bio_analysis_function/common/alignment_io -q
```

本项 **54 项**全过（0.21 s）：CIGAR **32 项**、SAM **22 项**。
另跑公共层回归（`common/tests` + 接头检测 + overlap + `reference_io` + 本项）**191 项**全过，
确认没有碰到既有工具。

---

## 附：设计记录

### 选择依据

**为什么它在比对器之前做。** 比对器需要一个"结果长什么样"的契约；把这个契约先定成
SAM，比对器就不用自创一套内部格式、也不用等到最后才考虑可核查性。而且它让上游
bowtie2 的比对结果可以进我们的下游——这在"不承诺逐位一致"的前提下是唯一能定位
"差异出在比对还是出在调用"的手段。

**为什么不做成算法入口。** 它不回答任何生物学问题，产物是"读进来的比对记录"，
注定被下游消费——按 `开发规则.md` 3.2 第 1 类，属公共层工具。

**为什么严格校验 `SEQ` 与 CIGAR 的一致性。** 这是本层唯一能提前抓住的严重错误：
CIGAR 与序列对不上时，任何按 CIGAR 走的坐标推算都是错的，而且**不会有人发现**。
宁可在这里失败。

**为什么流式读、并且把头部与记录分开。** 真实比对结果的规模随覆盖度线性增长
（细菌 100× 就是数亿条），整读进内存不现实；而头部必须先于记录拿到（下游要靠它
建立参考名 → 长度的映射）。

### 实现要点

- `Cigar` 保存 `CigarOp` 元组而不是文本，长度按"谁消费谁"两条求和得到
  （`query_length` / `reference_length`），并提供 `has_soft_clip` / `has_hard_clip` /
  `has_indel` / `has_skipped` / `is_simple` 几个判定口。**`is_simple`**（只含 `M`/`I`/`D`）
  是给变异检测用的——CIGAR 不简单时"某一位对应的参考坐标"要额外算。
- `SamHeader.from_sequences` **只吃 `(name, length)` 二元组**，不导入参考资料的数据结构：
  两个公共层工具之间保持单向、最小的耦合。
- 标签以 `(name, type, value)` 三元组保存，写回时逐字符还原；
  `tag_int` 对非整数返回 `None` 而不是抛错（调用方按"有没有这个信息"处理）。
- `SamReader` 支持 `with`：读头部时把第一条记录**暂存**在手上，迭代时先吐出它
  （否则会漏第一条）。

### 验证记录

| 组 | 内容 | 结果 |
| --- | --- | --- |
| `tests/test_cigar.py` | 八种操作逐一验消费关系（参数化）、`*` / 空 CIGAR、软硬剪裁区分、indel 与 `N`、`is_simple` 八种写法、文本往返、七种坏写法报错 | 32 项通过 |
| `tests/test_sam.py` | 头部解析与构造（含原始行保留）、流式读取、四条典型记录（正向带软剪裁 / 反向 read2 / 带缺失 / 未比对）的字段与 FLAG、标签取用、逐条往返、写出后**逐字节**等于原文、gzip、无头部文件、空行、九类报错路径 | 22 项通过 |

测试用的 SAM 是**按规范手写的**，不是抓来的真实数据：本项要验的是"格式读得对不对、
往返是否逐字节一致"，与某个数据集的内容无关。真实数据的 SAM 用例与上游对拍时一起补。

### 已知限制与待办

- **BAM 未支持**。做 BAM 要处理 BGZF（分块 gzip）、紧凑的 CIGAR/序列编码与
  `@SQ` ↔ `reference_id` 的整型映射；等真实数据规模需要时再做。
- **CIGAR 与坐标的互算没有做**：`read` 的第 n 个碱基对应参考的哪个位置、
  参考的第 m 位由哪些 read 覆盖——这是堆叠（pileup）与共识调用要用的能力，
  但它属于"用 CIGAR 的语义"，不属于"读写格式"，因此留在下一项里做，
  接口等堆叠层设计定了再定。
- **真实数据 SAM 用例缺失**（见上文）。
- `B` 数组标签（`B:i,1,2,3`）的值按文本保留，没有解析成数组；变异检测不依赖它，
  需要时再补。
