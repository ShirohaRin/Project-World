# read_filtering：reads 过滤

按质量、N 含量、长度与复杂度逐条判定 reads 的去留，只把通过的 read 写进输出文件，
并按失败原因分类统计。算法与 fastp 1.3.x 的 `Filter::passFilter` 逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-read-filtering`）。**只需要一份 FASTQ 本身就能运行**——
不需要参考基因组、不需要注释文件、不需要数据库，也不需要先跑别的算法。
代码层面同样可以单独导入，不依赖本模块的其他子模块，也不依赖任何外部程序。

---

## 1. 它解决什么问题

前面两步（[`quality_trimming`](../quality_trimming/quality_trimming.md)、
[`poly_trimming`](../poly_trimming/poly_trimming.md)）都是**改序列**：
把低质量的末端切掉、把同种碱基的尾巴切掉。
但有些 read 修完之后仍然不值得要——整条都烂、几乎全是 `N`、短到没法比对。
这一步负责**判去留**：整条 read 通过就留下，不通过就丢掉，序列本身一个字符都不改。

四类判据，对应四种真实的坏数据：

| 判据 | 拦掉的是什么数据 | 为什么它坏 |
| --- | --- | --- |
| 质量 | 低质量碱基过多的 read | 错误率高的 read 比对不上，或比到错误位置 |
| N 含量 | 含太多 `N` 的 read | `N` 是"测不准"，比对软件通常直接跳过 |
| 长度 | 过短（或过长）的 read | 太短没有唯一性，会大量多重比对 |
| 低复杂度 | `AAAAAA…` 这类序列 | 会匹配到基因组里无数位置，纯属噪声 |

> **空缺记录永远失败。** 长度为 0 的 read 不是"过滤偏好"能决定的事，
> 上游把它当作格式层面的异常，本实现同样如此：无论过滤开关怎么设，
> 空 read 一律丢弃。这一点有专门的测试守着。

---

## 2. 在算法广场中使用

本算法已在 IDEA Assistant 的**生物计算方法广场**（BIO COMPUTE MARKETPLACE）登记：

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-read-filtering` |
| 名称 | reads 过滤 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_READ_FILTERING_METHOD` |

**输入字段**（界面按此自动渲染表单）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| reads 文件 | 文本 | 是 | — | FASTQ 路径（`.fq` / `.fastq`，可 gzip）。当前只处理单端数据。 |
| 达标质量（Phred） | 数字 | 否 | 15 | 低于它的碱基算低质量。调高会更严。 |
| 低质量碱基比例上限 | 数字 | 否 | 40 | 百分数。低质量碱基数超过 `上限 × 长度 / 100` 即丢弃。 |
| N 碱基数量上限 | 数字 | 否 | 5 | 超过即丢弃。 |
| 平均质量下限 | 数字 | 否 | 0 | 0 表示不设要求。 |
| 最短长度 | 数字 | 否 | 15 | 短于它的 read 丢弃。 |
| 最长长度 | 数字 | 否 | 0 | 0 表示不限。 |
| 低复杂度过滤 | 选择 | 否 | 不启用 | `不启用` / `启用`。fastp 默认也是关闭。 |
| 复杂度阈值（%） | 数字 | 否 | 30 | 仅在上面选"启用"时生效。 |

**输出**：通过过滤的 reads 文件引用，加上统计摘要——输入条数、通过条数、
丢弃条数（**按原因分类**：低质量 / N 过多 / 过短 / 过长 / 低复杂度）、
通过率与碱基数变化。可选再产出**一份失败 reads 归档**：被丢弃的 read 原样另存，
名字后追加原因标签（如 `failed_too_short`），条数与丢弃数相等——
用户最常问的"我的数据为什么少了一半"，靠它可以直接翻出被丢掉的那几条。

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**
> （与 `bio-quality-trimming`、`bio-poly-trimming` 的现状一致）。

---

## 3. Python API 用法

### 文件进、文件出

```python
from modules.bio_analysis_function.submodules.read_filtering import (
    ReadFilterConfig,
    filter_fastq,
)

summary = filter_fastq(
    "trimmed_R1.fastq.gz",
    "clean_R1.fastq.gz",
    failed_output_path="rejected_R1.fastq.gz",   # 可选：把丢掉的 read 另存一份
    config=ReadFilterConfig(qualified_quality_phred=20, required_length=50),
)

