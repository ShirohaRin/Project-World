# read_stats：reads 质量统计

统计一份 FASTQ 的质量画像：Q20/Q30/Q40 碱基比例、GC 含量、读长分布、
**按测序位置**的质量曲线与碱基含量曲线、质量值分布、1024 个 5-mer 的频次。
算法与 fastp 1.3.x 的 `Stats`（`src/stats.cpp`）逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-read-stats`）。**只需要一份 FASTQ 本身就能运行**——不需要参考基因组、
注释文件或数据库，也不要求上游先跑过别的算法。代码层面同样可以单独导入，
不依赖本模块的其他子模块，也不依赖任何外部程序。

---

## 1. 它解决什么问题

前面的算法都在**改数据**：切质量、剪 poly、裁接头、过滤、去重。
改之前得先知道**该怎么改**，改之后得知道**改好了没有**——这就是本算法的位置。

它只看、不改：**不产出任何 FASTQ**，只给一份统计。

| 维度 | 粒度 | 回答什么问题 |
| --- | --- | --- |
| 条数 / 碱基数 / 平均读长 / 读长分布 | 全局 | 数据量够不够、剪切是不是砍过头 |
| Q20 / Q30 / Q40 碱基比例 | 全局 | 整体质量 |
| GC 含量 | 全局 | 物种特征、有没有污染线索 |
| **按位置**的质量均值 | 每个 cycle | **质量从第几个循环开始塌** |
| **按位置**的各碱基质量 | 每个 cycle | 某种碱基是不是特别差 |
| **按位置**的各碱基占比 | 每个 cycle | 组成是否均衡、有无异常富集 |
| 质量值分布 | 全局 | 质量是整体偏低，还是两头分化 |
| 5-mer 频次（1024 桶） | 全局 | 序列内容异常的线索 |

加粗的那两行是这份报告的核心。**只看全局平均质量，看不出质量是从第几个循环
开始塌的**，而那正是决定"要不要做质量剪切、从哪一端切"的依据。所以本算法
把每个位置的质量都留着，而不是只报一个平均数。

> **它不重复别人算过的东西**。重复率归
> [`deduplication`](../deduplication/deduplication.md)，接头归
> [`adapter_detection`](../../common/adapter_detection/adapter_detection.md)，
> 过滤结果归 [`read_filtering`](../read_filtering/read_filtering.md)。
> 本模块只管"碱基与质量本身长什么样"。

> **报告有两种形态。** 算法层给出结构化数据（曲线、分布、频次）供前端画图；
> 同时也提供一份**自包含的 HTML**（`render_html`，内嵌样式与 SVG、不执行脚本）
> 供离线查看与存档。上游那份**内嵌 JS** 的报告本实现没有照做——理由见
> `report.py` 的模块说明（静态文件更安全、能存成矢量 PDF、测试不必跑 JS 引擎）。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-read-stats` |
| 名称 | reads 质量统计 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_READ_STATS_METHOD` |

**输入**：只有一项——reads 文件（`.fq` / `.fastq`，可 gzip）。

> 双端数据请对 R1 与 R2 **各跑一次**。上游也是分开统计的：两份 read 的读长与
> 质量特征本来就该分开看，合并成一个平均值只会把两边的特征抹平。

**输出**：不产出新的测序文件，只返回结构化统计（字段见第 5 节）。

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**
> （与其余预处理算法的现状一致）。

---

## 3. Python API 用法

### 文件进、统计出

```python
from modules.bio_analysis_function.submodules.read_stats import stat_fastq

summary = stat_fastq("clean_R1.fastq.gz")

print(summary.total_reads, summary.total_bases)
print(f"Q30 {summary.q30_rate:.1%}  GC {summary.gc_content:.1%}")
print(summary.render())                     # 只列结论性的几行

mean = summary.quality_curves["mean"]       # 每个位置的平均质量
print(f"质量最低在第 {mean.index(min(mean)) + 1} 个 cycle")
```

**只读不写**：跑完之后目录里还是原来那些文件。

### 序列直接喂

不想走文件时用累加器（`add` 是热路径，`summarize` 才算曲线）：

```python
from modules.bio_analysis_function.submodules.read_stats import ReadStatsCollector

