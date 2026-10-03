# workflow：fastp 预处理工作流

把生物模块预处理线已有的开发项按上游 fastp 的顺序串成一条完整流程：
**一次读入、逐条走完整条链、分批写出**，输出处理后的 FASTQ 与一份汇总报告。
每一步调的都是对应算法**内部的同一份实现**，因此每一步的口径与单独调用它时完全一致。

它回答的问题是：**这份原始数据经过标准预处理之后变成什么样、中间掉了多少。**

---

## 1. 它是什么、为什么住在 `modules/` 下面

预处理线的十六个开发项各自解决一个问题（切质量、剪 poly、裁接头、过滤、去重……），
但真实用法从来不是"每次只跑一个"——拿到一批原始数据，用户要的是把标准流程整条跑完。
把这条流程拼起来有两种做法：

| 做法 | 代价 |
| --- | --- |
| 上层逐个调用既有算法 | 每个算法各读一遍输入、各写一遍输出。十六步就是十几倍的 I/O，而数据动辄几十 GB |
| **一条单遍流程**（本模块） | 输入只读一遍、输出只写一遍，中间结果在内存里从一步递给下一步 |

**它不产生新方法，也不属于任何一个算法模块**：它没有引入任何新的判定（不是算法），
也不是别的算法的一步（不是工具）；它是把多个算法的**调用顺序**固定下来，并处理它们之间
的数据接力。因此它住在 `modules/workflow/`，与 `modules/browser/`、`modules/automation/`、
`modules/realtime_voice/` 同属**能力模块**，而不是生物模块内部的一层。

> **2026-09-23 迁移**：它原先放在 `modules/bio_analysis_function/workflow/`，按"编排是生物
> 模块的第三类"定位。与白羽奈绪讨论后改判为能力模块——工作流后续会深度嵌入整个 IDEA
> 项目，还可能出现结合模型的工作流，这与"算法模块内部的分层"是两件事。

### 1.1 跨模块依赖（显式声明）

本模块**不实现任何算法**，全部计算依赖生物模块 `modules/bio_analysis_function`：

| 依赖 | 用途 |
| --- | --- |
| `bio_analysis_function.common.native.abi` | 原生库的 ctypes 封装、各步的 spec 类型（`CutSpec` / `PolySpec` / `FilterSpec` / `OverlapSpec` / `UmiSpec`） |
| `bio_analysis_function.common.svg_report` | 报告的绘图原语（与 `read_stats` 的报告共用一套） |
| `bio_analysis_function/common/native/lib/bio_native.dll` | 真正的计算：阶段 A 的二色判定/接头检测/reads 统计，以及阶段 B 的整条链 |

所以 **workflow 不能脱离生物模块单独发布**：打包时两者一起进 `resources/modules/`，
`wrapper.py` 也按"`modules/` 的上级目录进 `sys.path`"来导入，开发态与打包态同一套相对结构。

---

## 2. 两段式结构（照抄上游 fastp）

上游 fastp 在真正处理之前先跑一遍 `Evaluator` 做决策（读长、条数、是否二色测序、
自动检测接头、过表达序列），结论写回 `Options`，然后才进单遍主循环。本模块沿用这个
两段式，但把分界画在语言边界上：

| 阶段 | 在哪 | 做什么 |
| --- | --- | --- |
| **A · 预扫描** | Python 侧（`runner.py`） | 二色系统判定、接头检测、按文件数分卷时精确数一遍总条数。结论汇总成 `ScanReport` |
| **B · 主处理** | 一次原生调用（`bio_run_workflow`） | 一次读入、逐条走完整条链、分批写出 |

**阶段 A 为什么不放进原生入口。** 它做的三件事，每一件本来就是生物模块里**独立可跑的
算法/工具**：

- 二色判定 → `two_color`（上游 `Evaluator::isTwoColorSystem`）；
- 接头检测 → `adapter_detection`（工具，直接复用已有实现）；
- 总条数 → `read_stats`（算法，直接复用已有实现）。

