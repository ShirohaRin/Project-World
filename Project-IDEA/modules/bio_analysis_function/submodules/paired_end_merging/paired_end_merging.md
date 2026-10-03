# paired_end_merging：双端 read 合并

把一对双端 read 拼成一条更长的 read，**成对 FASTQ 进、单端 FASTQ 出**。
算法与 fastp 1.3.x 的 `OverlapAnalysis::merge` 逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-paired-end-merging`）。**只要一对原始的 R1/R2 FASTQ 就能运行**——
不需要参考基因组、注释文件或数据库，也不要求先跑过接头裁剪或质量剪切。

---

## 1. 它解决什么问题

双端测序把同一个 DNA 片段从两头各读一段。只要**插入片段短于两条读长之和**，两条 read
就会在中间重叠（读长 150、片段 200 时重叠 100 bp）——重叠部分被读了两遍，而片段本身
是完整的。把两条 read 接成一条，得到的就是**整条片段**，比两条各一半的 read 更有用：
更长的序列让比对更独特、让组装与变异检测更省事，也省掉一半的数据量。

做法是：先把 R2 取**反向互补**（记作 rc2），"两条 read 来自同一片段"就变成
"R1 与 rc2 在某个错位量上对齐"。这个错位量由公共层的 overlap 分析
（`common/paired_overlap/`，工具）算出来，本算法只负责**按它拼接**。

### 拼接几何（与上游逐位一致）

设 `ol = overlap_len`（重叠长度）、`offset` 为 rc2 相对 R1 的错位量：

| 情形 | 含义 | 输出 |
| --- | --- | --- |
| `offset > 0` | 片段**长于**读长：两条 read 只在中间重叠，片段缺的那段由 rc2 的尾部补上 | `R1[:ol+offset]` + `rc2[ol:]` |
| `offset <= 0` | 片段**短于**读长：两端读穿，重叠长度本身就是片段长度 | `R1[:ol]`（R1 的一个前缀） |
| 判不重叠 | 两条 read 不相干 | **不输出**，只计入统计 |

> `offset < 0` 与 `offset == 0` 合并成一行是有意的：上游的 `max(0, offset)` 让这两者
> 给出**同一个**结果。别按早期文档的说法把 `offset < 0` 写成 `R1[:ol+offset]`——那会
> 在片段短于读长时把保留长度算成负数方向。

具体地，长度按上游的算法取：

```text
len1 = overlap_len + max(0, offset)
len2 = len(R2) - overlap_len        （仅当 offset > 0，否则为 0）
```

输出名为 `原R1名字 merged_{len1}_{len2}`。

### 重叠区不做质量共识

**这是刻意的、也是上游的做法**：重叠区域**完全采用 R1 的序列和质量**，rc2
只在 `offset > 0` 时把**重叠之后**的尾部补上去。因此：

- 重叠区的错配**不会被纠正**，R1 的碱基原样保留；
- rc2 的质量字符串**只反转、不解码**（质量本身不随碱基互补变化）；
- 输出质量 = `R1 质量前缀` + `rc2 反转后的质量尾部`。

"用重叠区的高质量碱基去校正低质量碱基"是**另一个独立算法**（`fastp` 里的
`BaseCorrector::correctByOverlapAnalysis`，对应 `-c/--correction`），
不属于本算法，不要混进来。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-paired-end-merging` |
| 名称 | 双端 read 合并 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_PAIRED_END_MERGING_METHOD` |

**输入字段**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| R1 文件 | 文本 | 是 | — | 正向 reads 的 FASTQ（`.fq`/`.fastq`，可 gzip） |
| R2 文件 | 文本 | 是 | — | 反向 reads 的 FASTQ，记录数必须与 R1 一致 |
| 输出文件 | 文本 | 是 | — | 合并结果 FASTQ 的路径 |
| 最大错配数 | 数字 | 否 | 5 | 重叠区允许的错配数上限 |
| 最短重叠长度 | 数字 | 否 | 30 | 短于它一律判"不重叠" |
| 错配比例上限（%） | 数字 | 否 | 20 | 错配数还不得超过重叠长度的这个比例 |
| 允许 1 个插入/缺失 | 开关 | 否 | 关 | 打开后再多扫一轮带缺口的比对，窗口很窄（见 §7） |
| 输出压缩 | 单选 | 否 | 跟随输入 | 跟随输入 / 强制 gzip / 强制不压缩 |

**输出**：成功合并的 reads 组成的单端 FASTQ，外加统计——
总 pair 数、合并数、未合并数、输入碱基数、输出碱基数、走缺口路径的合并数。

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**。

---

## 3. Python API 用法

文件级（流式，内存占用与文件大小无关）：

```python
from modules.bio_analysis_function.submodules.paired_end_merging import (
    merge_paired_fastq,
)

