# paired_end_base_correction：重叠区碱基校正

用两条 read 重叠处**高质量一侧**的碱基，改正**低质量一侧**的错配碱基。
**成对 FASTQ 进、成对 FASTQ 出，不改长度、不丢 read。**

算法与 fastp 1.3.x 的 `BaseCorrector::correctByOverlapAnalysis` 逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-paired-end-base-correction`）。**只要一对原始的 R1/R2 FASTQ 就能运行**——
不需要参考基因组、接头序列或数据库，也不要求先跑过别的算法。

---

## 1. 它解决什么问题

同一个 DNA 片段被测了两遍，同一位点就有两个读数。**如果两个读数不一致，而又有一边明显
更可靠，那低质量那边多半是测序错误**——把它改成对侧碱基即可。

上游的判据（本实现逐位照搬）：

| 一边 | 另一边 | 怎么办 |
| --- | --- | --- |
| 可信（**≥ Q30**） | 不可信（**≤ Q14**） | 用可信的一侧改正另一侧 |
| 不可信 | 可信 | 反过来改正 |
| 可信 | 可信 | **不动**——说不清哪边对 |
| 不可信 | 不可信 | **不动**——同样说不清 |

改正时**把可信一侧的质量值也赋过去**，上游的注释是"让它们以后共享同一个质量值"。

两个质量门槛（Q30 / Q14）是**上游写死的常量**，不是命令行参数，本算法也不暴露它们。

### 三件"刻意不做"的事

1. **只在真有错配时动手**。两条 read 完全互补（错配数为 0）或压根没检出重叠时，
   一个字节都不改。
2. **不改长度、不丢 read**。它是"就地改碱基"，不裁剪也不过滤；所有记录都会写出。
3. **带缺口的重叠跳过**。上游在调用点写着 "no gap allowed for overlap correction"——
   带缺口的重叠里"哪一位对应哪一位"本身就不可靠，逐位比较没有意义。默认参数下
   重叠走无缺口路径，因此这一条不会触发。

### 它在完整流程里的位置

上游 PE 主流程的顺序是：**质量剪切 → poly →（算重叠）→ 本算法 → 按 overlap 裁接头 →
合并**。也就是说校正发生在裁接头**之前**，因为那时重叠区还完整。

本算法只做校正这一件事：裁接头请接
[双端接头裁剪](../paired_end_adapter_trimming/paired_end_adapter_trimming.md)，
合并请接[双端 read 合并](../paired_end_merging/paired_end_merging.md)。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-paired-end-base-correction` |
| 名称 | 双端重叠区碱基校正 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_PAIRED_END_BASE_CORRECTION_METHOD` |

**输入字段**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| R1 文件 | 文本 | 是 | — | 正向 reads 的 FASTQ（`.fq`/`.fastq`，可 gzip） |
| R2 文件 | 文本 | 是 | — | 反向 reads 的 FASTQ，记录数必须与 R1 一致 |
| 输出 R1 文件 | 文本 | 是 | — | 校正后的 R1 |
| 输出 R2 文件 | 文本 | 是 | — | 校正后的 R2 |
| 最大错配数 | 数字 | 否 | 5 | 重叠区允许的错配数上限 |
| 最短重叠长度 | 数字 | 否 | 30 | 短于它一律判"不重叠"，也就不会校正 |
| 错配比例上限（%） | 数字 | 否 | 20 | 错配数还不得超过重叠长度的这个比例 |
| 允许 1 个插入/缺失 | 开关 | 否 | 关 | 打开后**若重叠带缺口则跳过校正**（与上游一致） |
| 输出压缩 | 单选 | 否 | 跟随输入 | 跟随输入 / 强制 gzip / 强制不压缩 |

**输出**：校正后的 R1 与 R2 两份 FASTQ，外加统计——总 read 对数、改过碱基的对数与
涉及的 read 条数、改正的碱基数、处理前后的总碱基数（两者相等）。

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**。

---

## 3. Python API 用法

文件级（流式，内存占用与文件大小无关）：

```python
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    correct_paired_fastq,
)

