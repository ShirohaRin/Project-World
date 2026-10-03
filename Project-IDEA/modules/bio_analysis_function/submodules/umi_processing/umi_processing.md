# umi_processing：UMI 提取

把 UMI 从 read 序列或名字里的 index 提取出来、挂到 read 名字上，序列里那一段会被剪掉。
**单端与双端都支持。**

算法与 fastp 1.3.x 的 `UmiProcessor::process` / `addUmiToName`（`src/umiprocessor.cpp`）
逐位对齐，其中用到的 `Read::trimFront` / `firstIndex` / `lastIndex`（`src/read.cpp`）
也一并照搬。

本子模块**可独立使用**：在 IDEA Assistant 的生物计算方法广场中有独立入口
（方法 id `bio-umi-processing`）。**只要一份（或一对）FASTQ 就能运行**——
不需要参考基因组、接头序列或数据库。

---

## 1. 它解决什么问题

**UMI（unique molecular identifier）** 是建库时给每个原始 DNA 分子贴的一小段随机序列。
同一个分子在 PCR 里被复制多次，它们的 read 带着**相同**的 UMI；不同分子的 UMI 不同。
因此 UMI 是"区分真重复与 PCR 重复"的依据。

但它必须**先被搬到 read 名字里**：留在序列开头会干扰比对，也不被下游工具识别。
本算法做的就是这件事——**按指定位置取出 UMI、写进名字，并把序列里那一段剪掉**。

它不比对、不聚合、不改碱基，也不丢 read。

### 六种 UMI 来源

| 取值 | UMI 来自 | 需要 R2 | 会剪序列吗 |
| --- | --- | --- | --- |
| `index1` | R1 名字里的**第一段** index | 否 | 否（index 不在序列里） |
| `index2` | R2 名字里的**最后一段** index | **是** | 否 |
| `read1` | R1 序列开头的 `length` 个碱基 | 否 | **是**（再额外跳过 `skip` 个） |
| `read2` | R2 序列开头的 `length` 个碱基 | **是** | **是** |
| `per_index` | R1 第一段 index，并上 R2 最后一段 index | 否（单端时只取 R1） | 否 |
| `per_read` | R1 与 R2 各自的序列开头 | 否（单端时只取 R1） | **是** |

前四种模式下取到的 UMI 会被挂到**两条** read 上（双端时）；后两种是"两条的拼在一起、
同样挂到两条上"。

### 挂上去的标签长什么样

标签插在名字里**第一个空格之前**——空格之后是测序仪的注释段（含 index），UMI 不能插到
它后面；名字里没有空格就追加到末尾：

```text
原名字：NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA
挂 UMI：NS500713:64:HFKJJBGXY:1:11101:20469:1097:TATAGCCT 1:N:0:TATAGCCT+GGTCCCGA
                                                  ^^^^^^^^^
```

标签形如 `:UMI`；设了 `prefix` 则是 `:prefix_UMI`；`delimiter` 可换掉那个冒号。

### 两个容易踩的地方

1. **index 的格式**：Illumina read 名形如 ``...:1097 1:N:0:TATAGCCT+GGTCCCGA``，
   空格后面才是 index 段，其中用 `+` 分成两段。解析规则完全照搬上游（从右往左扫），
   没有自行发明。
2. **`trimFront` 永远留至少 1 个碱基**（上游写成 `min(length-1, len)`）。
   所以 UMI 比 read 还长的输入不会产生空 read，而是留下 1 个碱基。

---

## 2. 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-umi-processing` |
| 名称 | UMI 提取 |
| 分类 | 序列预处理 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_UMI_PROCESSING_METHOD` |

**输入字段**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| reads 文件（R1） | 文本 | 是 | — | FASTQ 路径（`.fq`/`.fastq`，可 gzip） |
| 输出文件（R1） | 文本 | 是 | — | 处理后的 R1 |
| reads 文件（R2） | 文本 | 否 | — | 双端数据的 R2；**与"输出 R2"要么都填、要么都不填** |
| 输出文件（R2） | 文本 | 否 | — | 处理后的 R2 |
| UMI 来源 | 单选 | 是 | read1 头部 | 见 §1 的六种来源 |
| UMI 长度 | 数字 | 否 | 0 | `read1`/`read2`/`per_read` 用；0 表示不取 |
| 跳过碱基数 | 数字 | 否 | 0 | 剪掉 UMI 之后再跳过的碱基数 |
| UMI 前缀 | 文本 | 否 | — | 非空时标签写成 `prefix_UMI` |
| 标签分隔符 | 文本 | 否 | `:` | 标签与名字之间的分隔符 |
| 输出压缩 | 单选 | 否 | 跟随输入 | 跟随输入 / 强制 gzip / 强制不压缩 |

**输出**：处理后的 FASTQ（名字里带 UMI、序列开头已剪掉），外加统计——输入 read 数、
挂上 UMI 的 read 数与占比、从序列里剪掉的碱基数、处理前后总碱基数。

> **选了 `index2` / `read2` 却没给 R2 时不会报错**，只是取不到 UMI（与上游一致）。
> 这是使用上的坑，表单里应当提示。
>
> 界面当前只按 `inputSchema` 渲染表单并创建任务，**实际计算执行链路尚未接通**。

---

## 3. Python API 用法

单端：

```python
from modules.bio_analysis_function.submodules.umi_processing import (
    UmiConfig,
    process_umi_fastq,
)