collector = ReadStatsCollector()
collector.add(b"ACGTACGT", b"IIIIIIII")
collector.add(b"ACGT", b"JJJJ")
summary = collector.summarize()
```

`add(序列, 质量)` 只做累加，可以喂进来几十万条之后**只汇总一次**。

### 报告序列化

```python
from modules.bio_analysis_function.submodules.read_stats import report_to_json

text = report_to_json(summary, indent=2)    # 返回 JSON 文本，不落盘
```

**为什么不自带文件输出**：这份 JSON 的消费者是前端（要画图）与用户（要存档），
两者对"写到哪里、叫什么名字"各有主张，塞进算法里只会打架。函数给出可复现的
文本，落盘交给调用方。

JSON 里除了 1024 个 5-mer 计数，还给一份 `kmer_top`（计数最高的若干个 5-mer
及其碱基串）——1024 个数字直接看没法看。

### 可视化报告（自包含 HTML）

```python
from modules.bio_analysis_function.submodules.read_stats import render_html

html = render_html(summary, title="样本 A · reads 质量报告")
Path("report.html").write_text(html, encoding="utf-8")
```

产出的是一份**能离线打开的静态文件**：内嵌样式与 **SVG 图**（不是 JS 画图），
不引用任何外部资源、不执行任何脚本。可以直接双击看、当邮件附件发、
在浏览器里"另存为 PDF"（曲线是矢量的，不会糊）。

流程层要补进自己的信息（处理前后读数、重复率、插入片段峰值……）时，
用两个可选参数塞进来，报告层不必知道流程长什么样：

```python
html = render_html(
    summary,
    extra_cards=[("重复率", "12.5%"), ("峰值片段长度", "316 bp")],
    extra_sections=[("处理前后对比", "<p>...</p>")],
)
```

**默认不写生成时间**，所以同一份数据每次渲染的结果**逐字节相同**——
报告本身也是可复现的产物。要时间戳就显式传 `generated_at`。

### 原生实现（跑大样本用这个）

Python 版同时是**可逐位对拍的规格**；真实样本用原生层，它位于模块公共层
`common/native/`：

```python
from modules.bio_analysis_function.common.native import read_stats_fastq