summary = correct_paired_fastq("clean_R1.fq.gz", "clean_R2.fq.gz",
                               "fixed_R1.fq.gz", "fixed_R2.fq.gz")
print(summary.render())
# 输入 read 对：1000000
# 改过碱基的 read 对：74213（7.42%），涉及 79304 条 read
# 改正的碱基：81220
# 碱基数：300000000 → 300000000（校正不改长度）
```

单对 read 级（不读不写文件，纯函数）：

```python
from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.submodules.paired_end_base_correction import (
    correct_pair_by_overlap,
)

result = correct_pair_by_overlap(
    FastqRecord("r1", seq1, qual1), FastqRecord("r2", seq2, qual2)
)
print(result.corrected, result.corrected_reads)   # 改了几位、动过几条 read
```

**没有可修的位点时也照常返回**（`corrected` 为 0），不是错误。

---

## 4. 输出解读

| 字段 | 含义 |
| --- | --- |
| `total_pairs` | 读入的 read 对数 |
| `corrected_pairs` | 至少改过一位碱基的 read 对数 |
| `corrected_reads` | 被改动过的 read **条数**（每对计 0~2） |
| `corrected_bases` | 改正的碱基总数 |
| `input_bases` / `output_bases` | 处理前后的总碱基数，**两者恒等**（不改长度） |
| `corrected_pair_rate` | 改过碱基的对数占比 |

两份输出的记录数**恒等于输入的对数**——本算法不做过滤。

---

## 5. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `correct_pair_by_overlap` | `BaseCorrector::correctByOverlapAnalysis` |
| `correct_paired_fastq` | PE 主流程里调用它的那一步 |
| 两个质量门槛 | `BaseCorrector` 里的常量 `num2qual(30)` / `num2qual(14)` |
| 重叠判定 | `OverlapAnalysis::analyze`（实现在公共层 `common/paired_overlap/`） |
| 上游的 `--correction` 开关 | 本算法本身就是那个功能，因此没有对应开关 |
| 上游裁完还要跑过滤 | 本算法不做过过滤，只校正 |

---

## 6. 验证

```bash
# 公共层子模块测试（纯 Python 规格）
python -m pytest modules/bio_analysis_function/submodules/paired_end_base_correction -q

# 原生层对拍（需要先编译原生库）
python modules/bio_analysis_function/common/native/tools/compile.py
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_paired_correction.py -q
```

Python 规格 14 项、原生对拍 5 项，覆盖：

| 覆盖范围 | 内容 |
| --- | --- |
| **上游自带向量** | `BaseCorrector::test()` 的输入/输出逐位一致——**Python 侧与原生侧各验一遍** |
| 两个方向 | 用 R1 修 R2、用 R2 修 R1 各一例，并验证"被修正时质量值一并搬过去" |
| 不修正的两种 | 两边都可信、两边都不可信，都一个字节不改 |
| 短路 | 完全互补（`diff == 0`）、完全不重叠，都不改动 |
| 三种几何 | `offset > 0`（片段长于读长）与 `offset < 0`（两端读穿）的位置对应都正确 |
| 文件级 | 所有 read 都写出（含未改动的）、gzip 跟随、中文路径、两端记录数不一致时删除两个半成品 |
| 原生对拍 | Python 与原生**两份输出逐字节相同**、六个统计字段相同、校正前后碱基数相等 |
| 多线程 | 1 线程与 4 线程输出完全一致 |
| 错误处理 | 空路径、缺失文件、记录数不一致 |

---

## 7. 已知限制

- **只改碱基，不做别的**：不裁接头、不合并、不过滤。要那三件事请分别接对应的算法。
- **质量门槛写死**（Q30 / Q14）。它们不是命令行参数，上游也没有提供调整入口；
  想改需要改代码。
- **两边都可信或都不可信时不动手**——这是上游的取舍，不是遗漏。
- **只处理重叠区**：不重叠的部分原样保留（那里没有第二份读数可参照）。
- **不校验 read 名字**：只按**位置**配对，不检查 `/1`、`/2` 后缀。
- **原生层的自动线程数取硬件并发，尚未做基准**（与另外两个双端算法同一现状）。
- **未与 fastp 二进制做端到端对拍**，当前只与 Python 参考实现对拍（它已与上游自带向量逐位对齐）。
- **执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录（设计原理与验证细节）

### 选择依据

1. **它是双端三件套的最后一块**。原计划的三个双端功能是
   "按 overlap 裁接头 / 重叠区碱基校正 / 双端合并"，前两个已分别落在
   `paired_end_adapter_trimming` 与 `paired_end_merging`，本算法补齐第三个。
2. **它是三者中唯一直接改善数据质量的一个**：裁接头是去掉多余序列、合并是拼成长序列，
   只有校正**改正错误本身**，对下游的变异检测尤其有价值（一个错配碱基就是一个假阳性）。
3. **上游自带测试向量**（`BaseCorrector::test()`），输入与期望输出都写死——
   这是和 overlap 分析一样"奢侈"的验证条件：可以逐位比对，而不是"看起来对"。

### 算法原理

上游实现（`src/basecorrector.cpp`，本实现逐行对应）：

```cpp
if(ov.diff == 0 || !ov.overlapped) return 0;      // 短路

