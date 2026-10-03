# poly_trimming：reads 尾部 poly 修剪

切掉 reads 3' 端一连串同种碱基的尾巴，输出修剪后的 FASTQ。
算法与 fastp 1.3.x 的 `trimPolyG` / `trimPolyX` 逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-poly-trimming`）。**只需要一份 FASTQ 本身就能运行**——
不需要参考基因组、不需要注释文件、不需要数据库，也不需要先跑别的算法。
代码层面同样可以单独导入，不依赖本模块的其他子模块，也不依赖任何外部程序。

---

## 1. 它解决什么问题

测序数据的 3' 端有时会出现一连串相同的碱基，来源有两种，处理方式也不同。

### polyG：仪器的一个具体假象

Illumina 的部分机型（NovaSeq / NextSeq / 部分 HiSeq）使用**双色合成**
（two-color chemistry），只检测红色和绿色两个荧光通道。四种碱基的编码是：

| 碱基 | 红通道 | 绿通道 |
| --- | --- | --- |
| A | 有 | 无 |
| C | 无 | 有 |
| T | 有 | 有 |
| **G** | **无** | **无** |

**G 被编码为"两个通道都没有信号"。** 当某个循环里这一簇的信号因为相位不同步、
聚合酶脱落等原因整体变暗时，识别系统就判成"无信号"——于是**被读成 G**。

所以这类仪器在 3' 端会产生长串假 G，越靠尾部越多。这不是生物学现象，
是仪器化学的系统性假象，因此专用一套逻辑处理，且**只关心"是不是 G"**。

### polyX：真实的同种碱基尾巴

建成文库时可能引入真实的同种碱基尾巴，最典型的是 **mRNA 的 polyA 尾巴**
（真核转录本 3' 端的 poly-A）。某些建库方式也会产生 polyC 等。

这类尾巴的碱基种类事先未知，所以算法要在扫描过程中**同时跟踪 A/T/C/G 四种计数**，
最后才知道尾巴是哪种。

### 为什么必须处理

这些尾巴有两个害处：一是它们**不是基因组序列**，留着会干扰下游比对
（一段 polyA 会匹配到基因组的任意 poly-A 区域，造成大量假比对）；
二是它们会让 reads 在尾部互相"看起来一样"，影响组装与去重。

---

## 2. 在算法广场中使用

本算法已在 IDEA Assistant 的**生物计算方法广场**（BIO COMPUTE MARKETPLACE）登记：

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-poly-trimming` |
| 名称 | reads 尾部 poly 修剪 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_POLY_TRIMMING_METHOD` |

**输入字段**（界面按此自动渲染表单）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| reads 文件 | 文本 | 是 | — | FASTQ 路径（`.fq` / `.fastq`，可 gzip）。当前只处理单端数据。 |
| 修剪类型 | 选择 | 是 | — | `任意同种碱基尾巴（polyX）` / `仅 G 尾巴（polyG）` / `两者都做` |
| 最短尾巴长度 | 数字 | 否 | 10 | 至少要扫过多少个碱基才认为存在尾巴；调小会更激进、也更容易误伤 |

**输出**：修剪后的 reads 文件引用，加上统计摘要（reads 数、保留数、被修剪条数、
修剪前后总碱基数与碱基去除比例）。**本算法只裁剪、不丢弃 read。**

修剪类型与内部配置的对应：

| 广场里的选项 | 对应的内部配置 |
| --- | --- |
| `任意同种碱基尾巴（polyX）` | `enabled_poly_x=True` |
| `仅 G 尾巴（polyG）` | `enabled_poly_g=True` |
| `两者都做` | 两个都开，执行顺序为 **polyG 在前、polyX 在后** |

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**
> （与 `bio-pca`、`bio-pcoa` 的现状一致）。

---

## 3. Python API 用法

### 文件进、文件出

```python
from modules.bio_analysis_function.submodules.poly_trimming import (
    PolyTrimConfig,
    trim_poly_fastq,
)

summary = trim_poly_fastq(
    "clean_R1.fastq.gz",
    "trimmed_R1.fastq.gz",
    config=PolyTrimConfig(enabled_poly_x=True, min_length_poly_x=10),
)

