# reference_io：参考资料读取（GenBank / FASTA）

把参考基因组读成"序列 + 拓扑 + 特征 + 位置"，供比对器与突变注释消费。
它是模块**公共层的工具**：自身不回答生物学问题，没有算法广场入口。

只取参考比对真正需要的那部分：``LOCUS``（名字、长度、拓扑、分子类型）、``DEFINITION``、
``FEATURES`` 注释表、``ORIGIN`` 序列。其余顶层字段（REFERENCE / COMMENT / 各种编号）
**原样跳过、不进模型**——它们对变异判定与注释没有作用，收进来只会多一份要维护的字段表。

## 1. 输入与输出

| 项 | 内容 |
| --- | --- |
| 输入 | GenBank 文件（明文或 gzip）、FASTA 文件（明文或 gzip），或直接给文本 |
| 输出 | `ReferenceSet` → 若干 `ReferenceSequence`（序列、`circular`、描述、`Feature` 元组） |
| 另可用 | `parse_location(text)` 单独解析位置写法，供 GBK/GFF 共用 |

## 2. Python API 用法

```python
from modules.bio_analysis_function.common.reference_io import (
    parse_location,
    read_fasta,
    read_genbank,
)

reference = read_genbank("NC_001422.gbk")      # 或 read_fasta("ref.fa")
target = reference.get("NC_001422")            # 按 SEQ_ID 取一条

print(target.length, target.circular)          # 5386 True
print(reference.total_length)                  # E-value 的分母要用它
print(reference.ids)                           # 全部 SEQ_ID

for feature in target.features_of("CDS"):
    print(feature.gene, feature.location.spans(), feature.location.strand)
    print(target.extract(feature.location)[:30])   # 负链自动反向互补

# 横跨复制原点的基因（phiX174 的基因 A 就是这么写的）
print(parse_location("join(3981..5386,1..136)").spans())
```

## 3. 三条必须知道的坐标与语义约定

1. **1-based、闭区间**（与 GenBank / IGV / samtools 一致，不是 Python 的 0-based 半开）。
2. **``complement(join(a,b))`` = 整体反向互补**，即"段序颠倒 + 每段链翻转"，
   取出来的序列等于 ``反向互补(a + b)``。写成"逐段反向互补、顺序不动"是**错的**，
   两者只有在单段时才相同。这条有专门测试钉住。
3. **不发明回绕语义**：``subsequence`` 越界直接报错。环状基因组里跨原点的特征按
   GenBank 自己的写法用 ``join(...,1..n)`` 表达（phiX174 就是这样），本层沿用同一约定，
   避免"看着能跑、其实序列错了"。

## 4. 支持范围

- 位置写法：``123``、``123..456``、``<123..456``、``123..>456``、``complement(...)``、
  ``join(...)``、``order(...)``，后三者可任意嵌套；``<`` / ``>`` 记为"端点部分是未知的"
  （``Location.complete`` 为假），不区分是哪一端。
- 一个文件多条记录；每条的 ``SEQ_ID`` 取 ``LOCUS`` 后的名字（phiX174 是 ``NC_001422``，
  注意不是 ``VERSION`` 里的 ``NC_001422.1``）。
- gzip 按**魔数**识别（与公共层 FASTQ 读写同一口径），不看扩展名。
- 序列字母限 IUPAC 核酸代码 ``ACGTRYSWKMBDHVN``，输出统一大写。

**不支持、且会明确报错**（不静默降级）：

| 情形 | 行为 |
| --- | --- |
| 蛋白记录（``LOCUS`` 单位是 ``aa``） | 报错：不能用作重测序参考 |
| 只有 ``CONTIG`` 的未完成图 | 报错：没有实际序列 |
| ``123^124`` 插入位点记法 | 报错：本层不支持 |
| 序列里出现非核酸字母 | 报错并列出具体字母（静默丢碱基会让下游坐标全体错位） |
| 声明的长度与实际序列长度不符 | 报错：文件疑似被截断 |
| GFF3 | **尚未支持**（待办；breseq 允许序列与注释来自不同文件，这一步要留给后续） |

## 5. 怎么跑测试

```bash
python -m pytest modules/bio_analysis_function/common/reference_io -q
```

本项 **58 项**全过（0.34 s）。另跑公共层回归
（``common/tests`` + ``adapter_detection/tests`` + ``paired_overlap/tests``）**79 项**全过，
确认没有碰到既有工具。

---

## 附：设计记录

### 选择依据

