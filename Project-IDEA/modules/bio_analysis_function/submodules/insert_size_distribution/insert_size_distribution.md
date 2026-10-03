# insert_size_distribution：双端插入片段长度分布

靠两条 read 的重叠关系倒推每一对的插入片段长度，汇成直方图并找出峰值。
算法与 fastp 1.3.x 的 `PairEndProcessor::statInsertSize` 逐位对齐。

本子模块**可独立使用**（方法 id `bio-insert-size`）。**只需要一对原始的
R1/R2 FASTQ**——不需要参考基因组、注释或数据库，也不需要先跑别的算法。

---

## 1. 它解决什么问题

"这个文库的插入片段有多大、整齐不整齐。"

- **峰值**（出现最多的那个长度）是建库是否正常最直接的指标；
- 分布**太宽**说明片段筛选不干净；
- **多个峰**说明混了两次建库。

片段长度不是测出来的，是**推算**出来的。规则完全照抄上游：

| 情况 | 片段长度 |
| --- | --- |
| 两条 read 只在中间重叠（`offset > 0`） | `len(R1) + len(R2) - 重叠长度` |
| 两端读穿、读进了接头（`offset <= 0`） | 重叠长度本身就是片段长度 |
| 找不到重叠 | **判不出来**，进"判不出"桶 |

> **"判不出"要如实看待。** 重叠判定要求最短重叠 30bp、错配不超过限制，
> 片段太长（两条 read 完全不搭界）或数据太脏都会判不出来。
> 所以报告里同时给"能判出片段长度的比例"——它低的时候，峰值也不可信。

重叠判定本身归公共层的 [`paired_overlap`](../../common/paired_overlap/paired_overlap.md)，
本模块只负责汇成直方图。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-insert-size` |
| 名称 | 双端插入片段长度分布 |
| 分类 | 序列预处理 |
| 执行方式 | `local` / 状态 `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_INSERT_SIZE_METHOD` |

**输入**：R1、R2（都必填）+ 可调的直方图上限与重叠判定参数。
**输出**：不产出文件，只返回统计（直方图、峰值、判不出的条数）。

---

## 3. Python API 用法

```python
from modules.bio_analysis_function.submodules.insert_size_distribution import (
    InsertSizeConfig,
    analyze_insert_size,
)

summary = analyze_insert_size("R1.fastq.gz", "R2.fastq.gz")
print(summary.peak_size, f"{summary.overlap_rate:.1%}")
print(summary.render())

# 只要某一段长度的分布：
counts = summary.histogram[:summary.max_size]   # 下标即片段长度
print(counts[150:170])

# 想统计得更宽松（更多对能判出片段长度）：
summary = analyze_insert_size(
    "R1.fq", "R2.fq",
    overlap=OverlapConfig(require=20, diff_limit=8),
)
```

**只读不写**，不产出任何测序文件。两份输入的记录数必须一致，不一致直接报错。

### 原生实现

```python
from modules.bio_analysis_function.common.native import analyze_insert_size