print(summary.kept_reads, f"{summary.kept_rate:.1%}")
print(summary.render())          # 含按原因分类的失败明细
print(summary.reason_counts())   # [("failed_quality_filter", 3), ...]
```

处理是流式的，任何时候内存里只有当前记录。默认输出压缩方式**跟随输入**
（与输出文件名的扩展名无关）。**序列与质量不被改写**，写出的记录与输入逐字节一致。

给了 `failed_output_path` 时，被丢弃的 read 会**原样**写进那个文件，只在名字后追加
一个空格与失败原因标签（与上游 `--failed_out` 的做法一致），因此归档里可以直接看到
"哪条、因为什么被丢了"。写出的条数恒等于 `summary.dropped_reads`（有测试守着）；
两个输出同压缩方式，中途失败时**两份半成品都删掉**。

### 单条 read

```python
from modules.bio_analysis_function.submodules.read_filtering import (
    PASS_FILTER,
    ReadFilterConfig,
    filter_verdict,
    verdict_label,
)

verdict = filter_verdict(b"ACGT" * 40, b"I" * 160)
print(verdict == PASS_FILTER)   # True
print(verdict_label(verdict))   # "passed"
```

只关心真假时用 `passes_filter(...)`；想知道"低质量碱基有几个"这类中间量时用
`count_quality_metrics(sequence, quality, qualified_code=15 + 33)`。

### 原生实现（同一套判定，跑大样本用这个）

上面的 Python 实现同时是**可逐位对拍的规格**；真正跑几十 GB 样本时用原生层，
它位于模块公共层 `common/native/`（与 `common/fastq.py` 同级），实现与本文件逐行对应：

```python
from modules.bio_analysis_function.common.native import read_filter_fastq

summary = read_filter_fastq(
    "trimmed_R1.fastq.gz",
    "clean_R1.fastq.gz",
    failed_output_path="rejected_R1.fastq.gz",   # 可选，同上
    qualified_quality_phred=20,
    required_length=50,
)