stats = read_stats_fastq("clean_R1.fastq.gz")
print(stats.q30_rate, stats.gc_content, len(stats.kmer_counts))
```

返回的 `NativeReadStats` 字段与 `ReadStatsSummary` 一致，两侧的统计与曲线
**逐点完全相等**（有对拍测试守着）。

---

## 4. 参数

**本算法没有参数。** 这不是偷懒：统计的口径应该由数据决定，不该由用户调。
尤其 Q20 / Q30 的判定阈值，上游写死成 `'5'`（Q20）与 `'?'`（Q30）且不给用户改，
本实现照抄——报告要能横向比较，阈值就必须是同一个。

---

## 5. 输出解读

`ReadStatsSummary` 的字段：

| 字段 | 含义 |
| --- | --- |
| `total_reads` / `total_bases` | 条数与碱基总数 |
| `mean_length` | 平均读长（**整数除法**，与上游一致） |
| `length_counts` | `{读长: 条数}`。上游不输出这一项，是本实现新增——平均读长会掩盖"一半很长一半很短" |
| `q20_bases` / `q30_bases` / `q40_bases` | 达到各档的碱基**个数**（Q30 也计入 Q20） |
| `q20_rate` / `q30_rate` / `q40_rate` | 属性，各自占总碱基的比例 |
| `gc_bases` / `gc_content` | G 与 C 的碱基总数与占比 |
| `cycles` | 曲线的长度 = 第一个"所有 read 都结束"的位置 |
| `quality_curves` | `{"mean": (...), "A": (...), "T": (...), "C": (...), "G": (...), "N": (...)}`，每个位置一个值 |
| `content_curves` | `{"A","T","C","G","N","GC"}`，每个位置上该碱基（或 GC 之和）的占比 |
| `quality_histogram` | `{Phred: 碱基个数}`，只含非零项 |
| `kmer_counts` | 1024 个 5-mer 的计数，下标即编码（A=0、T=1、C=2、G=3，高位在前） |

**怎么用这份报告判断**：

- `q30_rate` 低于 0.8（常见验收线）说明整体质量偏弱；
- 看 `quality_curves["mean"]` 的形状：**平着下降**是正常的测序衰减，
  **某一位突然掉下去**往往是那个循环的化学问题；
- 看 `content_curves["N"]`：某个位置 N 突然变多，通常是质量塌方的**先兆**；
- 看 `content_curves["A"/"T"/"C"/"G"]`：四条线如果严重不平行，
  可能是污染或建库偏好；
- `length_counts` 只有一两个值说明读长整齐；跨度大则要回头看是不是剪切过头。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `ReadStatsCollector.add` | `Stats::statRead` |
| `ReadStatsCollector.summarize` | `Stats::summarize` |
| `kmer_name` | `Stats::kmer3` / `kmer2` |
| `ReadStatsSummary` | `Stats::reportJson` 里的字段 |
| `report_to_json` | `JsonReporter::report`（只取其中属于 Stats 的那几节） |
| （本实现新增）`length_counts` | 无 |
| （本项目由前端负责） | `Stats::reportHtml*` 与 `HtmlReporter` |

**四处口径按上游原样保留**，不要"顺手改对"：

1. **Q20 / Q30 的阈值写死**（`'5'` 与 `'?'`），不做成参数。
2. **按"碱基字符的低 3 位"分桶**（`base & 0x07`）。Python 的整数按位与是
   任意精度，所以这一条在 Python 侧也要老老实实 `& 0x07`。**后果是大小写自动
   合并**（ASCII 大小写差 32，是 8 的倍数），A/C/T/G/N 恰好落在 1/3/4/7/6。
   这不是"顺手优化"，因为**分桶结果会直接进入曲线数组**，换个分法曲线就对不上。
3. **某位置没有该碱基时，质量曲线取整体均值兜底**，而不是记 0——否则 N 这类
   稀疏碱基的曲线会在没有数据的位置凭空掉到底。
4. **k-mer 只认大写 ACGT**（上游 `BASE2VAL` 对小写也返回 -1），且**遇到 `N`
   之后必须从头重算 5 个碱基**，不能沿用平移过的中间值。

另有三处整数口径也照抄：平均读长用整数除法；`cycles` 是"第一个所有 read 都
结束的位置"；质量直方图内部按**字符码**下标（对外报告时转成 Phred 值，
见第 7 节）。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/read_stats -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_stats.py -q
```

Python 侧 **47 项**，按文件分三组：

- **`tests/test_algorithm.py`（22 项）**：Q20/Q30 的边界（恰好等于阈值算达标）、
  Q40 的取值区间（Q39 不算）、低 3 位分桶与大小写合并、某位置缺该碱基时的均值兜底、
  读长参差时"短 read 不拉平长 read"、5-mer 的三个分支（增量更新 / 遇 N 重算 /
  N 之后恢复）、小写字母不计入 k-mer、空输入与空 read。
- **`tests/test_runner.py`（13 项）**：文件级接口与逐条累加结果一致、
  真实数据可读、空文件、gzip、缺失与损坏输入、只读不写、JSON 可解析且不丢字段、
  `kmer_top` 的排序与命名。
- **`tests/test_report.py`（12 项）**：HTML 报告的四条硬性质——**不执行脚本**
  （没有 `<script>`）、**不引用外部资源**（没有 URL、没有 `src=`）、
  **同一份数据渲染两次逐字节相同**、**图上的点数与数据对得上**；
  外加用户文本一律转义、额外卡片/整节能合并、5-mer 节可关且行数受限、
  读长种类过多时截断并给出说明、空数据与全 N 数据都能渲染。