int start1 = max(0, ov.offset);
int start2 = r2->length() - max(0, -ov.offset) - 1;

for(int i=0; i<ol; i++) {
    int p1 = start1 + i;      // R1 侧，递增
    int p2 = start2 - i;      // R2 侧，递减
    if(seq1[p1] != complement(seq2[p2])) {
        if(qual1[p1] >= GOOD_QUAL && qual2[p2] <= BAD_QUAL) {  // 用 R1 改 R2
            (*r2->mSeq)[p2] = complement(seq1[p1]);
            (*r2->mQuality)[p2] = qual1[p1];
        } else if(qual2[p2] >= GOOD_QUAL && qual1[p1] <= BAD_QUAL) {  // 用 R2 改 R1
            (*r1->mSeq)[p1] = complement(seq2[p2]);
            (*r1->mQuality)[p1] = qual2[p2];
        }
        // 否则不动
    }
}
```

**位置对应是这段代码里最容易搞错的地方**：`p1` 递增、`p2` **递减**——R2 是另一条链，
从右往左读同一段片段。两个起点 `max(0, offset)` 与 `len(R2) - max(0, -offset) - 1`
让这个循环对三种几何（offset 为正 / 为零 / 为负）都成立，也与 `statInsertSize`
那套符号约定自洽。

### 原生层

`common/native/src/paired_base_correction.{h,cpp}`：判定逻辑与 Python 版逐行对应，
**直接调用现有的 `bio::analyze_overlap`**，并走公共的
`paired_pipeline.h` 成对流水线（本算法是它的**第三个**使用方）。

C ABI 新增：

| 项 | 内容 |
| --- | --- |
| `bio_paired_correction_options_t` | overlap 四参数 + `threads` + `compress` |
| `bio_paired_correction_result_t` | 状态 + 消息 + 六个统计字段 |
| `bio_correct_paired_fastq` | 四个路径（R1/R2 输入、R1/R2 输出）+ 参数 + 结果 |
| 版本 | 原生库 **0.8.0 → 0.9.0** |

顺手做了一处小整理：两个"成对写出两份文件"的算法（裁接头、校正）的**四路径校验**
合并成同一个模板 `prepare_two_output_result`，免得两处措辞各自漂移。

### 验证记录

Python 规格 **14 项** + 原生对拍 **5 项**，全部通过；生物方法模块整体 **399 项**通过。

上游向量的**转录**花了点功夫，值得记一笔：`quality2` 的低质量字符在**索引 42**，
第一次转录时数成了 43，导致 `r2` 应有的那次修正没发生。当时靠"上游把 `r2[42]`
改了、但按质量条件不该改"这个矛盾定位到索引差一位——**用具体位置反推数据比反复数
字符可靠**。最后改为用 `b"E" * 42 + b"/" + b"E" * 13` 这样的构造式表达，不再手数。

### 适用边界与待办

- 只改碱基（见 §7）。
- 双端这条线到此收口。当时列出的"剩下的独立大块"（UMI 预处理、去重、
  失败 reads 输出）**已全部实现**，预处理线只剩**统计与报告**。