print(summary.kept_rate, summary.failures)   # failures: {结果码: 条数}
```

参数名与 `ReadFilterConfig` 的字段一一对应（只是展开成关键字参数，因为公共层
不反向依赖子模块）。返回的 `NativeFilterSummary` 字段与 `FilterSummary` 对齐，
`failures` 的结果码也相同，因此两侧的统计可以直接对照。

两个细节：`unqualified_percent_limit` 按**百分数**收（40.0 = 40%），
`complexity_threshold` 按**比例**收（0.3 = 30%），传给原生层时换算成万分之一整数
（4000 / 3000），精度到 0.01%，实际会设的阈值都在这个精度之内、无损传递；
`threads` 只影响速度、不影响结果。

---

## 4. 参数

`ReadFilterConfig` 的字段（默认值与 fastp 命令行一致）：

| 字段 | 默认 | fastp 参数 | 说明 |
| --- | --- | --- | --- |
| `enabled_quality` | `True` | `--disable_quality_filtering` 的反面 | 质量过滤总开关 |
| `qualified_quality_phred` | 15 | `-q` / `--qualified_quality_phred` | 达标线；字符码恰好等于它算达标 |
| `unqualified_percent_limit` | 40.0 | `-u` / `--unqualified_percent_limit` | 低质量碱基比例上限（百分数） |
| `n_base_limit` | 5 | `-n` / `--n_base_limit` | N 的最大个数 |
| `average_qual` | 0 | `-e` / `--average_qual` | 平均质量下限，0 表示不限 |
| `enabled_length` | `True` | `--disable_length_filtering` 的反面 | 长度过滤总开关 |
| `required_length` | 15 | `-l` / `--length_required` | 最短长度 |
| `max_length` | 0 | `--length_limit` | 最长长度，0 表示不限 |
| `enabled_complexity` | `False` | `-y` / `--low_complexity_filter` | 低复杂度过滤**默认关闭** |
| `complexity_threshold` | 0.3 | `-Y` / `--complexity_threshold` | 复杂度下限，**比例**而非百分数 |

参数越界会在构造 `ReadFilterConfig` 时直接报错。

---

## 5. 输出解读

`FilterSummary` 的字段：

| 字段 | 含义 |
| --- | --- |
| `total_reads` | 输入条数 |
| `kept_reads` | 通过条数 |
| `dropped_reads` | 丢弃条数，恒等于 `total_reads - kept_reads` |
| `bases_before` / `bases_after` | 过滤前后的碱基总数 |
| `failures` | `{结果码: 条数}`，只含失败原因，各项之和等于 `dropped_reads` |
| `kept_rate` | 属性，通过比例 |
| `bases_removed` | 属性，被丢弃 read 带走的碱基数 |
| `reason_counts()` | 按固定顺序返回 `[(标签, 条数)]`，便于与 fastp 报告逐项对照 |

**怎么判断参数是否合适**：先看 `kept_rate` 与 `reason_counts()` 的分布。
如果"低质量"占绝大多数，说明质量阈值或比例上限定得太紧；
如果"过短"很多，往往是前一步的质量剪切切得太狠，而不是过滤本身的问题。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `filter_verdict` / `passes_filter` | `Filter::passFilter` |
| `passes_low_complexity` | `Filter::passLowComplexityFilter` |
| `count_quality_metrics` | `fastp_simd::countQualityMetrics`（SIMD 版本） |
| 结果码常量 | `src/common.h` 的 `PASS_FILTER` / `FAIL_*`（数值照抄） |
| `filter_fastq` | 单端处理链里的"过滤 → 写出"一段 |
| `failed_output_path` / `failed_out` | `--failed_out`：被丢弃的 read 另存一份，名字后追加原因标签 |

`failed_out` 这一项已实现（Python 侧与原生侧都支持）。上游写归档时用的
`Read::appendToStringWithTag` 只改名字那一行——序列、加号行、质量行原样复写，
本实现同样如此，两边对拍是逐字节比对。

**三处口径按上游原样保留**，不要"顺手改对"：

1. 判定是**短路**的：质量那三项是 `else if`，只报第一条命中的原因；
2. 低质量比例是**浮点**比较（`>`，恰好等于上限算通过）；
3. 平均质量用**整数除法**（小数直接丢掉）。

本子模块范围之外的能力（接头检测与裁剪、双端 overlap 校正与合并、UMI 提取、
重复检测与去重、reads 质量统计、过表达序列分析、双端插入片段长度分布）
**均已实现**，见各子模块自己的文档与算法清单第 6 节的进度表。
**预处理线的算法层到此全部完成**；唯一不做的是上游那份 HTML 报告
（本项目里报告由前端渲染，算法层只给结构化数据）。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/read_filtering -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_filter.py -q
```

Python 侧测试 42 项，按文件分三组：

- **`tests/test_algorithm.py`（23 项）**：逐条判定的边界——判定顺序、
  "恰好等于阈值"的两侧、整数除法口径、总开关关闭时的行为，以及质量统计与
  "每步重新数一遍"的朴素实现在 500 组随机数据上的一致性。
- **`tests/test_runner.py`（15 项）**：文件级接口的统计口径、只写通过的记录、
  压缩策略（跟随输入 / 显式指定）、失败时删掉半成品、父目录创建，
  以及失败归档（内容与原因标签、压缩跟随、不给归档时不产出多余文件）。
- **`tests/test_real_data.py`（4 项）**：在真实 Illumina 数据上验证默认参数不滥杀、
  收紧阈值时筛掉的正是尾部最差的几条、以及过滤不改写序列。

**真实数据验证结果**（`tests/data/fastp_R1.fq`，9 条记录，其中 1 条长度为 0）：

| 配置 | 通过 | 失败明细 |
| --- | --- | --- |
| 默认（Q15 / 40% / 5 N / 最短 15 / 复杂度关） | 8 | `failed_too_short` × 1（那条空记录） |
| 低质量比例上限收紧到 20% | 5 | `failed_quality_filter` × 3、`failed_too_short` × 1 |
| Q30 且不允许任何低质量碱基 | 0 | `failed_quality_filter` × 8、`failed_too_short` × 1 |

8 条真实 read 的低质量碱基数依次为 `6, 38, 3, 37, 3, 37, 3, 3`（Q15 阈值），
平均质量在 Q28~Q34 之间。第二行筛掉的正是 38 与两个 37 那三条，
第三行则说明"每个碱基都要 Q30"对这批数据确实做不到——两行都是数据本身的特征，
不是阈值在乱杀。

