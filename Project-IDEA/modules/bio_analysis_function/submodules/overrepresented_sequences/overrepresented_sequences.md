# overrepresented_sequences：过表达序列分析

找出数据里**异常高频的片段**——接头残留、污染、rRNA 之类的线索。
算法与 fastp 1.3.x 的 `Evaluator::computeOverRepSeq` +
`Stats::statRead`/`overRepPassed` 逐位对齐。

本子模块**可独立使用**（方法 id `bio-overrepresented-sequences`）。
**只需要一份 FASTQ**，不需要参考基因组、注释或数据库。

---

## 1. 它解决什么问题

正常的测序数据里，任何一段短序列出现的次数都由它的长度决定：
10bp 有 $4^{10}\approx100$ 万种可能，某一条出现几百次不奇怪；
但一条 40bp 的序列出现上千次就**不正常**了。

它分两步：

1. **找候选**：只在文件**开头一段**数据上（默认 151 万碱基）把
   10 / 20 / 40 / 100 / 150 bp 的片段各数一遍，按长度分档筛出高频项；
   再剔掉"是别人子串、而且自己并不比别人多十倍"的冗余项。
2. **量化**：在**整个文件**上按采样（默认每 100 条取 1 条）数这些候选
   出现了多少次、分别落在 read 的哪些位置。

两步的数据范围不同是**上游的刻意设计**：发现用不着看完整文件，量化要覆盖全文件才准。

> **与接头检测的区别**：接头检测（[`adapter_detection`](../../common/adapter_detection/adapter_detection.md)）
> 也看 k-mer 富集，但目标是**拼出一条接头序列**。本算法不限接头：
> 凡是异常高频的片段都报，是更一般的污染排查。两者互补。

> **默认检不出是正常的**：干净数据本来就不该有过表达序列。检出了再看是什么。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-overrepresented-sequences` |
| 名称 | 过表达序列分析 |
| 分类 | 序列预处理 |
| 执行方式 | `local` / 状态 `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_OVERREPRESENTED_SEQUENCES_METHOD` |

**输入**：reads 文件 + 采样率。
**输出**：不产出文件，只返回检出的序列与它们的计数、占比、位置分布。

> 上游把这一项做成了**默认关闭**的命令行开关。在本项目里它就是一个独立算法：
> 想查就跑它，不想查就不跑——不需要再加一个开关。

---

## 3. Python API 用法

```python
from modules.bio_analysis_function.submodules.overrepresented_sequences import (
    OverrepConfig,
    find_overrepresented_sequences,
)

summary = find_overrepresented_sequences("clean_R1.fastq.gz")

print(len(summary.sequences))          # 通常是 0
for item in summary.sequences:
    print(item.render())
    print(item.distribution[:20])      # 它在 read 前 20 个位置上的分布

# 想全查（不采样）：更准，也更慢
summary = find_overrepresented_sequences(
    "clean_R1.fastq.gz", config=OverrepConfig(sampling=1)
)
```

**只读不写**，不产出任何文件。内部会**读两遍文件**（先找候选、再量化）——
这是上游的分工决定的，不是实现缺陷。

### 原生实现

```python
from modules.bio_analysis_function.common.native import find_overrepresented_sequences

summary = find_overrepresented_sequences("clean_R1.fastq.gz", sampling=1)
for item in summary.sequences:
    print(item.sequence, item.count)
```

参数一一对应，返回的 `NativeOverrepSummary` 字段与本模块的 `OverrepSummary` 一致：
**检出条数、顺序、序列、计数、占比、位置分布全部相同**（有对拍测试守着）。

---

## 4. 参数

| 参数 | 默认 | fastp | 说明 |
| --- | --- | --- | --- |
| `sampling` | 20 | `--overrepresentation_sampling` | 量化阶段每多少条取一条；合法范围 1~10000 |
| `base_limit` | 1510000 | 写死 `151 * 10000` | 找候选时扫多少碱基 |
| `seq_length_sample` | 1000 | 写死 1000 | 看前多少条来确定"读长" |

---

## 5. 输出解读

`OverrepSummary` 的字段：

| 字段 | 含义 |
| --- | --- |
| `total_reads` / `total_bases` | 输入的条数与碱基数 |
| `sampled_reads` | 量化阶段实际采样的条数（没找到候选时是 0） |
| `seq_length` | 报告里位置分布的长度（= 前若干条里最长的 read） |
| `sampling` | 实际使用的采样率 |
| `sequences` | 检出结果，按"计数降序、长度升序、字典序"排列 |

每条 `OverrepresentedSequence`：

| 字段 | 含义 |
| --- | --- |
| `sequence` / `length` | 片段本身与长度 |
| `count` | **采样计数**（只数了 1/sampling 的 read） |
| `estimated_count` | 乘回采样率之后的估计总量 |
| `base_percent` | 占全部碱基的百分比 |
| `distribution` | 每个位置出现的次数，长度 = `seq_length` |

**怎么看**：先看 `sequence` 像不像接头（对照已知接头表，或直接用接头检测）；
再看 `distribution` —— 如果集中出现在某个固定位置，基本可以确定是接头或
建库引入的固定序列；如果散布在各处，更可能是污染或高丰度基因。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `collect_candidate_counts` / `filter_candidates` | `Evaluator::computeOverRepSeq` |
| `remove_substrings` | 同一函数末尾的"去子串" |
| `scan_sampled_read` | `Stats::statRead` 里的过表达统计段 |
| `passes_report_threshold` | `Stats::overRepPassed` |
| `candidate_steps` | 两处都写死的 `steps[5]` |

**五处照抄、不要"顺手改对"**：

1. **候选阈值与报告阈值是两套**，方向也不同：候选用 `count >= 阈值`，
   报告用 `sampling × count > 阈值`（严格大于）。
2. **长度分档是从上往下判的**：先看"长度 ≥ 读长 - 1"（阈值 3），
   再看 ≥100、≥40、≥20、≥10。所以一条 150bp 的片段走的是第一档。
3. **去子串用整数除法**比较倍数（`count / other < 10`），不是浮点。
4. **命中之后要多跳一个片段长度**，否则同一段 DNA 会被它在不同起点的
   重叠窗口重复计数。
5. **第五个片段长度是 `min(150, 读长 - 2)`**，可能与前面重复（读长短时），
   上游不去重——同一个长度会被算两遍、计数翻倍。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/overrepresented_sequences -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_overrep.py -q
```