summary = analyze_insert_size("R1.fastq.gz", "R2.fastq.gz")
print(summary.peak_size, len(summary.histogram))
```

参数一一对应，返回的 `NativeInsertSizeSummary` 字段与本模块的 `InsertSizeSummary`
一致，两侧的直方图**逐桶相等**（有对拍测试守着）。

---

## 4. 参数

| 参数 | 默认 | fastp | 说明 |
| --- | --- | --- | --- |
| `max_size` | 512 | `insertSizeMax`（写死 512） | 直方图上限；越界与判不出的并入同一个桶 |
| `overlap.require` | 30 | `--overlap_require` | 最短重叠长度 |
| `overlap.diff_limit` | 5 | `--overlap_diff_limit` | 最大错配数 |
| `overlap.diff_percent_limit` | 0.2 | `--overlap_diff_percent_limit` | 错配比例上限 |
| `overlap.allow_gap` | `False` | 上游默认关闭 | 是否允许 1 个插入/缺失 |

`require` 调高会更严格，代价是判不出片段长度的对变多——这一点在报告里能看到。

---

## 5. 输出解读

`InsertSizeSummary` 的字段：

| 字段 | 含义 |
| --- | --- |
| `total_pairs` | 输入 read 对数 |
| `overlapped_pairs` / `overlap_rate` | 能判出片段长度的对数与占比 |
| `max_size` | 直方图上限 |
| `histogram` | 长度 = `max_size + 1`，**下标即片段长度**，最后一项是溢出桶 |
| `unknown_pairs` | 溢出桶里的条数（判不出 + 超上限） |
| `peak_size` | 峰值片段长度；**只在上限之内找** |

恒等式：`total_pairs = sum(histogram[:max_size]) + histogram[max_size]`。

**怎么判断**：先看 `overlap_rate`。低于八九成说明重叠判定太严（或数据太脏），
此时峰值只能当参考。再看直方图的形状——一个窄峰是正常的文库，
宽肩或双峰就该回头查建库了。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `insert_size_from` | `PairEndProcessor::statInsertSize` 的两个分支 |
| `InsertSizeHistogram._bucket_of` | 同一处的"超上限并入溢出桶" |
| `_find_peak` | `PairEndProcessor::getPeakInsertSize` |
| `max_size` | `Options::insertSizeMax` |

**三处照抄、不要"顺手改对"**：

1. **判不出与超上限共用一个桶**（下标 = 上限）。峰值只在上限之内找，所以混在
   一起不影响峰值；分开反而是自造口径。
2. **恰好等于上限时留在上限那个桶里**（判断用严格大于，不是 ≥）。
3. **峰值并列时取较小的长度**（上游用严格大于比较，先遇到的胜出）。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/insert_size_distribution -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_insize.py -q
```

Python 侧 **18 项**：桶的分配（两个分支、判不出、超上限、恰好等于上限）、
峰值判定（含并列取小、忽略溢出桶）、空输入、参数校验，以及文件级全链路
（按模板造出已知片段长度的 read 对，断言峰值落在预期位置）。

原生层对拍 **7 项**：直方图**逐桶相等**、峰值与总数相同；覆盖真实几何、
掺入判不出的对、把上限压小的情形、收紧重叠要求、中文路径、缺失输入与
两份记录数不一致。

---

## 8. 已知限制

- **片段长度是推算的**，判不出来就没有值。数据越脏、片段越长，判不出的越多。
- **直方图上限固定 512**（与上游一致）。长片段文库（如 600bp）会挤在溢出桶里，
  需要的话调 `max_size`。
- **只统计长度分布，不看每条 read 的质量**。想看质量请用
  [`read_stats`](../read_stats/read_stats.md)。
- **串行实现**（原生层没有 `threads` 参数）：它不产出文件，只有汇总，
  与去重同属"不写文件的统计类"。后续可以补上并行。
- **实际计算执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录

### 5.4.14.1 选择依据

1. **它是双端数据独有的质控项**。前面十几个算法里，只有双端合并/裁接头/校正
   用到了"两条 read 的关系"，但都没有把它**汇总成一个物种级指标**。
   插入片段长度分布是双端文库最直接的体检指标。
2. **它的计算件已经就位**：`common/paired_overlap` 早就给出了每对的 `insert_size`，
   缺的只是"汇成直方图 + 找峰值"这一层。属于低成本、高价值的补充。
3. 上游的对应实现（`statInsertSize` + `getPeakInsertSize`）短小且口径明确，
   适合做逐位对齐。

### 5.4.14.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**
- 开发目录：`modules/bio_analysis_function/submodules/insert_size_distribution/`
- 文件构成：`algorithm.py`（直方图与峰值）、`runner.py`（文件级接口）、
  `insert_size_distribution.md`、`tests/`
- 依赖：仅 Python 标准库与模块公共层（`fastq`、`paired_overlap`）
- 原生实现：`common/native/src/insert_size.{h,cpp}`，导出 C ABI
  `bio_insert_size_fastq`；直方图由调用方分配（长度由 `max_size` 决定，
  调用方预先知道），因此不必用句柄那一套
- 原生库版本：0.13.0 → **0.14.0**

### 5.4.14.3 待办

- 接通执行链路（Electron → 本地 Python / 原生层），目前只到契约层
- 与 fastp 二进制做端到端对拍
- 视需要给原生实现加读取端并行（当前串行）
