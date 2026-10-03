# read\_normalization：reads 规范化

处理链最前面的一步：把名字或质量编码上需要先归置的东西归置好。
两件事各自可开关，与 fastp 的 `--phred64` / `--fix_mgi_id` 逐位对齐。

本子模块**可独立使用**（方法 id `bio-read-normalization`），**只需要一份
FASTQ**，单端与双端都支持。

***

## 1. 它解决什么问题

### 质量编码：Phred+64 还是 Phred+33

质量字符是 ASCII，但**偏移量有两种**：现在通行 Phred+33（`'!'` = Q0），
早期/部分平台用 Phred+64（`'@'` = Q0）。两者差 31。

不转换的后果是**静默错误**：所有按质量判定的算法（质量剪切、过滤、统计）
都会算出偏移 31 的结论——质量剪切会切错位置、过滤会大量误杀或放行、
报告里的 Q30 比例完全失真，而且**不会报错**。

### 名字：MGI 的 `xxx/1`

MGI 平台的 read 名常写成 `xxx/1`、`xxx/2`。很多下游 BAM 工具要求
`<主名> <分隔><编号>` 这种形态，于是上游提供一个开关把它改成 `xxx /1`
（斜杠前插一个空格）。

> **不做自动判断。** Phred+64 的字符范围是 `'@'(64)` 到 `'~'(126)`，
> Phred+33 是 `'!'` 到 `'~'`——两者**在高质量区间完全重叠**。
> 只有出现 ASCII < 64 的字符时才能**确定**是 33；"没看到"既可能是 Phred+33
> 的高质量数据，也可能是 Phred+64。猜错就是上面那种静默错误。
> 所以本模块提供 `detect_quality_offset()` 做**单向判断**（能确定 33 时给 33，
> 否则给 `None` 表示"看不出来"），绝不猜。

***

## 2. 在算法广场中使用

方法 id `bio-read-normalization`｜分类"序列预处理"｜`local`｜`beta`。
契约位置：`IDEA Assistant Code/src/compute.ts` 的 `BIO_READ_NORMALIZATION_METHOD`。

**输入**：reads 文件、输出路径、可选 R2 与输出 R2、"输入质量编码"选择、
"修复 MGI 名字"开关、输出压缩。
**输出**：规范化后的 FASTQ（双端两份）+ 统计。Prokka

***

## 3. Python API 用法

```python
from modules.bio_analysis_function.submodules.read_normalization import (
    NormalizeConfig,
    detect_quality_offset,
    normalize_fastq,
)

# 先看看编码能不能确定（能确定 33 就给 33，否则 None）
print(detect_quality_offset("raw_R1.fastq.gz"))

# 老数据：Phred+64 转 33，顺便修 MGI 名字
summary = normalize_fastq(
    "raw_R1.fastq.gz",
    "clean_R1.fastq.gz",
    config=NormalizeConfig(input_phred=64, fix_mgi=True),
)
print(summary.render())

# 双端：成对进、成对出
normalize_fastq(
    "raw_R1.fq.gz", "clean_R1.fq.gz",
    read2_path="raw_R2.fq.gz", output2_path="clean_R2.fq.gz",
    config=NormalizeConfig(input_phred=64),
)
```

单条记录也可以单独处理：

```python
from modules.bio_analysis_function.submodules.read_nor malization import (
    NormalizeConfig, normalize_record,
)

outcome = normalize_record(record, NormalizeConfig(input_phred=64, fix_mgi=True))
print(outcome.renamed, outcome.requantified, outcome.record.quality[:4])
```

**不丢弃任何 read、不改碱基**，只动质量字符与名字。

### 原生实现

```python
from modules.bio_analysis_function.common.native import normalize_fastq

summary = normalize_fastq("raw.fq.gz", "clean.fq.gz", input_phred=64, fix_mgi=True)
```

参数一一对应；两侧输出**逐字节相同**。

***

## 4. 参数

| 参数            | 默认      | fastp           | 说明                    |
| ------------- | ------- | --------------- | --------------------- |
| `input_phred` | 33      | `--phred64` 的有无 | 只能是 33 或 64；33 表示不动质量 |
| `fix_mgi`     | `False` | `--fix_mgi_id`  | 把 `xxx/1` 改成 `xxx /1` |
| `compress`    | 跟随输入    | —               | 输出是否 gzip             |