让它们各自跑、结论传进主处理，比在主处理里再包一层更好——**中间结论因此对调用方
可见**：报告要如实写出"用了什么参数、为什么这么用"（检测到的接头、是不是二色系统、
polyG 开没开），这些都得留在上层。另外阶段 A 偏 I/O 采样，与阶段 B 的逐条计算没有
共享状态，合在一起只会让原生入口膨胀。

上游之所以把阶段 A 合在 `main.cpp` 里，是因为它只有命令行一个入口；本模块的调用方是
Python 编排层，有地方放这段逻辑。

**阶段 B 的输入是已经定下来的配置**（含检出的接头序列），主循环里不再回头做任何决策
——这一点与上游一致。

---

## 3. 三端分工与逐步顺序

主处理是一条"读取 → 计算 → 写出"三段并行的流水线（`workflow_pipeline.h`），
但三端**各被允许做什么**与另外两条流水线不同，这是正确性的关键：

| 端 | 线程 | 逐步顺序 |
| --- | --- | --- |
| 读取端 | 1 个，**按文件顺序** | 过滤前统计 → 按 index 过滤 → 去重判定 |
| 计算端 | N 个（并行 worker） | 规范化 → UMI → 首尾固定修剪 + 滑窗质量剪切 → polyG →〔双端：overlap 分析 → 碱基校正 → 按 overlap 裁接头（判不出则退化为按给定序列匹配）→ 接头二聚体判定〕→ 重叠区输出 → polyX → 限长截断 →〔合并模式：重算 overlap 并合并〕→ reads 过滤判定 |
| 写出端 | 1 个，**按输入顺序** | 过滤后统计 → 主输出 / 落单 / 失败 / 合并 / 重叠区 五路分流写出 → 分卷 |

顺序本身就是要照抄的东西，三处容易记错：

- **去重判定的对象是原始 read**（还没修剪），所以它在链最前面；
- **polyG 在质量剪切之后、接头裁剪之前；polyX 在接头裁剪之后**；
- **overlap 分析一次算好、多处复用，但合并之前必须重算**。

### 3.1 为什么去重在读取端

`DuplicateDetector` 的位图跨 read 累积、"谁先出现谁不算重复"，因此判定顺序会改变
结果。上游在 worker 线程里带锁判重，于是**同一份数据换个线程数，它报出的重复率就会
变**；本模块守的是"线程数不影响结果"这条纪律（见
[native.md](../bio_analysis_function/common/native/native.md) 5.4.0.5），
所以这种有状态、顺序相关的判定只能放在单线程、按文件顺序执行的读取端。

**过滤前统计一并放在读取端**，是为了对齐上游口径：上游在去重与按 index 过滤**之前**
就 `statRead`，被判重、被 index 命中的 read 也要计入"before filtering"。若把统计放进
worker，那些 read 已经不在批次里了。

### 3.2 为什么 overlap 只算一次、合并前要重算

一个 read 对要同时用到三件事——裁接头、碱基校正、插入片段长度——它们共用**同一次**
overlap 分析（上游显式缓存了它）。这是双端链路上最贵的一步，算三遍不可接受。

唯一的例外是**合并**：读到这里时 read 已经被前面的修剪步骤改过，重叠关系已经不同了，
必须**重算**（上游为 issue #675 做的修正）。此外，开启"允许缺口"时裁接头用的那份重叠
也要单独再算一次（缺口路径的结论与无缺口的不同）。

---

## 4. 配置与默认值

`WorkflowConfig` 的默认值走 **fastp 命令行的默认行为**：接头裁剪与 reads 过滤开着、
质量剪切与 poly 修剪要显式开、不去重、不合并。字段分组与对应的上游选项：

