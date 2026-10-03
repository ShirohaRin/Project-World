# paired_end_adapter_trimming：双端按 overlap 裁接头

按两条 read 的重叠关系把接头剪掉，**成对 FASTQ 进、成对 FASTQ 出**。
算法与 fastp 1.3.x 的 `AdapterTrimmer::trimByOverlapAnalysis` 逐位对齐。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-paired-end-adapter-trimming`）。**只要一对原始的 R1/R2 FASTQ 就能运行**——
不需要知道接头序列、不需要参考基因组或数据库，也不要求先跑过别的算法。

---

## 1. 它解决什么问题

双端数据处理接头比单端多一条路。单端只能靠**比对已知接头序列**去找（见
[`adapter_trimming`](../adapter_trimming/adapter_trimming.md)）；双端有更直接的证据：
两条 read 来自同一个片段，把它们对齐就知道**片段到哪里为止**，再往后就是接头。

所以本算法**不需要预先知道接头长什么样**，只需要 `common/paired_overlap` 给出的重叠结论。

### 它只处理一种情形

按重叠的偏移量（`offset`）分，双端数据有三种几何：

| 偏移量 | 几何 | 有接头吗 | 本算法 |
| --- | --- | --- | --- |
| `offset > 0` | 片段**长于**读长：两条 read 只在中间重叠 | 没有 | 不动 |
| `offset == 0` | 片段长度约等于读长 | 没有 | 不动 |
| `offset < 0` | 片段**短于**读长：两条 read 都读穿了片段，**两端都读进了接头** | **有** | **裁剪** |

也就是说，它管的是"插入片段比读长还短、接头出现在两条 read 的 3' 端"这一种情况
（上游 `trimByOverlapAnalysis` 的判定就写作 `offset < 0`）。

`offset < 0` 时 `overlap_len` **就是片段长度**，所以裁剪规则很直接：

```text
keep1 = min(len(R1), overlap_len + front_trimmed2)
keep2 = min(len(R2), overlap_len + front_trimmed1)
```

`keep` 之前保留、之后全部丢弃。两条 read 都会被裁，也**都会被写出**。

### `front_trimmed1/2` 是什么：头部补偿量

注意上面两行是**交叉**的：R1 保留多长取决于 **R2 头部被剪掉多少**。这不是笔误——
片段右端少了 `front_trimmed2` 个碱基，R1 要保留到的是那个新末端。

它的用途：如果数据在到本算法之前**已经做过头部固定修剪**（例如先跑过带
`trimFront` 的质量剪切），要把当时剪掉的碱基数填进来，保留长度才算得对。

| 情形 | 填什么 |
| --- | --- |
| 数据没做过头部修剪（多数情况） | 两个都留 0 |
| 先跑过"质量剪切"并设了头部固定修剪 N（对 R1） | `front_trimmed1 = N` |
| R2 也剪过 M | `front_trimmed2 = M` |

填错的后果是裁多或裁少，**不会报错**——所以不确定时保持 0，别凭印象填。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-paired-end-adapter-trimming` |
| 名称 | 双端接头裁剪（按 overlap） |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_PAIRED_END_ADAPTER_TRIMMING_METHOD` |

**输入字段**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| R1 文件 | 文本 | 是 | — | 正向 reads 的 FASTQ（`.fq`/`.fastq`，可 gzip） |
| R2 文件 | 文本 | 是 | — | 反向 reads 的 FASTQ，记录数必须与 R1 一致 |
| 输出 R1 文件 | 文本 | 是 | — | 裁剪后的 R1 |
| 输出 R2 文件 | 文本 | 是 | — | 裁剪后的 R2 |
| 最大错配数 | 数字 | 否 | 5 | 重叠区允许的错配数上限 |
| 最短重叠长度 | 数字 | 否 | 30 | 短于它一律判"不重叠"，也就不会裁 |
| 错配比例上限（%） | 数字 | 否 | 20 | 错配数还不得超过重叠长度的这个比例 |
| 允许 1 个插入/缺失 | 开关 | 否 | 关 | 上游的 `--allow_gap_overlap_trimming`；窗口很窄 |
| R1 头部已剪碱基数 | 数字 | 否 | 0 | 见 §1 的头部补偿量说明 |
| R2 头部已剪碱基数 | 数字 | 否 | 0 | 同上 |
| 输出压缩 | 单选 | 否 | 跟随输入 | 跟随输入 / 强制 gzip / 强制不压缩 |

**输出**：裁剪后的 R1 与 R2 两份 FASTQ，外加统计——总 read 对数、裁过的对数、
输入与输出碱基数、切下的接头碱基数。

> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**。

---

## 3. Python API 用法

文件级（流式，内存占用与文件大小无关）：

```python
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    trim_paired_fastq,
)