summary = merge_paired_fastq("raw_R1.fq.gz", "raw_R2.fq.gz", "merged.fq.gz")
print(summary)
# PairedEndMergeSummary(total_pairs=1000000, merged_pairs=823145,
#                       unmerged_pairs=176855, input_bases=..., output_bases=..., gap_overlaps=0)
```

调参数：

```python
from modules.bio_analysis_function.submodules.paired_end_merging import (
    PairedMergeConfig,
    merge_paired_fastq,
)

summary = merge_paired_fastq(
    "raw_R1.fq", "raw_R2.fq", "merged.fq",
    config=PairedMergeConfig(diff_limit=8, require=20),
)
```

单对 read 级（不读不写文件，纯函数）：

```python
from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.submodules.paired_end_merging import merge_pair

merged = merge_pair(FastqRecord("r1", seq1, qual1), FastqRecord("r2", seq2, qual2))
if merged is not None:
    print(merged.name, len(merged.sequence), merged.has_gap)
```

未检测到 overlap 时 `merge_pair` 返回 `None`——**"不重叠"是正常结论，不是错误**。

---

## 4. 输出解读

| 字段 | 含义 |
| --- | --- |
| `total_pairs` | 读入的 read 对数（两端的记录条数） |
| `merged_pairs` | 成功合并、写进输出的条数 |
| `unmerged_pairs` | 没找到 overlap、被跳过的对数 |
| `input_bases` | 两端读入的总碱基数 |
| `output_bases` | 写出读段的总碱基数 |
| `gap_overlaps` | 其中由"允许 1 个插入/缺失"那一轮给出结论的条数 |

**只有成功合并的 read 会被写出**；未找到 overlap 的 read 对**不进这个输出文件**
——本算法只产出一个文件，没有地方安放它们，因此它们在结果里是"未写出"。
（上游 fastp 的 merge 模式下，未合并的 read 会照常写进 `--out1` / `--out2`；
给了 `--include_unmerged` 才把两边都塞进同一个合并输出文件。本算法尚未实现这两种
出口，见 §7。）

于是：

- 输出条数 = `merged_pairs`；
- 末端的 `len1 + len2` 就是这条合并 read 的长度；
- 想知道"合掉了多少、剩下多少"，看 `merged_pairs / total_pairs`。

---

## 5. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `merge_pair` | `OverlapAnalysis::merge` |
| `merge_paired_fastq` | PE 主流程的 merge 模式（`-m/--merge`）：逐对调 `merge()`，只写成功合并的 read |
| overlap 判定 | `OverlapAnalysis::analyze`（实现在公共层 `common/paired_overlap/`） |
| 尚未实现 | 未合并 read 的出口（`--out1` / `--out2` / `--include_unmerged`）、双端模式按 overlap **裁接头**、重叠区低质量碱基**校正** |

> 上游在 merge 前会**重新对裁剪后的 read 做一次 overlap 分析**（修复 #675 的那处改动），
> 并在合并后对结果跑一遍过滤，只写出通过过滤的。本算法不做裁剪也不做过过滤，
> 因此直接对原始输入做一次分析——两处在语义上并不冲突，但串成完整流程时要注意顺序。

---

## 6. 验证

```bash
# 公共层子模块测试（纯 Python 规格）
python -m pytest modules/bio_analysis_function/submodules/paired_end_merging -q