| 分组 | 字段 | 上游对应 |
| --- | --- | --- |
| 输入输出 | `read1` / `output1` / `read2` / `output2` | `-i`/`-o`（R1）与 `-I`/`-O`（R2） |
| | `unpaired` | `--unpaired1`（见 5.3 的去向规则） |
| | `failed_out` / `merged_out` / `overlapped_out` | `--failed_out` / `--merged_out` / `--overlapped_out` |
| 规范化 | `normalize` / `input_phred` / `fix_mgi` | `--phred64` / `--fix_mgi_id` |
| 首尾修剪 + 滑窗剪切 | `cut` | `-f/-t`（固定）与 `-5/-3/-r`（滑窗） |
| | `max_length1` / `max_length2` | `--max_len1/2`（**只在本层实现**，见第 6 节第 7 条） |
| poly 修剪 | `poly` | `-x`/`-g`（开关）与 `--poly_x_min_len`/`--poly_g_min_len`（最短长度） |
| | `trim_poly_g` | `--trim_poly_g` / `--disable_trim_poly_g`；`None` 表示由二色判定决定 |
| 接头裁剪 | `adapter_trimming` / `detect_adapter` | `--detect_adapter_for_pe`（单端默认检测、双端默认不检测） |
| | `adapter1` / `adapter2` | `--adapter_sequence` / `--adapter_sequence_r2` |
| | `extra_adapters` | `--adapter_fasta`（与 `adapter1/2` 合并） |
| | `adapter_allow_one_gap` | `--allow_gap_overlap_trimming` |
| | `adapter_dimer` / `adapter_dimer_max_len` | 接头二聚体判定 / `--dimer_max_len` |
| reads 过滤 | `filter` | 四类判据（质量 / N / 长度 / 复杂度） |
| 双端专用 | `overlap` / `correction` / `merge` / `merge_include_unmerged` | overlap 参数 / `--correction` / `--merge` / `--include_unmerged` |
| 其他预处理 | `dedup` / `dedup_evaluate` / `dedup_accuracy_level` | `--dedup` / `--dont_eval_duplication` 的反面 / `--dup_calc_accuracy` |
| | `dedup_buffer_bytes` | **本实现新增**（上游没有；见 [deduplication.md](../bio_analysis_function/submodules/deduplication/deduplication.md)） |
| | `umi` | `--umi` 系列 |
| | `index_blacklist1/2` / `index_threshold` | `--filter_by_index1/2` / `--filter_by_index_threshold` |
| 输出组织 | `compress` | 压缩跟随输入 |
| | `split_records` / `split_number` / `split_digits` | `--split_by_lines` / `--split` / 序号位数 |
| 运行 | `threads` / `max_reads` / `report_title` | `--threads` / `--reads_to_process` / 报告标题 |

各步的子配置类型（`CutSpec` / `PolySpec` / `FilterSpec` / `OverlapSpec` / `UmiSpec`）
直接复用 `common/native/abi.py` 里的定义——它们本来就是按 fastp 命令行默认值写的；
`WorkflowConfig` 只添那些**工作流层面**的决策（阶段 A 怎么跑、落单 read 去哪、分卷怎么算）。

`__post_init__` 会挡住自相矛盾的组合（合并模式必须有双端输入与 `merged_out`、
碱基校正只适用于双端、单端不能给落单/重叠区输出、两种分卷口径只能给一个、质量编码只认 33/64）。

---

## 5. 输出

### 5.1 报告有两种，用途不同

一次 run 产出两份报告，分工明确：

| 产物 | 写在哪 | 给谁看 | 内容 |
| --- | --- | --- | --- |
| `workflow.json` | `log_dir`（默认不写） | **我们**——收集反馈与优化用 | 尽量留下每一步的中间量：各步改动条数、过滤原因明细、阶段 A 的判定、插入片段分布、完整配置 |
| HTML | `html_path`（默认不写） | **用户** | 只呈现结论：概览卡片、前后对比曲线、各步改动量、阶段 A 结论、输出文件清单 |

**为什么这么分。** JSON 面向的是排障与调优：它要能回答"这次为什么掉了这么多"，
所以宁可多留过程量（`build_workflow_json` 的形状参照上游 `fastp.json`，键名尽量对齐，
便于两份日志对照）。HTML 面向的是用户：只会把结论摆出来，不塞中间量。
两份都**不带时间戳**，同一份数据每次渲染逐字节相同——报告本身也是可复现的产物。

HTML 内嵌 SVG、不执行脚本（沿用 `bio_analysis_function/submodules/read_stats/report.py` 的取舍），
能离线打开、当邮件附件发、在浏览器里存成矢量 PDF。绘图原语来自公共层
`common/svg_report.py`。