**原生层对拍 23 项**（`common/native/tests/test_abi_filter.py`）：11 组参数要求
输出文件逐字节相同**且失败分类字典相同**；另有结果码映射（每种失败各一条，
直接核对分类计数）、分类之和 = 丢弃总数、通过条碱基数 = 写出碱基数三条独立判据；
合成数据（5000 条，故意掺 N 与同聚物）覆盖跨批次与多线程（1/2/4/8 线程结果一致）；
gzip 与中文路径各一条；失败归档三条（与 Python 版逐字节一致、压缩跟随主输出、
中途失败时两份半成品都删）。

---

## 8. 已知限制

- **只处理单端 read**。双端的配对过滤（一条失败则成对丢弃）属于后续算法。
- **不做接头识别**。接头残留不会让 read 变短到失败，只能靠后续的接头裁剪步骤处理，
  因此上游的处理顺序是"接头裁剪在前、过滤在后"。
- **不理解碱基语义**。复杂度只看"相邻碱基是否不同"，测不出串联重复、
  也认不出低复杂度但相邻碱基偶尔变化的序列。
- **默认参数面向 Illumina 常见数据**。阈值是绝对标准、不随数据自适应，
  换平台（长读长、低质量化学）时需要重新设参。
- **失败归档是可选的**（`failed_output_path`）。不传就只保留统计数字，
  不额外产出文件——与上游 `--failed_out` 一样是开关而不是默认行为。
- **实际计算执行链路尚未接通**。广场上的入口目前只到契约层。
- **原生实现的默认线程数与其他预处理算法不同**。本算法计算占比高，实测未压缩输入上
  8 线程约为单线程的 $2.0\times$（30 万条 151bp：240 ms → 122 ms），因此自动取硬件
  并发数；质量剪切与 poly 修剪反过来，自动取单线程。走 gzip 时加线程没有收益——
  瓶颈在写出端的单线程 deflate（详见 `common/native/native.md` 的 5.4.0.5 节）。

---

## 附：开发记录（原《生物方法模块算法清单》5.4.3 节）


#### 5.4.3.1 选择依据

1. **它是前两步的收尾，也是上游单端处理链的第三个环节**。上游对单条 read 的顺序是
   "UMI → 固定位置修剪 → **滑窗质量剪切** → polyG/polyX → 接头裁剪 → **过滤**"。
   前两个算法都只**改序列**，有些 read 修完之后仍然不值得保留——整条都烂、
   几乎全是 N、短到没法比对。这一步负责**判去留**，序列一个字符都不改。
2. **四类判据各自对应一种真实的坏数据**，而"坏"是可以讲清楚的：
   低质量 → 比对不上或比错位置；N 过多 → 比对软件直接跳过；
   过短 → 没有唯一性、大量多重比对；低复杂度 → 会匹配到基因组里无数位置。
3. **它是产品上最需要"看得到理由"的一步**。用户最常问的问题是"我的数据为什么少了
   一半"，只有把失败原因分类统计出来（而不是只报一个丢弃总数），才能回答这个问题。
4. **上游同样自带可逐行对照的源码**（`Filter::passFilter`），可以做到与前两个算法
   一样的逐位对齐。

#### 5.4.3.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**——核心算法、文件级接口、子模块说明文档、
  算法广场入口、单元测试、真实数据验证齐备，且已完成 C++ 移植并与 Python 版逐位对拍
- 开发目录：`modules/bio_analysis_function/submodules/read_filtering/`
- 文件构成：`algorithm.py`（单条 read 的判定）、`runner.py`（文件级接口与分类统计）、
  `read_filtering.md`、`tests/`
- 依赖：**仅 Python 标准库**（`dataclasses`、`pathlib`）与模块公共层
- 算法广场入口：已在 `IDEA Assistant Code/src/compute.ts` 登记 `bio-read-filtering`
  （分类"序列预处理"，`local`，`beta`），输入 9 项字段、输出为通过的 reads 文件引用
  与分类统计摘要。**当前只到契约层**
- 独立可用性：只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释、数据库，
  也不要求上游先做过处理
- 原生实现：`common/native/src/read_filter.{h,cpp}`，导出 C ABI
  `bio_read_filter_fastq`（见 $5.4.0$），ctypes 封装为
  `common.native.read_filter_fastq`；对拍测试 23 项

