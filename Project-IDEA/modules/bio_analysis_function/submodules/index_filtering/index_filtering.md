# index_filtering：按 index 过滤

按 index（barcode）黑名单把**不属于本样本**的 read 筛掉。
判定与 fastp 的 `Filter::filterByIndex` / `Filter::match` 逐位对齐。

本子模块**可独立使用**（方法 id `bio-index-filtering`），只需要一份（或一对）
FASTQ，单端与双端都支持。

---

## 1. 它解决什么问题

一次测序 run 里通常混着多个样本，靠 index（也叫 barcode、标签序列）区分。
建库或拆分出错时会冒出**不属于本样本**的 index：

- **index 交叉污染**：上机前混样时串了；
- **index hopping**：ExAmp 扩增时标签跳到别的分子上（ patterned flowcell 上更常见）。

这些 read 如果留到比对阶段，会变成假阳性变异、假定量信号。**在比对之前筛掉最省事。**

用法是给一份**黑名单**（每行一个 index 序列）：命中黑名单的 read 被丢掉。
上游没有白名单模式——要"只保留某些 index"，就把其余的列成黑名单。

> **与 [`read_filtering`](../read_filtering/read_filtering.md) 的区别**：
> 那边看的是 **read 本身的质量**，这边看的是 **read 的标签属于哪个样本**。
> 上游也把它们放在处理链的不同位置。

---

## 2. 在算法广场中使用

方法 id `bio-index-filtering`｜分类"序列预处理"｜`local`｜`beta`。
契约位置：`IDEA Assistant Code/src/compute.ts` 的 `BIO_INDEX_FILTERING_METHOD`。

**输入**：reads 文件、输出路径、可选 R2 与输出 R2、两份 index 黑名单、错配阈值、输出压缩。
**输出**：过滤后的 FASTQ（双端两份，始终对齐）+ 统计。

---

## 3. Python API 用法

```python
from modules.bio_analysis_function.submodules.index_filtering import (
    IndexFilterConfig,
    filter_by_index_fastq,
)

summary = filter_by_index_fastq(
    "merged_R1.fastq.gz",
    "sampleA_R1.fastq.gz",
    config=IndexFilterConfig(blacklist1=("ACGTACGT", "TTTTGGGG")),
)
print(summary.render())   # 被丢了多少、占比多少

# 允许一个错配（更宽松，但会误伤）
filter_by_index_fastq(
    "merged_R1.fq.gz", "out.fq.gz",
    config=IndexFilterConfig(blacklist1=("ACGTACGT",), threshold=1),
)
```

双端：R1 用名字里的**第一段** index 比 `blacklist1`，R2 用**最后一段**比
`blacklist2`，任一端命中就丢整对：

```python
filter_by_index_fastq(
    "merged_R1.fq.gz", "A_R1.fq.gz",
    read2_path="merged_R2.fq.gz", output2_path="A_R2.fq.gz",
    config=IndexFilterConfig(blacklist1=("ACGTACGT",), blacklist2=("TTTTGGGG",)),
)
```

**不改碱基、不改名字**，只决定去留。

### 原生实现

```python
from modules.bio_analysis_function.common.native import filter_by_index_fastq

summary = filter_by_index_fastq(
    "merged_R1.fq.gz", "A_R1.fq.gz", blacklist1=["ACGTACGT"]
)
```

原生侧直接收字符串序列（不走配置对象），其余一致；输出**逐字节相同**。

---

## 4. 参数

| 参数 | 默认 | fastp | 说明 |
| --- | --- | --- | --- |
| `blacklist1` | `()` | `--filter_by_index1`（文件，每行一个） | 与 R1 的第一段 index 比对 |
| `blacklist2` | `()` | `--filter_by_index2` | 与 R2 的最后一段 index 比对；单端忽略 |
| `threshold` | 0 | `--filter_by_index_threshold` | 允许的错配数 |
| `compress` | 跟随输入 | — | 输出是否 gzip |

两份黑名单都空时**不过滤任何 read**（上游同样如此）。

---

## 5. 输出解读

`IndexFilterSummary`：

| 字段 | 含义 |
| --- | --- |
| `total_reads` | 单端是 read 条数，双端是 **read 对数** |
| `filtered_reads` | 被丢掉的条数（同单位） |
| `kept_reads` / `filtered_rate` | 属性，留下的条数与丢弃比例 |
| `input_bases` / `output_bases` | 过滤前后的碱基数 |
| `paired` / `enabled` | 是否双端、黑名单是否生效 |

**怎么看**：`filtered_rate` 在**几个百分点**以内属正常（污染与 hopping 的量级）；
如果过半，几乎一定是黑名单写错了——最常见的是**填了本该保留的 index**。