print(summary.changed_reads, summary.bases_removed, summary.removal_rate)
```

处理是流式的，任何时候内存里只有当前记录。
默认输出压缩方式**跟随输入**（与输出文件名的扩展名无关）。

### 单条 read

```python
from modules.bio_analysis_function.submodules.poly_trimming import (
    trim_poly_g,
    trim_poly_x,
    trim_poly_tails,
    PolyTrimConfig,
)

result = trim_poly_x(b"C" * 10 + b"A" * 20, min_length=10)
print(result.sequence, result.trimmed_bases, result.poly_base)
# b"CCCCCCCCCC" 20 b"A"

# 按上游顺序串联两步（polyG 在前）
combined = trim_poly_tails(
    b"A" * 12 + b"C" * 12 + b"G" * 15,
    config=PolyTrimConfig(enabled_poly_g=True, enabled_poly_x=True),
)
```

三个函数都返回 `PolyTrimResult`，`trimmed_bases` 为 0 表示没检测到尾巴。

### 直接读写 FASTQ

FASTQ 流式读写已上移到模块公共层，两个预处理子模块共用：

```python
from modules.bio_analysis_function.common.fastq import read_fastq, write_fastq

for record in read_fastq("sample.fastq.gz"):
    print(record.name, record.length)
```

---

## 4. 参数

`PolyTrimConfig` 的字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled_poly_g` | `False` | 启用 polyG 修剪（只针对 G 尾巴） |
| `enabled_poly_x` | `False` | 启用 polyX 修剪（自行判定碱基种类） |
| `min_length_poly_g` | 10 | polyG 的最短尾巴长度 |
| `min_length_poly_x` | 10 | polyX 的最短尾巴长度 |

`trim_poly_g` / `trim_poly_x` 另有各自的 `min_length` 关键字参数。

`min_length` 小于 1 会在构造 `PolyTrimConfig` 或调用函数时直接报错。

---

## 5. 输出解读

`PolyTrimResult` 的字段：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 修剪后的序列 |
| `trimmed_bases` | 去掉的碱基数；为 0 表示没有检测到尾巴 |
| `poly_base` | 被判定的 poly 碱基（`b"A"` 等）；没有修剪时为 `None` |
| `changed` | 属性，等价于 `trimmed_bases > 0` |

文件级接口返回 `FastqStreamSummary`（公共层类型），字段与 quality_trimming 完全一致：
`total_reads` / `kept_reads` / `changed_reads` / `dropped_reads` /
`bases_before` / `bases_after`，以及派生属性 `bases_removed` / `removal_rate`。

**怎么判断参数是否合适**：先看 `changed_reads` 占 `total_reads` 的比例。
DNA 重测序数据里通常是个位数百分比甚至 0（见第 7 节实测），
若比例明显偏高，说明 `min_length` 定得太小、正在误伤基因组序列。

---

## 6. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `trim_poly_g` | `PolyX::trimPolyG` |
| `trim_poly_x` | `PolyX::trimPolyX` |
| `min_length_poly_g` | `--poly_g_min_len` |
| `min_length_poly_x` | `--poly_x_min_len` |
| `trim_poly_tails` | 上游的调用顺序（polyG 先、polyX 后） |

本子模块范围之外的能力（reads 过滤、接头检测与裁剪、双端 overlap 校正与合并、
UMI 提取、重复检测与去重、reads 质量统计、过表达序列分析、
双端插入片段长度分布）**均已实现**，
见各子模块自己的文档与算法清单第 6 节的进度表。
**预处理线的算法层到此全部完成**；唯一不做的是上游那份 HTML 报告
（本项目里报告由前端渲染，算法层只给结构化数据）。