> 两者的入参都是 `WorkflowOutcome`，且经同一个 `_aggregate` 口径取值，
> 所以 JSON 与页面上的"前后对比"数字天然一致。

### 5.2 `WorkflowOutcome` 的字段

| 字段 | 含义 |
| --- | --- |
| `config` | 本次配置 |
| `scan` | 阶段 A 的结论（`ScanReport`：二色判定、polyG 是否开启、检出的接头与采样量、总条数） |
| `summary` | 原生层的汇总标量（`NativeWorkflowSummary`） |
| `pre_stats1/2` / `post_stats1/2` | 过滤前 / 过滤后的质量统计（单端只有 `*1`） |
| `insert_size_histogram` | 插入片段长度直方图（下标即长度，最后一项是溢出桶；单端全 0） |
| `outputs` / `split_files` | 实际写出的文件（分卷时是全部卷） |
| `log_path` / `html_path` | 两份报告的落盘位置（不给就不写） |
| `retention_rate` / `base_retention_rate` | 属性，保留的 read 比例与碱基比例 |

### 5.3 落单 read 的去向规则

双端模式下只有一端通过过滤时，那条 read 的去向由 `unpaired` 决定：

- **没给（默认 `None`）**：写到主输出旁边的 `<主名>.unpaired<后缀>`；
- **给空串**：明确**丢弃**（上游不给 `--unpaired1` 时就是这个行为）；
- **给了路径**：写到那里。

分卷模式下**只写主输出**，落单 read 无处安放，因此默认值退化为丢弃（报告里如实写出）；
若用户显式给了落单路径，则被判为参数矛盾并报错——明确要过的东西不该无声消失。

---

## 6. 与上游 fastp 的差异

与上游逐条对齐是这条线的原则，但有几处是**刻意的、必须如实说明**的差异：

1. **去重在读取端串行判定**，结果是数据的确定函数；上游在 worker 里带锁判，
   重复率随线程数变。
2. **插入片段对所有 read 对统计**；上游只统计 0 号线程经手的那一份（省时间，
   但直方图计数只有 $1/N$）。
3. **过滤失败明细按 read 计**；上游按"一对里较差的那个结果码 ×2"计，
   而且"未合并"分支又是另一套口径。
4. **分卷只支持"每卷多少条"**（`--split_by_lines` 语义）；"按文件数"（`--split`）
   由上层先精确数一遍总条数再换算——上游是估算的。
5. 分卷与辅助输出（落单 / 失败 / 合并 / 重叠区）同时启用时**报错**；
   上游静默不写并打一句警告。用户明确要过的东西不该无声消失。
6. 落单 read **合并写进一个 unpaired 文件**（上限只给 `--unpaired1` 时上游也是这个行为）。
7. **限长截断**（`--max_len1/2`）只在工作流层实现；单独的质量剪切模块没有这个参数。
8. **阶段 A 由 Python 侧编排**；上游把它放在 `main.cpp` 的 `Evaluator` 里（理由见第 2 节）。
9. **未覆盖的上游能力**：`--stdin` / `--stdout` / `--interleaved_in` 流式模式、
   `--dont_overwrite`、`--verbose`、双端 R2 的独立首尾修剪（`--trim_front2` /
   `--trim_tail2`，本实现两条 read 共用一套）、`--overrepresentation_analysis`
   （由上层单独调用 `overrepresented_sequences` 子模块，不在工作流入口内）。

---

## 7. 已知边界

- **只处理 FASTQ**（`.fq` / `.fastq`，可 gzip，gzip 按魔数识别）。不读 BAM/CRAM，
  也不做格式转换。
- **分卷只支持"每卷多少条"**，且分卷时只写主输出（见第 6 节第 4、5 条）。
- **落单 read 的去向规则见 5.3**；分卷与显式落单路径不可同时要。
- **合并模式**只对双端有意义，且必须给出 `merged_out`。
- **超长直方图的溢出桶**（`insert_size_histogram` 最后一项）混着"判不出"与"超上限"
  两种，报告里已单列到 `insert_size_unknown`，柱状图不画它。