默认（33、不修名字）下**什么都不改**，只是把数据抄一遍——输出与输入逐字节相同。

***

## 5. 输出解读

`NormalizeSummary`：`total_reads`、`renamed_reads`、`requantified_reads`、
`input_bases`、`output_bases`。`input_bases` 恒等于 `output_bases`——
规范化不改长度，这个等式成立本身就是"没动碱基"的证据。

`input_phred=64` 时 `requantified_reads` 恒等于 `total_reads`。

***

## 6. 与 fastp 的对应关系

| 本子模块                                     | fastp                      |
| ---------------------------------------- | -------------------------- |
| `convert_phred64_to_phred33`             | `Read::convertPhred64To33` |
| `fix_mgi_name`（在 `common/read_names.py`） | `Read::fixMGI`             |
| `NormalizeConfig.input_phred`            | `--phred64`                |
| `NormalizeConfig.fix_mgi`                | `--fix_mgi_id`             |

**两处照抄、不要"顺手改对"**：

1. **`max(33, q - 31)`** **的下界是必须的**。Phred+64 的 Q0 是 `'@'`(64)，减 31 得 33；
   但若输入里混了更低的字符（比如已经是 Phred+33 的数据被误当 64），减完会小于 33，
   上游把下界钉在 33。照抄。
2. **MGI 修复只看最后两个字符**：末位是 `1` 或 `2`、且倒数第二位是 `/` 才改。
   所以名字就是 `/1` 这种极端形状也会被改成   ` /1`。

***

## 7. 验证

```bash
python -m pytest modules/bio_analysis_function/submodules/read_normalization -q
python -m pytest modules/bio_analysis_function/common/native/tests/test_abi_normalize.py -q
```

Python 侧 **20 项**：转换的位移与下界、探测的单向性（能确定 33 / 看不出来）、
不改动时返回原对象、MGI 的边界形状、文件级（单端/双端/默认不改/压缩跟随/
失败删半成品/记录数不一致）。

原生层对拍 **12 项**：**输出逐字节相同** + 五个统计字段相同。

***

## 8. 已知限制

- **不做自动编码判断**（理由见第 1 节）。要按 64 处理必须显式指定。
- **只处理 Phred+64 → 33 这一个方向**。反向（把 33 转成 64）没有使用场景，
  不做。
- **不改名字里的其他部分**，也不重排名字。
- **串行实现**（原生层没有 `threads` 参数）：转换是查表、改名是插一个空格，
  瓶颈在 I/O。
- **实际计算执行链路尚未接通**，广场入口只到契约层。

***

## 附：开发记录

### 5.4.15.1 为什么把它做成一个算法模块

它本身很像"工具"（只是格式转换），但按 `开发规则.md` 3.2 的判据，
公共层的工具是"**算法必须使用的**"——而质量编码转换只在特定数据上才需要，
不是每个算法都得经过它。做成独立算法更合适：用户能直接用它，
产物（FASTQ）本身就能回答问题，也不必给其他算法各加一个参数。

顺带把 `Read::fixMGI` 也并进来——两者在**上游处理链里的位置相同**
（都是读入后、处理前的动作），都是"格式归置"，合在一起语义清楚。

### 5.4.15.2 开发状态

- 状态：**已完成（Python 侧 + 原生层）**
- 开发目录：`modules/bio_analysis_function/submodules/read_normalization/`
- 文件构成：`algorithm.py`（记录级转换与探测）、`runner.py`（文件级接口）、
  `read_normalization.md`、`tests/`
- 依赖：仅 Python 标准库与模块公共层（`fastq`、`read_names`）
- 原生实现：`common/native/src/normalize.{h,cpp}`，导出 C ABI
  `bio_normalize_fastq`（单端与双端共用入口）
- 原生库版本：0.14.0 → **0.15.0** → 0.16.0（与按 index 过滤同一轮）

### 5.4.15.3 待办

- 接通执行链路（Electron → 本地 Python / 原生层），目前只到契约层
- 与 fastp 二进制做端到端对拍