---

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/poly_trimming -q
```

测试 23 项，按文件分三组：

- **`tests/test_algorithm.py`（14 项）**：复现上游 `src/polyx.cpp` 的官方测试向量
  （逐位比对），polyG 与 polyX 各覆盖"应裁剪""不应裁剪"两侧，
  以及含 N、整条同碱基、串联两步等边界。
- **`tests/test_runner.py`（7 项）**：文件级接口的统计、质量串同步截断、压缩策略。
- **`tests/test_real_data.py`（2 项）**：在真实 Illumina 数据上验证不误伤、能识别真尾巴。

**真实数据验证结果**（`tests/data/fastp_R1.fq`，8 条 151bp 的真实 read）：

| 算法 | 8 条 read 上被修剪的碱基数 | 说明 |
| --- | --- | --- |
| polyG | `0, 0, 0, 0, 0, 0, 0, 0` | 这是 DNA 重测序数据，不该出现双色合成假象，全部不触发 |
| polyX | `0, 0, 0, 0, 0, 0, 0, 46` | 只有最后一条带真实 polyA 尾巴，正确识别并切掉 46 个碱基 |

第二行的 7 个 0 比那个 46 更重要：**噪声数据上的假阳性才是这类修剪算法的真正风险**。

---

## 8. 已知限制

- **只处理单端 read**。双端相关处理属于后续算法。
- **只识别大写碱基**。小写 `g` 不会被 polyG 识别；`N` 在 polyX 中同时计入四种碱基
  （因为无法判定它是哪种），因此不会中断扫描、也不会被选为 poly 碱基。
- **不看质量值**。本算法只依据碱基种类判断，与 `quality_trimming` 彼此独立。
  上游的处理顺序是质量剪切在前、poly 修剪在后。
- **`min_length` 是绝对阈值，不随数据自适应**。在富含重复序列的基因组上，
  偏小的阈值可能误伤真实的低复杂度区域。
- **整条 read 都是同种碱基时会被清空**。polyG 会截断到位置 0；polyX 的处理方式
  见下一节——这是本实现与上游的**唯一有意差异**。
- **实际计算执行链路尚未接通**。广场上的入口目前只到契约层。
- **性能未做基准**。单线程纯 Python。

### 与上游的一处有意差异

当扫描覆盖了整条 read（循环自然结束）时，上游会访问 `c_str()` 之前的内存
（`pos = -1` 时的 `data[-1]`），属于**未定义行为**，其实际结果取决于内存布局。
本实现明确取裁剪位置为 0，即整条 read 都被视为 poly 尾巴并切掉。
这一处已在 `algorithm.py` 中标注，并用测试固定。

---

## 附：开发记录（原《生物方法模块算法清单》5.4.2 节）


#### 5.4.2.1 选择依据

1. **它是处理链上紧接质量剪切的一步**。上游对单条 read 的顺序是"质量剪切 → polyG → polyX → 接头"，而 poly 修剪只看碱基种类、不看质量，与质量剪切完全正交，因此可以独立实现、独立验证。
2. **它承载一个具体的仪器知识**。polyG 不是通用算法，而是为 Illumina 双色合成化学的一个已知假象专门设计的——这类"为某个仪器的系统性偏差写一段处理"的知识，在别处很难遇到，值得完整记录。
3. **上游同样自带测试向量**（`PolyX::test()`），可以与 $5.4.1$ 一样做到逐位对齐。
4. **它是第二个需要读写 FASTQ 的算法**，正好触发了模块分层纪律中"公共能力至少出现两个真实使用方后才提取"的条件，使公共层第一次真正成形。

#### 5.4.2.2 开发状态

- 状态：**已完成**（核心算法、文件级接口、子模块说明文档、算法广场入口、单元测试、真实数据验证齐备）
- 开发目录：`modules/bio_analysis_function/submodules/poly_trimming/`
- 文件构成：`algorithm.py`（核心算法）、`runner.py`（文件级接口）、`poly_trimming.md`（本文件：使用说明 + 设计记录）、`tests/`
- 依赖：**仅 Python 标准库**（`dataclasses`、`pathlib`）与模块公共层
- 算法广场入口：已在 `IDEA Assistant Code/src/compute.ts` 登记 `bio-poly-trimming`（分类"序列预处理"，`local`，`beta`），输入 3 项字段、输出为修剪后的 reads 文件引用与统计摘要。**当前只到契约层**
- 独立可用性：只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释、数据库，也不要求上游先做过处理

**同批完成的公共层提取**：FASTQ 流式读写与"读 → 逐条变换 → 写"管道已从 `quality_trimming` 上移到 `common/fastq.py`，新增 `process_fastq()` 与公共统计类型 `FastqStreamSummary`。两个预处理子模块现在共用同一套 I/O 与统计口径，`quality_trimming` 的 `runner.py` 已改为调用公共管道。

#### 5.4.2.3 问题定义与算法原理

**polyG 的来源是一个仪器细节。** Illumina 的部分机型（NovaSeq / NextSeq / 部分 HiSeq）使用**双色合成**（two-color chemistry），只检测红、绿两个荧光通道，四种碱基的编码如下：

| 碱基    | 红通道   | 绿通道   |
| ----- | ----- | ----- |
| A     | 有     | 无     |
| C     | 无     | 有     |
| T     | 有     | 有     |
| **G** | **无** | **无** |

**G 被编码为"两个通道都没有信号"。** 当某个循环里这一簇的信号因为相位不同步、聚合酶脱落等原因整体变暗时，识别系统便判为"无信号"，于是**被读成 G**。结果是这类仪器的 3' 端出现长串假 G，且越靠尾部越多。

**因为那个假象只会产生 G**，polyG 的判定刻意做得极简——只统计"是不是 G"：从 3' 端向左逐碱基扫描，记 $m$ 为已扫范围内非 G 的数量，$i$ 为已扫长度，扫描在

$$
m > 5 \quad\text{或}\quad \left(m > \left\lfloor \frac{i+1}{8} \right\rfloor \ \text{且}\ i \ge \text{min\_length}-1\right)
$$

时停止（前半是硬上限，后半是"每 8 个碱基允许 1 个杂碱基"的软约束）。若最终扫过的长度达到 `min_length`，就把序列截断到**扫过范围内最左边那个 G** 的位置——**那个 G 本身也被切掉**。

**polyX 要在扫描中同时维护四个计数**，因为碱基种类事先未知。设 $c_b$ 为已扫范围内碱基 $b$ 的计数，$L$ 为已扫长度，则扫描停止的条件是**对四种碱基全部成立**：

$$
L - c_b > \min\left(5, \left\lfloor \frac{L}{8} \right\rfloor\right) \quad \forall b \in \{A,T,C,G\}
$$

即对每一种碱基而言，"非它的数量"都超过了允许的错配数——此时没有任何一种碱基还像尾巴。停下后取 $\arg\max_b c_b$ 作为 poly 碱基（并列时按下标顺序取，即 $A > T > C > G$）。

**两者的关键差异：**

| <br /> | polyG          | polyX                     |
| ------ | -------------- | ------------------------- |
| 跟踪的碱基  | 只关心"是否 G"      | 同时跟踪 A/T/C/G 四个计数         |
| 停止条件   | 错配数超限（两条判据）    | 四种碱基全部不像                  |
| 切点     | 扫过范围内最左边的 G 位置 | 从扫描范围左端向右找到第一个 poly 碱基的位置 |
| 用途     | 处理双色合成的系统性假象   | 处理真实存在的同种碱基尾巴             |

**两处容易被忽略的细节：**

1. **N 同时计入四种碱基**。N 表示"无法判定"，它既不能参与投票选 poly 碱基，也不该中断扫描，所以让它对四个桶各投一票、相互抵消。
2. **切点会向右回退**。扫描可能停在尾巴中间的杂质处，但真正的尾巴可能延伸到更左边，因此要从扫描范围左端**向右**找到第一个 poly 碱基的位置，从那里开始切——这样杂质左边属于尾巴的部分才一并去掉。

#### 5.4.2.4 工程实现

**单遍反向扫描。** 两者都是 $O(n)$ 的单遍扫描，无额外内存。polyG 只用两个整型变量（错配数、最左 G 位置），polyX 用长度为 4 的计数数组。

**碱基索引查表。** polyX 用 `_BASE_INDEX` 映射 `A=0 / T=1 / C=2 / G=3 / N=4 / 其他=5`，下标顺序与上游 `ATCG_BASES = {'A','T','C','G'}` 严格一致——顺序若不同，$\arg\max$ 在并列时选出的碱基就会不同。

**一处对未定义行为的修正。** 当扫描覆盖了整条 read（循环自然结束）时，上游的 `pos` 等于 read 长度，随后的回退循环会访问 `data[rlen - pos - 1]` 即 `data[-1]`——**读取** **`c_str()`** **之前的内存，属未定义行为**。本实现明确把裁剪位置取为 0，即整条 read 都被判为 poly 尾巴并切掉。这是本算法与上游**唯一的有意差异**，已在代码注释与 `poly_trimming.md` 中标注，并用测试固定。

**文件级接口与失败清理。** `runner.py` 只做粘合：把配置包装成逐条变换函数交给公共层的 `process_fastq()`。注意 poly 修剪**只改序列、不改质量**，因此裁剪后质量串必须同步截短，否则 FASTQ 记录会不自洽——这一点有专门的测试。

#### 5.4.2.5 验证记录

测试共 **23 项**（算法 14、文件级接口 7、真实数据 2），全部通过。

**与上游逐位对齐。** 直接复现上游 `PolyX::test()` 的向量：

| 输入                                                         | 参数              | 期望（上游断言）              | 结果     |
| ---------------------------------------------------------- | --------------- | --------------------- | ------ |
| `ATTTTAAAAAAAAAATAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAT` | `min_length=10` | 序列变 `ATTTT`，修剪 51 个碱基 | ✓ 完全一致 |

**语义用例覆盖。** polyG 与 polyX 各覆盖"应裁剪"与"不应裁剪"两侧：G 尾巴 15 个时截断、只有 5 个时不触发；polyA 尾巴被识别；交替序列 `ACGTTGCA` 重复 3 次不构成尾巴；含 N 的序列中 N 不中断扫描也不被选为 poly 碱基；整条同碱基时全部切掉。

**真实数据验证。** 数据同 $5.4.1$：`tests/data/fastp_R1.fq`，8 条 151bp 的真实 Illumina read。

| 算法    | 8 条 read 上被修剪的碱基数         | 说明                                |
| ----- | ------------------------- | --------------------------------- |
| polyG | `0, 0, 0, 0, 0, 0, 0, 0`  | DNA 重测序数据，不该出现双色合成假象，全部不触发        |
| polyX | `0, 0, 0, 0, 0, 0, 0, 46` | 只有最后一条带真实 polyA 尾巴，正确识别并切掉 46 个碱基 |

第二行的 7 个 $0$ 比那个 $46$ 更重要：**噪声数据上的假阳性才是这类修剪算法的真正风险**。被切掉的那条 read，末尾 50 个碱基为 `GTACATCACAAGT` 再接 37 个 A，是确凿的真实 polyA 尾巴，不是误伤。

#### 5.4.2.6 适用边界与已知限制

- **只处理单端 read**。双端相关处理属于后续算法。
- **只识别大写碱基**。小写 `g` 不会被 polyG 识别；上游与真实数据都以大写为准。
- **不看质量值**。本算法只依据碱基种类判断，与 $5.4.1$ 的质量剪切彼此独立；上游顺序是质量剪切在前、poly 修剪在后。
- **`min_length`** **是绝对阈值，不随数据自适应**。在富含低复杂度序列的基因组上，偏小的阈值可能误伤真实的同种碱基区段（例如真实的长 polyA 编码区）。
- **整条 read 都是同种碱基时会被清空**，且 polyX 在这一点上与上游有意不同（见 $5.4.2.4$）。
- **实际计算执行链路尚未接通**，广场入口目前只到契约层。
- **性能未做基准**，单线程纯 Python。

#### 5.4.2.7 待办

- 接通 `bio-poly-trimming` 的实际执行链路（Electron → 本地 Python），目前只到契约层
- 与 fastp 二进制做端到端结果对拍（当前只在单元测试层面与上游自带测试向量对齐）
- 上游会依据测序仪型号自动开启 polyG（按 read 名前缀识别双色系统），本实现只提供显式开关；待上层串联多个预处理步骤时再决定该策略放在哪一层
- 预处理线现在的进度见算法清单第 6 节；本线剩下的 fastp 功能只有统计与报告（JSON / HTML）。