Python 侧 **21 项**：候选阈值与分档边界、去子串规则（含整数除法的边界）、
报告阈值的严格大于与档位匹配、命中跳步、采集上限的"越限那条也处理"，
以及文件级的三条——干净数据一条不检出、掺入重复片段必须检出、
采样率按比例影响计数与推算总量。

原生层对拍 **7 项**：检出**条数、顺序、序列、计数、占比、位置分布全部相同**；
覆盖掺入重复、干净数据、采样率传递、压小扫描上限、空文件、中文路径、缺失输入。

> **测试构造里有个坑，值得记一笔**：掺进去的重复片段**必须是非周期的**。
> 用 `"ACGT" * 10` 这类周期串，它自己的 10bp 子串会出现几千次，而按去子串规则
> （短的比长的多出 10 倍以上就保留短的），那条子串反而会把长的剔掉——
> 最后什么都测不到。这一点在测试注释里写明了，免得后人重踩。

---

## 8. 已知限制

- **内存开销与扫描碱基数同量级**。第一步要对每个窗口建一个哈希项，
  默认扫 151 万碱基、五档长度加起来约 430 万个窗口，峰值内存可以到几百 MB。
  这是上游的取舍（换来的是一次扫描就能发现任意长度的异常片段）。
  嫌大可调小 `base_limit`。
- **候选只在文件开头找**。同样的序列如果只出现在文件后半段，不会被选中——
  这是上游的行为，不是本实现的偏差。
- **报告的是"片段"不是"整条 read"**，所以同一条 read 可能贡献多条检出。
- **不做接头识别**：报了"某 40bp 片段异常高频"不等于它就是接头，需要人判断
  （或对照接头检测的结果）。
- **串行实现**（原生层没有 `threads` 参数），与去重、统计同属"不写文件的统计类"。
- **实际计算执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录

### 5.4.13.1 选择依据

1. **它是 fastp 预处理线里最后一块"诊断"能力**。走到这一步，数据"改"的算法
   （剪切、裁剪、过滤、去重）和"看"的算法（质量统计、插入片段分布）都齐了，
   只差"数据里有没有不该有的东西"这一问——过表达序列就是回答它的。
2. **它与接头检测同源但目标不同**：`adapter_detection` 只在已知接头表与 k-mer
   富集两条路上找**接头**；本算法不限类型，凡是异常高频的都报。
   上游也是这么分开的（`Evaluator` 里两件事各有一个函数）。
3. **它的实现有一处上游特有的"两遍不同范围"设计**（开头一段找候选、全文件量化），
   值得如实记录下来，免得后人以为是实现缺陷而"顺手改成一致"。

### 5.4.13.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**
- 开发目录：`modules/bio_analysis_function/submodules/overrepresented_sequences/`
- 文件构成：`algorithm.py`（纯计数与筛选）、`runner.py`（两遍扫描）、
  `overrepresented_sequences.md`、`tests/`
- 依赖：仅 Python 标准库与模块公共层（`fastq`）
- 原生实现：`common/native/src/overrep.{h,cpp}`，导出 C ABI
  `bio_overrep_*` 一组（**不透明句柄**——检出条数取决于数据，塞不进定长结构体，
  理由与 reads 统计相同）
- 原生库版本：0.13.0 → **0.14.0**

### 5.4.13.3 待办

- 接通执行链路（Electron → 本地 Python / 原生层），目前只到契约层
- 与 fastp 二进制做端到端对拍
- 视需要把检出的序列与已知接头表做交叉标注（"这条看起来是接头"），
  让报告更可直接行动