#### 5.4.3.3 问题定义与算法原理

**第一步：质量统计，一次遍历出三个量。** 上游在 `fastp_simd::countQualityMetrics`
里一次遍历同时算三个量，本实现照做：

$$
\text{lowQual} = \#\{i : q_i < \theta\},\quad
\text{nBase} = \#\{i : s_i = \mathrm{N}\},\quad
\text{totalQual} = \sum_i (q_i - 33)
$$

其中 $q_i$ 是质量字符码，$\theta$ 是"达标"阈值（Phred 值 $+33$）。
注意判据是**严格小于**：质量恰好等于阈值的碱基算达标。

**第二步：三类质量判据，短路判定。** 上游这三项写成 `else if`，
**只报第一条命中的原因**：

| 顺序 | 判据                                                                                       | 结果码            |
| -- | ---------------------------------------------------------------------------------------- | -------------- |
| 1  | $\text{lowQual} > \text{limit}\times n / 100$                                            | `FAIL_QUALITY` |
| 2  | $\text{average\_qual} > 0$ 且 $\lfloor \text{totalQual}/n \rfloor < \text{average\_qual}$ | `FAIL_QUALITY` |
| 3  | $\text{nBase} > \text{nBaseLimit}$                                                       | `FAIL_N_BASE`  |

第 1 条是比例判据而不是计数判据，因为同一条 read 在不同读长下"能容忍几个坏碱基"
本来就不同：151bp 上 40% 是 60 个，50bp 上只有 20 个。

**第 2 条用的整数除法是口径而不是笔误。** 上游两个操作数都是 `int`，
$\text{totalQual}/n$ 的小数部分被丢掉，所以平均质量 14.9 会被截成 14、
过不了 15 这条线。本实现按原样保留（Python 写成 `//`），并用测试固定——
若改成浮点比较，边界附近 read 的去留会成批改变。

**第三步：长度判据。** 先"过短"后"过长"，顺序本身有意义（两个条件同时矛盾时
报的是"过短"）。`maxLength = 0` 表示不限长度。

**第四步：低复杂度。** 复杂度定义为"每个碱基与下一个碱基不同的比例"：