> **一个必须知道的陷阱**：匹配时**只比两者中较短的长度**。所以名字里
> 取不到 index 的 read（返回空串）与**任何非空黑名单**都算命中，那一批会被全丢。
> 这是上游 `Filter::match` 的行为，本实现照抄，并有测试固定住。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `matches_blacklist` | `Filter::match` |
| `is_filtered` | `Filter::filterByIndex`（单端与双端两个重载） |
| `IndexFilterConfig.blacklist1/2` | `--filter_by_index1` / `--filter_by_index2` |
| `threshold` | `--filter_by_index_threshold` |
| `first_index` / `last_index`（在 `common/read_names.py`） | `Read::firstIndex` / `lastIndex` |

**三处照抄、不要"顺手改对"**：

1. **只比两者中较短的长度**（`for(s=0; s<len1 && s<len2; s++)`）。改成"比全长"
   会让短的黑名单项失效。
2. **提前退出后仍用 `diff <= threshold` 判断**——退出条件是 `diff > threshold`，
   所以这个判断是安全的（不是笔误）。
3. **单端只看 `blacklist1`**，`blacklist2` 在单端时完全忽略（上游如此）。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/index_filtering -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_index_filter.py -q
```

Python 侧 **20 项**：完全匹配、只比较短长度、空 target 的陷阱、空黑名单、
阈值边界与提前退出、单端用 R1 第一段 / 双端 R2 用最后一段、任一端命中即丢、
文件级（单端/双端成对丢/空黑名单不过滤/名字无 index 全丢/失败删半成品）。

原生层对拍 **8 项**：**输出逐字节相同** + 四个统计字段相同。

---

## 8. 已知限制

- **黑名单要用户自己准备**。本模块不做"从数据里发现异常 index"——那属于
  统计类算法（可以参考 [`overrepresented_sequences`](../overrepresented_sequences/overrepresented_sequences.md)
  的思路，但目前没做）。
- **只支持黑名单**，没有白名单模式（与上游一致）。
- **只看 index，不看序列**：index 相同的 read 之间不做任何一致性检查。
- **名字格式按 Illumina 风格解析**（照抄上游的扫法）。其他命名的数据可能
  取不到 index——而按上面那条陷阱，**取不到就会被全丢**，所以换平台时先小样本试跑。
- **串行实现**（原生层没有 `threads` 参数）：判定是常数时间的字符串比较，
  瓶颈在 I/O。
- **实际计算执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录

### 5.4.16.1 为什么独立成模块而不是并进 reads 过滤

最初考虑并进 [`read_filtering`](../read_filtering/read_filtering.md)（都是"判去留"），
但有两处不匹配：

1. **双端语义不同**。index 过滤在双端时要**成对判定**（R1 看第一段、R2 看最后一段），
   而 reads 过滤是单端算法（上游的 `passFilter` 逐条独立判定）。并进去要额外
   引入成对逻辑，会破坏它"与 `passFilter` 逐位对齐"的纯净性。
2. **在上游处理链里的位置不同**。index 过滤在**最前**（`passFilter` 之前），
   且失败的 read 不计入 filter result 的失败分类。并进去会让统计口径变复杂。

所以独立成模块，代价只是多一个入口——但它能独立回答问题（"哪些 index 被过滤了"），
也天然支持双端。

### 5.4.16.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**
- 开发目录：`modules/bio_analysis_function/submodules/index_filtering/`
- 文件构成：`algorithm.py`（黑名单匹配）、`runner.py`（文件级接口）、
  `index_filtering.md`、`tests/`
- 依赖：仅 Python 标准库与模块公共层（`fastq`、`read_names`）
- 原生实现：`common/native/src/index_filter.{h,cpp}`，导出 C ABI
  `bio_index_filter_fastq`（单端与双端共用入口；黑名单以「字符串数组 + 个数」传入）
- 原生库版本：0.15.0 → **0.16.0**

### 5.4.16.3 顺带做的一件事

`first_index` / `last_index` 原本只长在 UMI 提取里（C++ 侧在 `umi_process.cpp`
的匿名命名空间里）。本模块成为**第二个使用方**，于是按 `开发规则.md` 3.2 的
"公共能力至少出现两个真实使用方之后再提取"把它们上移到公共层：
Python 侧 `common/read_names.py`、原生侧 `src/read_names.h`（header-only inline）。
两份实现都改成了引用公共版本，没有留下重复代码。

### 5.4.16.4 待办

- 接通执行链路（Electron → 本地 Python / 原生层），目前只到契约层
- 与 fastp 二进制做端到端对拍
- 视需要补"从数据里发现异常 index"（例如按 index 前缀做频次统计），
  让用户不必手工准备黑名单