原生层对拍 **9 项**（`common/native/tests/test_abi_stats.py`）：与 Python 侧
**逐字段对拍**，并且 **11 条曲线要求逐点完全相等**（`double` 的 `==`，不留容差）。
两侧用的是同一套运算顺序（同样的整数累加、同样的除法），所以这个要求是能达到的——
达不到就说明某一侧的某个累加或某个分桶写错了。覆盖真实数据、800 条读长参差
且质量随位置衰减的合成数据、整条全 N、空文件、gzip、中文路径、缺失与损坏输入。

**对拍第一次就抓到一个真错**：原生层把质量直方图按**字符码**（0~127）输出，
Python 侧按 **Phred 值**（0~93）输出，两侧的键整整差 33。这是只在"逐字段对拍"
下才会暴露的错误——只比总数、比曲线的话，它会一直潜伏到前端画图时才被发现。
C ABI 的文档写的是 Phred，所以修的是原生侧。

---

## 8. 已知限制

- **不检测过表达序列**——那是独立的一块，已由
  [`overrepresented_sequences`](../overrepresented_sequences/overrepresented_sequences.md)
  实现（它要做采样预扫描，与接头检测同源，不适合塞进本模块）。
- **不统计双端插入片段长度分布**——同样独立成模块，见
  [`insert_size_distribution`](../insert_size_distribution/insert_size_distribution.md)；
  本模块只处理单份文件，那项要逐对做 overlap 分析。
- **不生成 HTML**，只给结构化数据（见第 1 节的分工说明）。
- **5-mer 表有 1024 项**，直接看没有意义；报告里附了 `kmer_top`，
  但真正的诊断（富集热图）要靠前端画。
- **平均读长是整数除法**，与上游一致；想要精确的平均值请自己算
  （用 `length_counts` 加权重）。
- **双端要跑两次**，本模块没有"两个文件一起统计"的入口。
- **实际计算执行链路尚未接通**。广场上的入口目前只到契约层。

---

## 附：开发记录

### 5.4.12.1 选择依据

1. **它是预处理线的"眼睛"**。前面十个算法都在改数据，但没有一个回答
   "这批数据该不该这么改、改完好了没有"。没有统计，预处理就只是照着默认参数
   盲跑——这在产品上是不可接受的：用户需要一个能自己判断的依据。
2. **它把"按位置"这件事带进来了**。此前所有算法的判据都是"单条 read 自身"
   或"read 与 read 之间"的，这是第一个"按 read 内部的位置"看问题的算法，
   数据结构（每个 cycle 一组计数）也是一次新的尝试。
3. **上游源码可逐行对照**（`Stats` 一个类），能做到与前面算法一样的逐位对齐。
4. 它的产物形态（大块变长结构化数据）逼出了 `common/native` 的**第二种 ABI 形态**
   （不透明累加器），这一点对后续的统计类算法都有用。

### 5.4.12.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**——核心统计、文件级接口、JSON 序列化、
  子模块说明文档、算法广场入口、单元测试与真实数据验证齐备，
  且已完成 C++ 移植并与 Python 版逐点对拍
- 开发目录：`modules/bio_analysis_function/submodules/read_stats/`
- 文件构成：`algorithm.py`（累加与汇总）、`runner.py`（文件级接口、
  JSON 序列化、可读报告）、`read_stats.md`、`tests/`
- 依赖：**仅 Python 标准库**（`dataclasses`、`json`、`pathlib`）与模块公共层
- 算法广场入口：已在 `IDEA Assistant Code/src/compute.ts` 登记
  `bio-read-stats`（分类"序列预处理"，`local`，`beta`）。**当前只到契约层**
- 原生实现：`common/native/src/read_stats.{h,cpp}`，导出 `bio_stats_*` 一组
  C ABI，ctypes 封装为 `common.native.read_stats_fastq`；对拍测试 9 项
- 原生库版本：0.12.0 → **0.13.0**

### 5.4.12.3 工程实现

**累加与汇总分开。** `add` 是热路径（每条 read 一遍逐碱基循环），只做加法；
`summarize` 才把原始计数整成曲线，代价与数据量无关、与读长成正比。一批数据
只需要汇总一次，累加要做几千万次——混在一起就是白白多算。原生侧同理，
并且 `add` 会把上一次的汇总结果置为失效，这样"多次扫描、最后统一查询"
不会拿着第一份的曲线回答第二份的问题。