summary = trim_paired_fastq("raw_R1.fq.gz", "raw_R2.fq.gz",
                            "clean_R1.fq.gz", "clean_R2.fq.gz")
print(summary.render())
# 输入 read 对：1000000
# 裁过接头的 read 对：312004（31.20%）；按 read 计为 624008 条
# 切下的接头碱基：18720240
# 碱基数：300000000 → 281279760
```

先跑过头部修剪的情形：

```python
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    PairedAdapterTrimConfig,
    trim_paired_fastq,
)

summary = trim_paired_fastq(
    "raw_R1.fq", "raw_R2.fq", "clean_R1.fq", "clean_R2.fq",
    config=PairedAdapterTrimConfig(front_trimmed1=8),
)
```

单对 read 级（不读不写文件，纯函数）：

```python
from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.submodules.paired_end_adapter_trimming import (
    trim_pair_by_overlap,
)

result = trim_pair_by_overlap(
    FastqRecord("r1", seq1, qual1), FastqRecord("r2", seq2, qual2)
)
if result is not None:
    print(result.read1.sequence, result.adapter1, result.trimmed_bases)
```

**没裁到时返回 `None`**——那是正常结论（这对 read 没有可裁的接头），不是错误。

---

## 4. 输出解读

| 字段 | 含义 |
| --- | --- |
| `total_pairs` | 读入的 read 对数 |
| `trimmed_pairs` | 裁过接头的对数（**两条 read 一起裁**） |
| `trimmed_reads` | 恒等于 `2 × trimmed_pairs`（上游对两条各记一次） |
| `trimmed_pair_rate` | 裁过的对数占比 |
| `input_bases` / `output_bases` | 处理前后的总碱基数 |
| `trimmed_bases` | 切下来的接头碱基总数 |
| `top_adapters` | 切下来的接头序列及其出现次数（最多 5 条） |

**本算法不丢弃任何 read**：没检出接头的对**原样写出**，长度被裁到多少都不过滤
（"该不该丢"留给 reads 过滤）。因此两份输出的条数**恒等于输入的对数**。

---

## 5. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `trim_pair_by_overlap` | `AdapterTrimmer::trimByOverlapAnalysis` |
| `trim_paired_fastq` | PE 主流程里的这一步（成对读入、成对写出） |
| 重叠判定 | `OverlapAnalysis::analyze`（实现在公共层 `common/paired_overlap/`） |
| 头部补偿量 | `trim_front1/2` 的实剪量 |

> **两点与上游主流程的差别**，都是刻意的：
>
> 1. **上游是"overlap 优先、失败回退序列匹配"**：`trimByOverlapAnalysis` 没裁到时，
>    fastp 会接着用 `trimBySequence` / `trimByMultiSequences` 去比对已知接头。
>    本算法**只做前半段**——"先按重叠裁、再用接头序列兜底"是一个算法链，
>    把两个 [reads 接头裁剪](../adapter_trimming/adapter_trimming.md) 串起来即可，
>    不塞进同一个算法里（那样输入表单会同时要接头序列，而本算法的价值恰恰是不需要它）。
> 2. **上游裁完还会跑一遍过滤**再决定写不写。本算法不做过过滤，只裁剪。

---

## 6. 验证

```bash
# 公共层子模块测试（纯 Python 规格）
python -m pytest modules/bio_analysis_function/submodules/paired_end_adapter_trimming -q