- **实际执行链路已接通 `bio-workflow`**：桌面端在算法广场提交任务后，由 Electron
  主进程起 Python 子进程调用 `modules/workflow/wrapper.py`（JSON 进、JSON 出），再进原生层；
  生物模块其余算法仍只到契约层。
- **桌面端取消是硬杀进程**：Python 进程被直接终止，来不及走原生层的失败清理，
  已经写出一部分的输出文件会残留在用户给的路径上（正常报错退出不会——原生层会删掉
  半成品）。取消后请自行检查输出路径。

---

## 8. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-workflow` |
| 名称 | fastp 预处理工作流 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_WORKFLOW_METHOD` |

它是预处理线里唯一"进 FASTQ、出 FASTQ + 报告"的**流程型入口**：
用户给一份（或一对）原始 FASTQ，拿到标准预处理后的结果文件与汇总报告。

> 这条流程**已接通实际执行链路**：在广场上填好路径与选项、提交任务后，
> 由 Electron 主进程执行 `modules/workflow/wrapper.py`，界面轮询任务状态与事件，
> 完成后列出输出文件、HTML 报告与日志路径，并可按任务 id 取消。
> （生物模块其余算法的入口仍只到契约层；本工作流模块下另有
> [breseq 工作流](breseq/breseq_workflow.md)，它也已接通，入口是
> `modules/workflow/breseq/wrapper.py`。）

---

## 9. Python API 用法

```python
from pathlib import Path
from modules.workflow import PolySpec, WorkflowConfig, run_workflow

outcome = run_workflow(
    WorkflowConfig(
        read1=Path("raw_R1.fq.gz"),
        read2=Path("raw_R2.fq.gz"),
        output1=Path("clean_R1.fq.gz"),
        output2=Path("clean_R2.fq.gz"),
        poly=PolySpec(g=True),        # 其余默认：接头裁剪与过滤开着、不去重不合并
    ),
    html_path=Path("report.html"),    # 给用户看的那份
    log_dir=Path("logs"),             # 各步统计的 workflow.json 写这里
)

print(outcome.summary.total_reads, outcome.summary.output_reads)
print(f"{outcome.retention_rate:.1%}")
print(outcome.scan.adapter.adapter1)  # 阶段 A 检出的接头
```

不给 `html_path` / `log_dir` 就只跑不写报告。`run_workflow` 返回的 `WorkflowOutcome`
带全部统计、阶段 A 结论与输出文件清单。

### 9.1 供桌面端调用的 JSON 入口

`modules/workflow/wrapper.py` 是给桌面端用的薄壳：从 **stdin** 读一个 JSON 对象，调
`run_workflow`，把结果写到 **stdout**。算法与编排都不在这里，这里只做
「JSON ↔ `WorkflowConfig`」的翻译。

```bash
python -u modules/workflow/wrapper.py < request.json
```

输入字段与广场上的 `BIO_WORKFLOW_METHOD` 一一对应：`read1Path` / `read2Path` /
`output1Path` / `output2Path`（成对规则同界面）、`qualityCutMode`、`trimPolyG`
（`auto` / `on` / `off`）、`adapterTrimming`、`detectAdapterForPE`、`correction`、
`dedup`（`none` / `evaluate` / `filter`）、`merge`、`splitRecords`、`threads`、
`compress`（`follow` / `gzip` / `plain`），外加桌面端自己给的 `htmlPath` 与 `logDir`。

成功时 stdout 是一行 JSON `{"ok": true, "outcome": …, "outputs": [...], "html": …,
"log": …}`，退出码 0；失败时是 `{"ok": false, "error": {"type": …, "message": …}}`，
退出码 1——**不让调用方去解析 stderr 文本**。

入口层面另有三条约定：

- **表单送来的数字是字符串**：`splitRecords` / `threads` 按数值字符串解析，空串取默认值。
- **`3' 端剪切` 对应 `cut_tail`**（`CutSpec(tail=True)`），不是 `cut_right`；
  口径与 `bio_analysis_function/submodules/quality_trimming` 的选项映射一致。