# 原生层对拍（需要先编译原生库）
python modules/bio_analysis_function/common/native/tools/compile.py
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_merge.py -q
```

Python 规格 11 项、原生对拍 6 项，覆盖：

| 覆盖范围 | 内容 |
| --- | --- |
| 上游向量 | `OverlapAnalysis::test()` 第一组（offset 10 / 重叠 79）逐位复现长度与序列 |
| 三种几何 | 正 offset（补 rc2 尾部）、offset = 0（不补）、负 offset（只取 R1 前缀） |
| 不重叠 | 返回 `None`，不进输出 |
| 质量 | 质量字符串**只反转**、尾部截取位置与序列一致 |
| 缺口路径 | `GAP_ONLY_VECTOR`：关掉开关判不重叠，打开后合并且 `has_gap` 为真 |
| 文件级 | 只写成功合并的 pair、gzip 跟随、中文路径、两端记录数不一致时删除半成品 |
| 原生对拍 | Python 与原生**输出逐字节相同** + 全部统计字段（含 `gap_overlaps`）相同 |
| 多线程 | 1 线程与 4 线程的输出、统计完全一致 |
| 错误处理 | 空路径、缺失文件、记录数不一致 |

---

## 7. 已知限制

- **只做合并，不做校正**：重叠区的错配不会被修正，R1 的质量也不会和 rc2 融合
  （见 §1）。低质量碱基校正属于另一个算法。
- **未合并的 read 没有出口**：本算法只产出一个合并输出，因此没找到 overlap 的
  read 对不会出现在任何文件里（只计入 `unmerged_pairs`）。上游 merge 模式下
  它们会照常写进 `--out1` / `--out2`，或用 `--include_unmerged` 合并进同一个文件；
  本算法尚未实现这两个出口。若产品上需要"一条都不丢"，应做成显式选项。
- **`allow_gap` 的适用窗口很窄**：要三条同时成立——重叠区不长、indel 贴着比对区末尾、
  且无缺口那一轮恰好过不去。实测 12000 组里只有 443 组由缺口那一轮给出结论；
  读长 30~151、重叠上百的常规形态里打开开关一次都没改变结论。上游 PE 主流程
  默认也不开它。保留是为了与上游行为一致。
- **空 read 的退化行为照搬上游**：一条 read 为空、另一条够长时，overlap 分析会返回
  长度为 0 的"重叠"。为逐位兼容而保留，未做修正。
- **不校验 read 名字**：只按**位置**配对，不检查 `/1`、`/2` 后缀或名字是否对应。
  上游的 merge 路径也只按位置走。名字明显对不上而位置对的输入，会被照常合并。
- **两端记录数必须一致**：不一致时抛格式错误并删除已写出的半成品输出。
- **执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录（原《生物方法模块算法清单》5.4.7 节）


> 与 $5.4.6$ 相对：overlap 分析是**工具**（产出自身没意义），本算法是**真正的算法**——
> 它产出合并后的单端 FASTQ，产物本身就是结论，因此按 `开发规则.md` 3.2 进
> `submodules/`，并有独立广场入口。这也是三个双端功能里唯一符合"独立可用"定义的。

#### 5.4.7.1 选择依据

1. **它是三个双端功能里唯一能独立回答问题的一个**。按 $5.4.6.5$ 的划分，三个候选是
   ① 按 overlap 裁接头、② 重叠区低质量碱基校正、③ 双端合并。前两个的产物都还要
   接着处理（裁完要过滤、校正完要合并），只有合并**一步到位**：输入一对 R1/R2，
   输出一条可直接进入下游比对与组装的更长 read。
2. **它复用已经逐位对齐的 overlap 结论**。判定部分在 $5.4.6$ 已经与上游测试向量对拍
   通过，本算法只做几何拼接，逻辑短、边界清楚，是接入"双端"这条支线风险最低的起点。
3. **有上游可逐行对照的源码**（`OverlapAnalysis::merge`），且它的行为足够"硬"——
   重叠区采用谁、质量怎么走、名字怎么拼，全部有确定答案。

#### 5.4.7.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**；原生实现导出 `bio_merge_paired_fastq`
  （原生库版本 **0.7.0**），与 Python 版**逐字节对拍**
- 位置：`modules/bio_analysis_function/submodules/paired_end_merging/`
  （`algorithm.py` 单对规格、`runner.py` 文件级流式参考实现、`paired_end_merging.md`、`tests/`）
- 依赖：模块公共层——`common/fastq.py`（流式读写）、
  `common/paired_overlap/`（overlap 判定，**工具**）、`common/sequences.py`（反向互补）
- 原生层：`common/native/src/paired_merge.{h,cpp}`（核心 + **专用成对流水线**）、
  C ABI `bio_merge_paired_fastq`
- 广场入口：已登记 `bio-paired-end-merging`（分类"序列预处理"，`local`，`beta`），
  8 项输入字段；**当前只到契约层**
- 独立可用性：只需要一对原始的 R1/R2 FASTQ 即可运行；不要求先做接头裁剪或质量剪切
  （合并靠 overlap 判定，不靠接头序列）

#### 5.4.7.3 问题定义与算法原理

把 R2 取**反向互补**（rc2）后，"两条 read 来自同一片段"变成"R1 与 rc2 在某个错位量
`offset` 上对齐"。这个错位量由 $5.4.6$ 的 `analyze_overlap` 给出，本算法只负责**拼接**。

设 `ol = overlap_len`：

| 情形            | 含义                                       | 输出                            |
| ------------- | ---------------------------------------- | ----------------------------- |
| `offset > 0`  | 片段**长于**读长：只中间重叠，片段缺的那段由 rc2 尾部补上        | `R1[:ol+offset]` + `rc2[ol:]` |
| `offset <= 0` | 片段**短于**读长：两端读穿，重叠长度本身就是片段长度             | `R1[:ol]`（R1 的一个前缀）           |
| 判不重叠          | 两条 read 不相干                              | **不写进输出**，只计入统计               |

（`offset < 0` 与 `offset == 0` 归成同一行：上游的 `max(0, offset)` 让这两者结果相同。）

长度照上游取：

```text
len1 = overlap_len + max(0, offset)
len2 = len(R2) - overlap_len        （仅当 offset > 0，否则 0）
```

输出名 = `原R1名字 merged_{len1}_{len2}`。

**三处"刻意不做事"，都是上游行为，也都写进了测试与 README**：

1. **重叠区不做质量共识**。重叠区域**完全采用 R1 的序列和质量**，rc2 只补重叠之后的
   尾部。因此错配不会被纠正，R1 的碱基原样保留。"用重叠区高质碱基校正低质碱基"是
   上游另一个函数（`BaseCorrector::correctByOverlapAnalysis`，对应 `-c/--correction`），
   **属于 ② 那件独立算法，不混进这里**。
2. **质量字符串只反转、不解码**。质量值本身不随碱基互补变化，因此
   `reverse2_quality = R2.quality[::-1]`，与序列的反向互补**不是**同一个操作。
   输出质量 = `R1 质量前缀` + `rc2 反转质量的尾部`。
3. **不校验 read 名字**。只按**位置**配对，不检查 `/1`、`/2` 后缀。上游 merge 路径
   也只按位置走，因此这里保持一致。

#### 5.4.7.4 原生层：专用成对流水线与 C ABI

单端流水线（$5.4.0.5$ 的 `pipeline.h`）处理不了这个算法——它一次只喂一条 read。
因此 `paired_merge.cpp` 里放了**专用的成对流水线**，复用通用层的三条纪律
（按批次传递 / 槽位环 / 有界在途），但槽里装的是 **read 对**：

```text
┌────────────────┐        ┌────────────┐        ┌──────────┐
│ pair reader    │──批次──▶│ worker × N │──批次──▶│ writer   │
│ 同步读 R1、R2  │        │ 逐对合并    │        │ 单输出    │
└────────────────┘        └────────────┘        └──────────┘
```

- **同步读取**：每次都从 R1、R2 各取一条；**一端先读完而另一端还有记录**时抛
  `FastqFormatError`（"两份配对 FASTQ 的记录数不一致"），并删除半成品输出。
- **批次保序**：批次下标单调，writer 严格按 0、1、2…… 写出，因此**多线程与单线程
  输出逐字节一致**（有常驻测试守着）。
- **内存有界**：在途批次数有上限，与输入文件大小无关。
- **worker 内直接调** **`bio::analyze_overlap`**：满量数据**不**一对一次跨语言调用
  （那样光调用开销就盖过计算）。C ABI 那个 `bio_analyze_overlap` 只服务对拍与小批量。
- **统计**用 `PairedMergeStats`（`total_pairs / merged_pairs / unmerged_pairs /
  input_bases / output_bases / gap_overlaps`），字段与 Python 侧 `PairedEndMergeSummary`
  **一一对应**，两边可直接比较——对拍测试就是这么做的。

**成对流水线已上移到公共层**：它原先长在 `paired_merge.cpp` 内部。按 `开发规则.md` 3.2
"公共能力至少出现两个真实使用方后才提取"，在**第二个双端算法（双端按 overlap 裁接头，
`submodules/paired_end_adapter_trimming`）落地时**，它被提取为
`common/native/src/paired_pipeline.h`：同步读 R1/R2、批次保序、有界在途这套编排现在
由两个双端算法共用，批量大小与线程口径不会各自漂移。**本算法的行为没有变化**——
提取后与 Python 参考实现的逐字节对拍仍然全过，其中就包含"失败时删除半成品"一项。

**C ABI 新增**：

| 项                            | 内容                                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------------------- |
| `bio_paired_merge_options_t` | `diff_limit` / `require` / `diff_percent_bp`（万分之一）/ `allow_gap` / `threads` / `compress` |
| `bio_paired_merge_result_t`  | 状态 + 消息 + 上面六个统计字段                                                                       |
| `bio_merge_paired_fastq`     | 三个路径（R1 / R2 / 输出）+ 参数 + 结果；**不新增命令行入口**                                                 |
| 版本                           | 原生库 **0.6.0 → 0.7.0**                                                                    |

`compress` 与其它算法同口径：**显式指定优先，否则跟随输入**（按魔数判断，任一输入是
gzip 就输出 gzip）。

#### 5.4.7.5 验证记录

测试 **17 项**全通过：

- Python 侧 **11 项**（`submodules/paired_end_merging/tests/`：算法 8 + 文件级 3）；
- 原生层对拍 **6 项**（`common/native/tests/test_abi_merge.py`）。

| 覆盖范围     | 内容                                                              |
| -------- | --------------------------------------------------------------- |
| **上游向量** | `OverlapAnalysis::test()` 第一组（offset 10 / 重叠 79）逐位复现长度与序列       |
| 三种几何     | 正 offset（补 rc2 尾部，且尾部位置正确）、offset = 0（不补）、负 offset（只取 R1 前缀）    |
| 不重叠      | `merge_pair` 返回 `None`、文件级不写出该 pair                             |
| 质量       | 质量字符串**只反转**、尾部截取位置与序列一致（与序列反向互补分开验证）                           |
| 缺口路径     | `GAP_ONLY_VECTOR`：关掉开关判不重叠，打开后合并且 `has_gap` / `gap_overlaps` 为真 |
| 文件级      | 只写成功合并的 pair、gzip 跟随输入、中文路径、缺失文件报错                              |
| 错误处理     | 两端记录数不一致时报错**并删除半成品输出**；空路径由 C 侧拦下                              |
| 原生对拍     | Python 与原生**输出逐字节相同**，六个统计字段全部相同                                |
| 多线程      | 1 线程与 4 线程的输出、统计完全一致                                            |

#### 5.4.7.6 适用边界与待办

- **只做合并，不做校正**（见 $5.4.7.3$ 第 1、2 点）。低质量碱基校正属于后续算法，
  不要与这里混在一起。
- **`allow_gap`** **的适用窗口很窄**（同 $5.4.6.3$ 第 3 点）：实测 12000 组里只有 443 组
  由缺口那一轮给出结论，常规形态里打开开关一次都没改变结论。上游 PE 主流程默认
  也不开它。保留是为了与上游行为一致。
- **空 read 的退化行为照搬上游**：一条 read 为空、另一条够长时，overlap 分析会返回
  长度为 0 的"重叠"。为逐位兼容而保留，未做修正。
- **未合并的 read 没有出口**：本算法只产出一个合并输出，因此没找到 overlap 的 read 对
  不出现在任何文件里（只计入 `unmerged_pairs`）。上游 merge 模式下它们会照常写进
  `--out1` / `--out2`，或用 `--include_unmerged` 把两边一起塞进同一个合并输出；
  **本算法尚未实现这两个出口**。若产品上需要"一条都不丢"，应做成显式选项，
  而不是改默认行为。
- **上游在 merge 前会重新对裁剪后的 read 做一次 overlap 分析**（修复上游 #675 的那处
  改动），并在合并后对结果跑一遍过滤、只写出通过过滤的。本算法不做裁剪也不做过过滤，
  因此直接对原始输入分析一次——语义上不冲突，但串成完整流程时要注意顺序。
- **原生侧的自动线程数取硬件并发，但这一条尚未做基准**。质量剪切/poly 修剪的
  单线程结论来自实测（$5.4.0.5$），reads 过滤的硬件并发结论也来自实测；
  合并的计算量（每个错位量都要扫一遍）看起来更重，但**没有测过**，
  因此这里只记"当前的取值"，不写结论。要动它先跑基准。
- **未与 fastp 二进制做端到端对拍**，当前只与 Python 参考实现对拍。
- **执行链路尚未接通**，广场入口只到契约层。