**每个 cycle 的计数放在一条连续的数组里。** 8 个桶各持一条与容量等长的数组，
容量不够时按"多给一截"扩容（与上游同样）。Python 侧用 `list`，原生侧用
`std::vector<int64_t>`，两者扩容策略一致，因此曲线长度不会漂移。

**C ABI 换了一种形态。** 其他算法是"一次调用、填一个结果结构体"，本算法不行：
产物是 11 条与读长同长的曲线、1024 个 5-mer 桶、94 项直方图、读长分布，
**长度在运行期才知道**。于是改成不透明累加器：
`bio_stats_create` → `bio_stats_scan_fastq`（可多次，把多份文件累加）→
按需查询 → `bio_stats_destroy`。句柄由创建方销毁，跨 ABI 不转移所有权、
不暴露内部布局；查询函数在容量不够时返回 `BIO_ERR_OUTPUT` 并回填所需长度。
Python 侧用一个函数把"创建 → 扫描 → 逐项取回 → 销毁"包起来，
调用方看到的仍是"进文件、出统计"。

**质量直方图对内按字符码、对外按 Phred。** 内部沿用上游的字符码下标（0~127），
但 C ABI 输出的是 **Phred 值**（0~93）下标——字符码是 Phred+33 的表示细节，
不该泄漏给调用方。这一处转换漏掉时，两侧的直方图键会整整差 33（见第 7 节）。

**JSON 里的 5-mer 附了可读形式。** 1024 个整数直接给出去，没人能从中看出
"A 特别多"。所以除数组外还给一份 `kmer_top`：计数最高的若干个 5-mer 及其
碱基串。

### 5.4.12.4 验证记录

Python 侧 **47 项**、原生对拍 **9 项**，全部通过。

**曲线逐点对拍是本模块最严的一条。** 曲线有几百个点，只要有一个位置的分桶、
累加或除法的写法与 Python 侧不同，就会有一个点不相等。把"相等"定为 `double`
的 `==`（而不是容差比较），是为了让这条测试真的能抓到错——用容差比较的话，
"某一侧少加了一个碱基"这种错会被 1e-9 的差异抹过去。

**为什么直方图那条错误值得记一笔**：它是两侧**各自都自洽**的错误——
原生侧按字符码输出，自己不觉得有问题；Python 侧按 Phred 输出，也没问题。
只有把两侧的字典放在一起比才暴露。这提醒了一件事：对拍要走**完整字段**，
不能只挑几个"看起来重要"的数字比。

**HTML 报告是靠肉眼复查发现问题的。** 12 项单元测试全绿的情况下，
把报告渲染出来用浏览器逐元素量几何位置，仍然查出三处真实缺陷：
图例画在绘图区**内部**（质量图有 135 个数据点落在图例包围盒里、碱基含量图 12 个）、
y 轴标题与顶部刻度数字**重叠约 18–22px**、5-mer 那节的标题容易被读成"最多 5 个"。
三处都改掉并用同一套量化方法复验（落入图例的点数 0/0、标题与刻度重叠面积为 0）。
**教训**：结构性的断言（有没有脚本、点数对不对）替代不了"画出来看一眼"——
版面的问题只能在版面上发现。

### 5.4.12.5 待办

- 接通 `bio-read-stats` 的实际执行链路（Electron → 本地 Python / 原生层），
  目前只到契约层
- 与 fastp 二进制做端到端对拍（当前只与 Python 参考实现对拍）
- **过表达序列分析**：上游在 `Evaluator` 里做采样预扫描，统计 10/20/40/100bp
  片段的高频项，再统计它们在各位置上的分布。它与已有的接头检测同源，
  是独立的一块
- **双端插入片段长度分布**：`paired_overlap` 已经能算出每个片段的长度，
  但没有汇总成直方图；上游的双端报告里有这一项
- 视需要补"多份文件一起统计"的入口（原生 ABI 已经支持多次扫描累加，
  只是子模块层没有暴露）