# 原生层对拍（需要先编译原生库）
python modules/bio_analysis_function/common/native/tools/compile.py
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_paired_trim.py -q
```

Python 规格 15 项、原生对拍 5 项，覆盖：

| 覆盖范围 | 内容 |
| --- | --- |
| 两端读穿 | 两条 read 都裁到片段长度，切下的两段与接头一致 |
| 长短不对称 | 一侧只穿出 20 bp、另一侧整条接头都读到，仍各裁到片段长度 |
| 头部补偿量 | `front_trimmed1=10` 时 R1 保留到片段末端、R2 保留整条片段（交叉补偿） |
| 质量同步 | 质量串与序列按同一位置截断 |
| 三种几何 | `offset > 0`（片段长于读长）、`offset == 0`、不重叠——都不裁、返回 `None` |
| 文件级 | 未裁到的对原样写出、gzip 跟随、中文路径、两端记录数不一致时删除两个半成品 |
| 原生对拍 | Python 与原生**两份输出逐字节相同**、全部统计字段相同 |
| 多线程 | 1 线程与 4 线程输出完全一致 |
| 错误处理 | 空路径、缺失文件、记录数不一致 |

---

## 7. 已知限制

- **只处理"两端读穿"这一种接头**（`offset < 0`）。片段长于读长时本来就没有接头，
  这类数据在这个算法上得不到收益——它该去跑
  [双端 read 合并](../paired_end_merging/paired_end_merging.md)。
- **不做序列匹配兜底**：见 §5 第 1 点。要兜底就把两个接头裁剪串起来跑。
- **`allow_gap` 的适用窗口很窄**（同 `common/paired_overlap` 的记录）：实测 12000 组里
  只有 443 组由缺口那一轮给出结论，常规形态里打开开关一次都没改变结论。
- **头部补偿量填错不会报错**，只会裁多或裁少（见 §1）。
- **不校验 read 名字**：只按**位置**配对，不检查 `/1`、`/2` 后缀。
- **原生层不汇总"切下的接头序列"**：那部分统计留在 Python 侧（只用于报告，不参与判定），
  因此对拍时比的是输出字节与通用统计——与单端接头裁剪同一口径。
- **原生层的自动线程数取硬件并发，尚未做基准**（与双端合并同一现状）。
- **未与 fastp 二进制做端到端对拍**，当前只与 Python 参考实现对拍（它已与上游源码逐行对应）。
- **执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录（设计原理与验证细节）

### 选择依据

1. **PE 数据找接头的**主力手段**。单端只能靠比对已知接头序列，而双端可以直接用重叠
   几何判断片段边界——这条路径不依赖"接头长什么样"，对未知建库方案更稳。
2. **它是"双端三件套"里接下来最该做的一个**。原计划的三个双端功能是
   "按 overlap 裁接头 / 重叠区碱基校正 / 双端合并"，合并已完成（`paired_end_merging`），
   本算法是第二个。它与合并**共用同一份重叠结论**，只是动作不同（合并是拼起来，
   本算法是各留一半）。
3. **上游有可逐行对照的源码**（`AdapterTrimmer::trimByOverlapAnalysis`），
   函数体很短、行为确定，适合做得扎实。

### 算法原理

上游的原始实现（`src/adaptertrimmer.cpp`）：

```cpp
bool AdapterTrimmer::trimByOverlapAnalysis(Read* r1, Read* r2, FilterResult* fr,
                                           OverlapResult ov,
                                           int frontTrimmed1, int frontTrimmed2) {
    int ol = ov.overlap_len;
    if(ov.overlapped && ov.offset < 0) {
        int len1 = min(r1->length(), ol + frontTrimmed2);
        int len2 = min(r2->length(), ol + frontTrimmed1);
        string adapter1 = r1->mSeq->substr(len1, r1->length() - len1);
        string adapter2 = r2->mSeq->substr(len2, r2->length() - len2);
        r1->resize(len1);
        r2->resize(len2);
        fr->addAdapterTrimmed(adapter1, adapter2);
        return true;
    }
    return false;
}
```

移植时保持了三件事：**只在 `offset < 0` 时动手**、**`len` 的两个补偿量交叉使用**、
**`min` 兜住越界**（`overlap_len` 理论上不会超过 read 长度，但上游用 `min` 保证，
本实现照抄）。

`Read::resize` 会**同时截断序列与质量**，本实现两处都按同一位置切片。

### 几何为什么是这样

理解这条判据的关键是**反向互补会翻转顺序**：R2 读到的接头在 R2 的 3' 端，
取反向互补后跑到了 **rc2 的开头**。所以"两端都读穿"呈现出来的样子是
"R1 的头与 rc2 的中间对齐"，也就是 `offset < 0`，而 `overlap_len` 正好是片段长度。

这一点在本模块开发时**修正过一次**：早期文档把 `offset` 的正负语义写反了
（写成"`offset > 0` 是两端读穿"）。依据是上游 `PairEndProcessor::statInsertSize`
的两个分支——`offset > 0` 时片段长是 `len1 + len2 - overlap_len`，`offset <= 0` 时
就是 `overlap_len`；用"片段 100 / 读长 80"和"片段 160 / 读长 120"两组数据实测，
两个公式分别给出 100 与 160，与真实片段长度吻合。修正同时落在
`common/paired_overlap` 的文档、C ABI 注释与测试期望值上。

### 原生层

`common/native/src/paired_adapter_trim.{h,cpp}`：判定逻辑与 Python 版逐行对应，
**并直接调用现有的 `bio::analyze_overlap`**（不跨语言逐对调用）。

它走的是 `common/native/src/paired_pipeline.h` —— 双端专用的成对流水线
（同步读 R1/R2、批次保序、有界在途）。**本算法是这条流水线的第二个使用方**，
也是当初把成对流水线从合并算法内部提到公共层的理由：按 `开发规则.md` 3.2，
公共能力要等到有两个真实使用方之后再提取。

C ABI 新增：

| 项 | 内容 |
| --- | --- |
| `bio_paired_adapter_trim_options_t` | overlap 四参数 + `front_trimmed1/2` + `threads` + `compress` |
| `bio_paired_adapter_trim_result_t` | 状态 + 消息 + 五个统计字段 |
| `bio_trim_paired_adapter_fastq` | **四个路径**（R1/R2 输入、R1/R2 输出）+ 参数 + 结果 |
| 版本 | 原生库 **0.7.0 → 0.8.0** |

### 验证记录

Python 规格 **15 项** + 原生对拍 **5 项**，全部通过；生物方法模块整体 **380 项**通过。

原生对拍的主用例含 900 对"两端读穿"的数据**外加 20 对互不相关的 read**——
既跨了批次（900 > 1024 的两批边界两侧），也验证了"没裁到的对原样写出、一条不丢"。

### 一个踩到的坑（值得记）

改造 `paired_merge.cpp` 让它共用公共流水线时，把 `FastqWriter` 从流水线内部
**移到了 `try` 块外面**，于是异常路径上 writer 还活着、文件句柄仍被占用，
`remove_file_if_exists` 在 Windows 上**静默失败**——半成品没被删掉。
合并算法的"失败时删除半成品"测试立刻抓到了这个回归。

修法是让 writer 声明在 `try` **内部**：异常时它先随栈展开析构（`~FastqWriter` 会
`gzclose`），`catch` 里的删除才能成功。公共层的 `remove_file_if_exists` 是
"尽力而为、不回错误"的，所以这一点只能靠作用域保证，不能指望它报错。

### 适用边界与待办

- 只处理 `offset < 0`（见 §1）；不做序列匹配兜底、不做过滤（见 §5）。
- 原生层自动线程数取硬件并发，**尚未做基准**（与双端合并同一现状，不写结论）。
- 未与 fastp 二进制端到端对拍。
- 下一步（按双端三件套的剩余项）：**重叠区低质量碱基校正**（`BaseCorrector`，
  上游 `--correction`）。它的源码在本地包里（`src_basecorrector.cpp`），可直接对照。