- **`merge` 与 `splitRecords` 互斥**：`merge` 打开时合并结果写到主输出旁边的
  `*.merged.*`（界面没有单独的合并输出字段）；两者同开会在入口直接报错，
  而不是无声丢掉一边。

---

## 10. 验证

```bash
python -m pytest modules/workflow -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_workflow.py -q
```

Python 侧 **31 项**，按文件分两组：

- **`tests/test_runner.py`（20 项）**：跑**真正的原生工作流**，重点盯三类性质——
  统计自洽（summary 里的条数、碱基数与真实写出的文件逐条对得上）、
  分卷命名契约（`runner._split_name` 算出的名字就是原生层真的写出来的文件，
  两侧各有一份命名实现，漂移了必须被发现）、**线程数不影响结果**（生物模块定下的纪律）。
  另有单端直路、质量剪切、过滤与失败原因、双端按 overlap 裁接头与"判不出退化为
  按序列匹配"、落单 read 去向、按文件数分卷先数一遍、合并模式、二色系统自动开 polyG、
  polyX 与限长截断的先后、显式接头跳过检测、按 index 过滤、报告落盘、
  空输入、缺失输入、配置矛盾、阶段 A 与保留比例。
- **`tests/test_report.py`（11 项）**：手工构造 `WorkflowOutcome`，把报告层单独拎出来测
  ——自包含（无 `<script>`、无外部 URL）、逐字节可复现、图上点数与数据对得上、
  双端前后曲线不等长也能对齐渲染、JSON 可 `dumps`、单端 JSON 标"single end"、
  插入片段直方图只留非零桶且只对双端出现、失败原因表、未改动步骤不出现、
  阶段 A 节显示检出的接头。

原生层 ABI 测试 **20 项**（`common/native/tests/test_abi_workflow.py`）：
二色判定的前缀识别、非二色仪器、空输入与缺失输入；工作流的纯拷贝与输入一致、
关掉统计时曲线为空、**子结构体全零回退到默认值**这条 ABI 约定、参数矛盾报错、
缺失输入不留下任何输出、分卷命名、插入片段直方图取回、`max_reads` 限制读入量、
失败 read 带原因标签、去重丢重复并计数。

---

## 附：开发记录

### 选择依据

1. **它是预处理线的收口**。十六个开发项各自可跑之后，缺的是"按标准顺序整条跑一遍"
   这件事本身。没有它，用户要自己拼十六步、自己管中间文件，而每一步都会重读一遍数据。
2. **它把"单遍"这件事做进原生层**。逐个调用是十几倍 I/O，只有把整条链放进一次
   读入、逐条走完、分批写出，才谈得上处理真实样本的规模。
3. **它是第一条"三端职责不对等"的流水线**。既有两条流水线的读取端不做事、写出端
   固定，工作流却要求读取端有**有状态、顺序相关**的判定、写出端要**多路分流**，
   因此必须自带一条流水线（见 [native.md](../bio_analysis_function/common/native/native.md) 5.4.0.5.3）。

### 开发状态

- 状态：**已完成（Python 编排层 + 原生层 + 桌面端执行链路）**——两段式编排、单遍处理链、
  五路分流写出、分卷、两份报告、算法广场契约入口、桌面端 JSON 入口、单元测试与 ABI 测试齐备
- 开发目录：`modules/workflow/`（2026-09-23 从 `modules/bio_analysis_function/workflow/` 迁出）
- 文件构成：`config.py`（配置与结果类型）、`runner.py`（阶段 A 与编排入口）、
  `report.py`（JSON 日志与 HTML 报告）、`wrapper.py`（桌面端的 JSON 入口）、
  `workflow.md`、`tests/`
- 依赖：生物模块的公共层（`bio_analysis_function/common/native/abi.py`、
  `common/svg_report.py`）与原生库；不依赖任何外部程序
- 算法广场入口：已在 `IDEA Assistant Code/src/compute.ts` 登记 `bio-workflow`
  （分类"序列预处理"，`local`，`beta`）。**已接通真实执行链路**：Electron 主进程 →
  `wrapper.py` → 原生层，是**已接通的两条方法之一**（另一条是 breseq 工作流）