summary = process_umi_fastq(
    "reads.fq.gz", "reads.umi.fq.gz",
    config=UmiConfig(location="read1", length=8),
)
print(summary.render())
# 输入 read 数：1000000
# 挂上 UMI 的 read：1000000（100.00%）
# 从序列里剪掉的碱基：8000000
# 碱基数：150000000 → 142000000
```

双端（`index1` 模式，不剪序列）：

```python
summary = process_umi_fastq(
    "R1.fq.gz", "R1.umi.fq.gz",
    read2_path="R2.fq.gz", output2_path="R2.umi.fq.gz",
    config=UmiConfig(location="index1"),
)
```

单条 read 级（不读不写文件）：

```python
from modules.bio_analysis_function.common.fastq import FastqRecord
from modules.bio_analysis_function.submodules.umi_processing import process_umi

result = process_umi(FastqRecord("read1", seq, qual), config=UmiConfig(location="read1", length=8))
print(result.umi, result.read1.name, result.read1.sequence)
```

两个解析函数也可以单独用（它们与上游同名方法一一对应）：

```python
from modules.bio_analysis_function.submodules.umi_processing import first_index, last_index

first_index("NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA")  # 'TATAGCCT'
last_index("NS500713:64:HFKJJBGXY:1:11101:20469:1097 1:N:0:TATAGCCT+GGTCCCGA")   # 'GGTCCCGA'
```

---

## 4. 输出解读

| 字段 | 含义 |
| --- | --- |
| `total_reads` | 处理的 read **条数**（双端时一对算两条，因此单/双端可直接比较） |
| `reads_with_umi` | 名字真的被改动过的 read 条数 |
| `umi_rate` | 上面的占比 |
| `trimmed_bases` | 从序列里剪掉的碱基数 |
| `input_bases` / `output_bases` | 处理前后的总碱基数 |

`reads_with_umi` 的判据是"名字被改动了"——`per_index` 在名字里没有 index 时也会留下一个
分隔符，那种情况算改动过。

**本算法不丢弃任何 read**：UMI 取不到时名字与序列原样保留，所有记录都会写出。

---

## 5. 与 fastp 的对应关系

| 本子模块 | fastp |
| --- | --- |
| `process_umi` | `UmiProcessor::process` |
| `add_umi_to_name` | `UmiProcessor::addUmiToName` |
| `first_index` / `last_index` | `Read::firstIndex` / `Read::lastIndex` |
| `trim_front` | `Read::trimFront` |
| `UmiConfig.location` | `--umi_loc` |
| `UmiConfig.length` / `skip` / `prefix` / `delimiter` | `--umi_len` / `--umi_skip` / `--umi_prefix` / `--umi_delim` |
| 上游 `--umi` 开关 | 本算法本身就是那个功能，因此没有对应开关 |

> 上游在 PE 与 SE 两条流程里都调它（`process(or1, or2)` 与 `process(or1)`），本算法
> 的单端/双端两条路径正对应这件事。
>
> 上游 `UMIOptions` 里还有一个 `separator` 字段，但 `UmiProcessor` 全程没用过它，
> 本实现同样不引入。

---

## 6. 验证

```bash
# 公共层子模块测试（纯 Python 规格）
python -m pytest modules/bio_analysis_function/submodules/umi_processing -q