$$
C = \frac{\#\{i : s_i \ne s_{i+1}\}}{n-1}
$$

全同序列 $C = 0$；四碱基等概率的随机序列约为 $0.75$。判据是 $C \ge \text{threshold}$
（**恰好等于阈值算通过**），默认阈值 0.3。边界：$n \le 1$ 时分母为 0，
上游明确返回 `false`（判为不通过）。

**两条"看起来奇怪但不能改"的边界**：

- **长度为 0 的 read 永远失败**（`FAIL_LENGTH`），且**不受任何过滤开关影响**。
  上游把它当作格式层面的异常，此时连质量统计都不做。真实数据里确实存在这种记录
  （本模块的共享测试数据 `fastp_R1.fq` 第一条就是），因此这条不是理论边界。
- **判定顺序决定报出的原因**。一条既低质量又 N 过多的 read 报的是"低质量"。

**阈值编码。** 上游把达标阈值以**字符**形式存在配置里（默认 `'0'`，即 Q15），
解析命令行时用 `num2qual` 换算（$Q + 33$，并把越界值夹到 $[0, 94]$）。
本实现对用户暴露 Phred 值，换算放在 `ReadFilterConfig.qualified_code`；
差异只有一处：越界值本实现**直接报错**而不是夹紧。

**结果码照抄上游数值。** 上游 `src/common.h` 刻意留出间隔（4、8、12……），
注释写明"数字越大表示越差"，以便日后插入新类别。本实现连数值一起照抄，
标签名也用上游 `FAILED_TYPES` 里的字符串（`failed_quality_filter` 等），
这样统计口径与报告字段可以直接和 fastp 对照。

#### 5.4.3.4 工程实现

**文件级接口为什么没有走公共层的** **`process_fastq`。** 那条公共管道只统计
"保留 / 丢弃"两个总数，而本算法真正要看的是**按原因分类的失败统计**
（多少条低质量、多少条 N 过多……），这正是 fastp 报告里"过滤结果"那一节。
因此 `runner.py` 自己驱动一遍流式读写，但读与写仍复用公共层的 `read_fastq` /
`FastqWriter`（`FastqWriter` 就是为"同时写两个输出"而从 `write_fastq` 里提出来的），
以及同一套"中途失败就删掉半成品"的处理方式——重复的只有计数循环，
不是 I/O 能力。

**统计口径与模块其余算法一致**：`total_reads = kept_reads + dropped_reads`，
另外 `failures` 明细之和必须等于 `dropped_reads`（有测试守着）。
序列从不被改写，因此这里没有 `changed_reads` 这个概念。

**参数校验只在构造配置时做一次**，热路径（`filter_verdict`）不再重复检查：
这是用户会直接构造的对象，越界值必须在这里挡住。

**算法广场入口。** 已登记为 `BIO_READ_FILTERING_METHOD`（id `bio-read-filtering`），
输入 9 项：reads 文件、达标质量、低质量碱基比例上限、N 上限、平均质量下限、
最短长度、最长长度、低复杂度开关、复杂度阈值。**输入自洽**——只需要一份 FASTQ，
不依赖参考基因组、注释、数据库，也不要求上游先做过处理。

本文件分两部分：前半（第 1~6 节）是**使用说明**——算法做什么、广场入口与输入输出、Python API 用法、参数表、输出解读、验证方式与已知限制；后半「附：开发记录」是**设计文档**——选择依据、算法原理、工程实现、验证记录与待办。

**原生层移植（C++）。** 判定逻辑落在 `common/native/src/read_filter.{h,cpp}`，
与 Python 版逐行对应；流水线复用 $5.4.0$ 那一条，本算法只提供"逐条判定"这一步。
为此做了两处扩展：

- **流水线新增结果码**。原流水线只区分"丢弃 / 改写"，而过滤的价值一半在"为什么丢"。
  现在 `StepOutcome` 多带一个 `verdict`，流水线的返回值从 `bio_stats_t` 变成
  `PipelineStats`（通用统计 + 按结果码计数的数组）。计数由每条 read **自己的处理器**
  累加（每个批次只有一个线程在写，批次之间由 writer 按序合并），因此**不需要原子量**，
  多线程下也不会让分类数字漂移。质量剪切与 poly 修剪不填结果码，它们那些槽恒为 0。
- **C ABI 新增一套过滤结构**：`bio_read_filter_options_t` /
  `bio_filter_breakdown_t` / `bio_filter_result_t` / `bio_read_filter_fastq`。
  两个比例参数用**万分之一整数**传递（40% → 4000，0.3 → 3000），
  算法内部还原成与 Python 版同序的浮点运算，因此边界比较逐位一致；
  明细字段与 Python 的结果码一一对应（见 $5.4.0.3$）。
- **失败归档进了公共流水线**。`--failed_out` 不是本算法独有的需求（双端的
  过滤类算法同样要它），因此"收集被丢弃的 read 并写第二个输出"做在
  `pipeline.h` 里，由 `dropped_path` 这个可选参数开关，而不是在过滤算法内部
  另起一套读写。给原因标签的那张表也放在流水线层（`verdict_label`），
  取值与上游 `FAILED_TYPES` 同口径。**多线程下归档与单线程逐字节一致**：
  收集发生在 worker（每个批次由唯一线程写），写出发生在按批次编号排序的
  writer 线程，与主输出共用同一套保序机制。

#### 5.4.3.5 验证记录

Python 侧测试共 **42 项**，全部通过（算法 23、文件级接口 15、真实数据 4）；
原生层另有 **23 项**对拍测试（`common/native/tests/test_abi_filter.py`），全通过。

**边界的两侧都测。** 这类"阈值判定"最容易写出差一位的错，因此每条判据都取两侧：

| 判据         | 恰好等于阈值              | 越过阈值                    |
| ---------- | ------------------- | ----------------------- |
| 低质量比例      | 40% → 通过            | 41% → `FAIL_QUALITY`    |
| 达标质量       | 恰好 Q15 → 算达标        | Q14 → 算低质量              |
| 平均质量（整数除法） | 总和 150/10 = 15 → 通过 | 总和 149/10 = 14 → 失败     |
| N 个数       | 恰好 5 个 → 通过         | 6 个 → `FAIL_N_BASE`     |
| 最短长度       | 恰好 15bp → 通过        | 14bp → `FAIL_LENGTH`    |
| 最长长度       | 恰好 100bp → 通过       | 101bp → `FAIL_TOO_LONG` |
| 复杂度        | 3/10 = 0.3 → 通过     | 2/10 = 0.2 → 不通过        |

**判定顺序单独测。** "既低质量又 N 过多"必须报低质量；"过短又过长"必须报过短；
"长度不够 + 复杂度不达标"必须报长度。

**实现交叉验证。** 质量统计另写一份朴素实现，在 500 组随机数据（长度 1\~200、
含 N、质量 0\~93、阈值随机）上对比，结果完全一致。

**真实数据验证**（`tests/data/fastp_R1.fq`，9 条记录，其中 1 条长度为 0）：

| 配置                                 | 通过 | 失败明细                                               |
| ---------------------------------- | -- | -------------------------------------------------- |
| 默认（Q15 / 40% / 5 N / 最短 15 / 复杂度关） | 8  | `failed_too_short` × 1（那条空记录）                      |
| 低质量比例上限收紧到 20%                     | 5  | `failed_quality_filter` × 3、`failed_too_short` × 1 |
| Q30 且不允许任何低质量碱基                    | 0  | `failed_quality_filter` × 8、`failed_too_short` × 1 |

8 条真实 read 的低质量碱基数依次为 `6, 38, 3, 37, 3, 37, 3, 3`（Q15 阈值），
平均质量在 Q28\~Q34 之间。三行合起来说明两件事：**默认参数不滥杀**
（fastp 自己仓库的数据只有那条空记录被丢弃），且**阈值确实在起作用**
（第二行筛掉的正是尾部最差的 38 与两个 37，第三行说明"每个碱基都要 Q30"
对这批数据确实做不到）。另有一条用例逐字段核对"写出的记录与输入完全一致"，
固定住"过滤不改写序列"。

**原生层怎么验证。** 与 Python 版对拍 **11 组参数**（默认、各判据单开与关闭、
最长长度与低复杂度组合、全部关闭），要求输出文件逐字节相同**且失败分类字典相同**；
另有三条不依赖 Python 实现的独立判据：结果码映射（每种失败各一条，直接核对分类计数
为 `{低质量 1、N 过多 1、过短 2、过长 1、低复杂度 1}`）、"分类之和 = 丢弃总数"、
"通过条的碱基数 = 写出的碱基数"。合成数据（5000 条、故意掺 N 与同聚物）覆盖了
跨批次与多线程（1/2/4/8 线程结果完全一致），gzip 与中文路径各一条。
失败归档另有四条：与 Python 版归档**逐字节相同**、条数等于丢弃数且每条都带原因标签、
压缩跟随主输出、中途失败时两份半成品都删。

#### 5.4.3.6 适用边界与已知限制

- **只处理单端 read**。双端的配对过滤（一条不过则成对丢弃）属于后续算法。
- **不做接头识别**。接头残留会让 read 变"脏"但不会让它变短到失败，
  因此上游的处理顺序是接头裁剪在前、过滤在后。
- **不理解碱基语义**。复杂度只看相邻碱基是否相同，测不出串联重复，
  也认不出"相邻碱基偶尔变化"的低复杂度序列。
- **阈值是绝对标准，不随数据自适应**。默认参数面向 Illumina 常见数据，
  换平台（长读长、低质量化学、单细胞）时需要重新设参。
- **失败归档是可选的开关**（`failed_output_path` / C ABI 的 `failed_out`），
  默认不产出，与上游 `--failed_out` 一致。
- **实际计算执行链路尚未接通**，广场入口只到契约层。
- **原生默认多线程**（自动取硬件并发数）。本算法计算占比高，实测未压缩输入上
  8 线程约为单线程的 $2.0\times$（数据见 $5.4.0.5$）；走 gzip 时无收益，
  因为瓶颈在写出端的单线程 deflate。

#### 5.4.3.7 待办

- 接通 `bio-read-filtering` 的实际执行链路（Electron → 本地 Python / 原生层），
  目前只到契约层
- 与 fastp 二进制做端到端结果对拍（当前只有单元测试层面的边界对齐）
- 双端的配对过滤（一条不过则成对丢弃）属于后续算法
- 失败归档（对应上游 `--failed_out`）已在 $5.4.3$ 完成；
  接头检测在 $5.4.4$、接头裁剪在 $5.4.5$、双端三件套与 UMI 亦已完成