- 预设队列：登记在 `queue-presets.json`（本目录，**数据文件、不进客户端构建**：改完存盘、重开组装页即生效，不需要重新出包）。现有两个条目（**单端** / **双端**），步骤顺序照抄本文第 3 节，步骤名沿用该节措辞；没有独立算法块的环节（overlap 分析、重叠区输出、限长截断）写进对应条目的 `unmapped`，由组装页如实标出。
  客户端侧：Electron 主进程读该文件并经 IPC 交给渲染端（`IDEA Assistant Code/src/queuePresets.ts` 负责校验），组装页另有「存为本机预设」把当前画布存在本机。约束见 [PROJECT_RULES.md](../../PROJECT_RULES.md) 7.4
- 打包：`modules/workflow/`、`modules/bio_analysis_function/`（仅运行时需要的部分）与
  `modules/__init__.py` 一起进 `resources/modules/`；解释器复用客户端自带的嵌入式
  Python（`resources/rag/runtime/python`），不额外打包一份
- 原生实现：`common/native/src/workflow.{h,cpp}`（主编排）、`workflow_pipeline.h`
  （工作流专用保序流水线）、`two_color.{h,cpp}`（二色判定）；
  导出 `bio_detect_two_color_system` 与 `bio_run_workflow` 一组 C ABI，
  ctypes 封装为 `common.native.abi.run_workflow` 等
- 原生库版本：0.16.0 → **0.17.0**

### 工程实现

**为什么阶段 A 落在 Python 侧。** 三件事各自本来就是独立可跑的算法/工具，
让它们各自跑、结论传进主处理，中间结论才对调用方可见——报告要写出"用了什么参数、
为什么"。另外这阶段偏 I/O 采样，与阶段 B 的逐条计算没有共享状态。

**三端的累加器是分开的。** `prepare` 在读取线程里跑、`consume` 在写出线程里跑，
两者并发，因此各自持一套计数器，等流水线把所有线程 join 回来之后再合并。
共用一套就是数据竞争，而且这种竞争不会崩、只会悄悄算错数。

**写出器声明在 `try` 内部。** 失败时它要随栈展开先析构（关掉文件句柄），
`catch` 里的清理才会成功——Windows 上删一个仍被打开的文件会静默失败。
分卷写出器的产物不止一个文件，所以它自己记着创建过哪些路径，失败时把这一批全删掉。

**限长截断放在工作流层。** 它是输出整形，不是判定，单独的质量剪切模块没有对应参数，
两者不共享字段。

**报告层不知道流程长什么样。** `report.py` 只吃 `WorkflowOutcome`，绘图原语来自
公共层 `common/svg_report.py`（与 read_stats 的报告共用同一套原语）。

> 这一轮还顺带修掉一个既有缺陷：三条流水线（`pipeline.h`、`paired_pipeline.h`、
> `workflow_pipeline.h`）在**输入为空**时写出端会永久挂起——写出端等的是槽的条件变量，
> 而读取端一个批次都没发出就收工时只唤醒了 `work_cv`，且等待谓词里没有"读取端已收工"
> 这一条。已修，详见 `native.md` 5.4.0.5.3。

### 验证记录

Python 侧 **31 项**、原生 ABI **20 项**，全部通过。桌面端到端跑的是真正的原生工作流，
因为要验的正是"整条链串起来之后结果还对不对"；各步单独的判定已由各自的测试守住。

### 待办

- `wrapper.py` 入口**没有单独的自动化测试**，桌面端那条链需要人工跑一遍（真 FASTQ 进、
  HTML 报告出）；打包后的资源（模块目录 + 嵌入式 Python + 原生库）也还没在真实安装包里验过
- 与 fastp 二进制做端到端对拍（当前只与 Python 参考实现及各步的单独测试对拍）
- 补上第 6 节第 9 条列出的未覆盖上游能力（流式输入输出、`--dont_overwrite`、
  R2 的独立首尾修剪等），按需求排优先级