# 原生层对拍（需要先编译原生库）
python modules/bio_analysis_function/common/native/tools/compile.py
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_umi.py -q
```

Python 规格 27 项、原生对拍 7 项，覆盖：

| 覆盖范围 | 内容 |
| --- | --- |
| **上游自带向量** | `Read::test()` 验证的 `lastIndex` 结果（`GGTCCCGA`）与 `firstIndex`（`TATAGCCT`）逐位一致 |
| 六种来源 | `index1` / `index2` / `read1` / `read2` / `per_index` / `per_read` 各一例，含"两条 read 都挂上标签"与"只剪对应那条的序列" |
| 命名细节 | 标签插在第一个空格前、无空格时追加、`prefix` 与 `delimiter` 生效 |
| 边界 | 名字短于 5 个字符、名字里没有 index（`per_index` 会留下一个分隔符）、UMI 比 read 长（`trimFront` 至少留 1 个碱基） |
| 单端 | 选到需要 R2 的来源时取不到 UMI、名字与序列原样保留 |
| 质量同步 | 质量串与序列按同一位置截断 |
| 原生对拍 | 单端 1500 条 / 双端 700 对，Python 与原生**输出逐字节相同**、五个统计字段相同 |
| 多线程 | 1 线程与 4 线程输出完全一致 |
| 文件级 | gzip 跟随、中文路径、R2 只给一个时拒绝、两端记录数不一致时删除半成品 |
| 错误处理 | 空路径、缺失文件、非法 `location` |

---

## 7. 已知限制

- **只搬 UMI，不做别的**：不聚合、不去重、不改碱基。**按 UMI 分组判重是另一件事**——
  本模块的 [`deduplication`](../deduplication/deduplication.md) 只按"序列是否相同"判重，
  不读名字里的 UMI，两者尚未联动。
- **`index2` / `read2` 需要 R2**，但没给 R2 时**不报错**（上游行为），只是取不到 UMI。
- **名字里 index 的解析完全按上游的扫法**：它假设 Illumina 风格的 read 名。其他命名的
  数据可能解析出空串或不合预期的片段——这与上游一致，不是本实现的偏差。
- **`trimFront` 留 1 个碱基的行为照搬上游**，即使 UMI 长度大于读长。
- **不校验 read 名字**：双端只按位置配对。
- **原生层的自动线程数取硬件并发，尚未做基准**（与其它算法同一现状）。
- **未与 fastp 二进制做端到端对拍**。
- **执行链路尚未接通**，广场入口只到契约层。

---

## 附：开发记录（设计原理与验证细节）

### 选择依据

1. **UMI 是预处理链里唯一"给数据加信息"的一步**。裁接头、修剪、过滤都在去掉东西，
   UMI 提取则把原本埋在序列里的分子身份**显式化**，是后续去重的直接前置。
2. **它的逻辑边界很清楚**：六种来源、两个解析函数、一个命名规则，没有数值计算，
   适合一次做扎实。
3. **上游既有 SE 也有 PE 路径**，正好让我们把"单端/双端两条流式路径"这个模式
   在第三个算法上再走一遍（前两个是双端专属）。

### 上游源码里的两个细节

**`Read::trimFront` 会留 1 个碱基**：

```cpp
void Read::trimFront(int len){
    len = min(length()-1, len);
    mSeq->erase(0, len);
    mQuality->erase(0, len);
}
```

`min(length()-1, len)` 意味着它永远不会把 read 剪空。长度为 0 时 `len` 变成 -1，
在 C++ 里 `erase(0, (size_t)-1)` 会清空一个本就空的串——本实现等价处理（什么都不做）。

**`firstIndex` 的右边界**：

```cpp
for(int i=len-3;i>=0;i--){
    if((*mName)[i]=='+')
        end = i-1;
    if((*mName)[i]==':')
        return mName->substr(i+1, end-i);
}
```

`substr(i+1, end-i)` 的长度参数让结果**结束于 `end`**（含），不是 `end-1`。
第一次实现时把 Python 切片写成了 `text[index+1:end]`，少取一个字符；
用上游向量 `TATAGCCT` 一验就露出来了。

### 原生层

`common/native/src/umi_process.{h,cpp}`：判定逻辑与 Python 版逐行对应，
单端走通用的 `pipeline.h`，双端走 `paired_pipeline.h`。

C ABI 新增：

| 项 | 内容 |
| --- | --- |
| `bio_umi_options_t` | `location`（1~6）/ `length` / `skip` / `prefix` / `delimiter` / `threads` / `compress` |
| `bio_umi_result_t` | 状态 + 消息 + 五个统计字段 |
| `bio_process_umi_fastq` | **单端与双端共用**：`read2_path` / `output2_path` 传 NULL 即单端 |
| 版本 | 原生库 **0.9.0 → 0.10.0** |

这是 C ABI 里**第一次出现字符串参数**（`prefix` / `delimiter`）。约定仍与既有的一致：
UTF-8、以 NUL 结尾、调用方拥有内存、被调方只读，不涉及所有权转移。

单端的统计需要跨 worker 累加，因此用了 `std::atomic`；双端的统计在写出方（单线程）
顺序累加，不需要原子量。

### 关于验证强度的一条坦白

**上游 `UmiProcessor::test()` 是空实现**：

```cpp
bool UmiProcessor::test() {
    return true;
}
```

也就是说它**没有自带测试向量**——这一点不如 overlap 分析、接头裁剪和碱基校正
（那三个都有上游写死的输入输出可以逐位比）。因此本算法的验证靠两条腿：
① `Read::test()` 里验证 `lastIndex` 的向量（那是真的）；
② 逐行对照上游源码 + 自己构造的确定性用例，覆盖六种来源与各种边界。

这个弱点已如实写在上面，不当作"和别的算法一样强"。

### 适用边界与待办

- 只搬 UMI（见 §7）。
- 与 UMI 配套的下一块是**去重**（按 UMI 或按序列合并重复分子）。去重涉及
  上游自带的布隆过滤器与最高 32 G 的内存缓冲，是独立的一大块。