**为什么归公共层而不是算法子模块。** 它不回答任何生物学问题，产物只是"读进来的参考"，
注定要被比对器与注释层消费——按 `开发规则.md` 3.2 第 1 类，这是典型的工具。因此没有
广场入口，登记在 `common/README.md`（产出与消费方）。

**为什么先做它。** breseq 方向的第一个开发项不是算法，而是参考：
比对器要参考序列，突变注释要特征表与坐标，两者都从这里取。它自洽、可独立测完，
真实数据也容易拿（一条完整病毒基因组就够），适合作为这条线的地基先落地。

**为什么用"顶格即新关键字"切块，而不是按字段名逐个找。** GenBank 的顶层关键字一律
从第 1 列开始、续行必有缩进，这条规则足以稳定切块；按字段名找则要枚举 REFERENCE /
COMMENT / DBLINK / PRIMARY 等一大批字段，字段名一多就容易被真实文件里的冷门字段绊倒。

**为什么严格校验字母表。** 参考序列是所有坐标的基准。如果解析器对不认识的字符"跳过处理"，
下游全长都会错位，而且**不会有任何报错**——错得安静。宁可在这里直接失败。

### 实现要点

- 模型三个类：`Part`（一段区间）、`Location`（若干段 + 运算符 + 原始写法）、
  `Feature`（类型 + 位置 + 限定符）；容器两个：`ReferenceSequence`、`ReferenceSet`。
- 位置解析是递归下降：`complement` / `join` / `order` 三个函数各自解析参数，
  顶层逗号切分带括号深度计数。段序语义见上文第 3 条。
- 特征表解析是一个小状态机：新特征行 → 位置（可能续行）→ 限定符（`/key=value`，
  值可跨行、可带引号、可有多个同名）。列位按 GenBank 规范取第 6 列与第 22 列，
  并为"关键字列与位置列紧挨着"的写法留了退路。
- `ReferenceSequence` 在构造时校验不变式：序列非空、无空白、全大写、
  **所有特征都在序列范围内**（越界即报错，早于任何算法运行）。
- `ReferenceSet` 校验 SEQ_ID 不重复，并提供 `total_length`——breseq 的 E-value 分母是
  参考总长度，不是某一条的长度，这个量必须有唯一出处。

### 验证记录

| 组 | 内容 | 结果 |
| --- | --- | --- |
| `tests/test_model.py` | 坐标校验、链方向、混合链报错、按位置取序列、`complement(join)` 段序语义、模型不变式、ReferenceSet 契约 | 21 项通过 |
| `tests/test_genbank.py` | 位置写法解析（含嵌套、部分标记、续行）、合成记录（程序化生成，固定种子）、多条记录、六类报错路径、gzip，以及真实数据两组 | 26 项通过 |
| `tests/test_fasta.py` | 多记录、大小写与空白、拓扑恒为线状、五类报错路径、gzip | 11 项通过 |

真实数据用的是 **phiX174 全基因组**（`tests/data/phiX174_NC_001422.1.gbk`，
NCBI RefSeq `NC_001422.1`，5386 bp，环状）：守的是长度与拓扑读得对、11 个 CDS 的长度
都对得上、注释都落在序列范围内，以及**跨复制原点的第一个 CDS**
（`join(3981..5386,1..136)`，`/locus_tag="phiX174p01"`、产物为 DNA replication initiation）
取出来的序列正确。校验和与来源见 `tests/测试数据资源登记.md`。

真实文件顺带印证了两件事：① 该记录的 CDS 用的是 `/locus_tag` + `/product`，**没有 `/gene`**
——`Feature.gene` 的退路（``/gene`` → ``/locus_tag``）在真实数据上就是必需项；
② `LOCUS` 的分子类型写作 `ss-DNA`，本层只借它判断"是不是蛋白"，其余形态不影响解析。

### 已知限制与待办

- **GFF3 未支持**。breseq 允许"序列来自 FASTA、注释来自 GFF3"，后续要补一个 GFF3
  读取器并把两来源合并成一个 `ReferenceSet`（合并时要校验 SEQ_ID 对得上）。
- **`SEQ_ID` 的取法是我们定的**（取 `LOCUS` 名）。breseq 自己认哪个字段尚未核对
  （文档只说"序列与注释文件的 SEQ_ID 要一致"）。等接入比对结果与上游对拍时确认，
  必要时加一个显式覆盖参数。
- 分子类型（`ss-DNA` / `DNA` / `ds-RNA`…）目前只用来判断"是不是蛋白"，
  没有进模型。将来若要做 RNA 参考或长读数据，再考虑收进来。
- 只读不写：`.gd` 的输出格式在 `genome_diff`（待做）里实现，本层不承担写回。
