# missing_coverage：覆盖度与缺失区（MC）

从读段深度找"整段没有 read"的区域，作为**大片段缺失**的证据。这是参考比对与变异检测线
（breseq 方向）的第五项，对应上游的 `MC` 证据线（missing coverage evidence）。

> **当前状态：分片 A–C 完成（34 项测试）。** 覆盖剖面 → 负二项拟合与阈值 → 缺失区检测这条
> 链路已经跑通；合成"整段被删"的数据上缺口边界逐位吻合。**分片 D（`MC` 证据行构造 / 与真实
> 产物逐字段核对）与 `MC + JC → DEL` 的判定尚未做**——后者本来就在编排层（算法清单 5.5.2），
> 前者见「已知限制与待办」。算法广场入口**未登记**（与 `.gd` 一样，它是证据线的一环，
> 等编排层连成完整流程再谈入口）。

## 1. 现在能做什么

| 项 | 内容 |
| --- | --- |
| 输入 | 一组参考序列 + 比对结果（**SAM 文件路径，或一堆 `SamRecord`**） |
| 输出①剖面 | `CoverageProfile`：一条参考序列的逐位深度（0-based，含零覆盖位置）、均值、总体方差、深度直方图 |
| 输出②分布 | `CoverageDistribution`：按矩估计得到的负二项参数（均值、方差、离散参数 `r`），或退回泊松；`pmf` / `cdf` / `quantile` |
| 输出③阈值 | `low_coverage_cutoff`：**深度 ≤ 它算低覆盖**，由"左尾概率 = 0.05/√L"定出 |
| 输出④缺失区 | `MissingCoverageRegion`：疑似缺失的 0-based 闭区间 `[start, end]`、长度，以及四个边缘深度（`left/right_inside/outside_cov`） |
| 一次跑完 | `analyze_missing_coverage`：参考 + SAM → 逐条序列的 `MissingCoverageAnalysis`（分布、阈值、区间） |

## 2. Python API 用法

```python
from modules.bio_analysis_function.common.reference_io import read_genbank
from modules.bio_analysis_function.submodules.missing_coverage import (
    MissingCoverageSettings,
    analyze_missing_coverage,
    build_coverage_profile,
    find_missing_coverage,
    iter_coverage_profiles,
)

reference = read_genbank("NC_001422.gbk")

# 只要某条序列的逐位深度
profile = build_coverage_profile(reference, "mapped.sam", "NC_001422")
profile.depths[:10]          # (0, 0, 1, 3, 3, 2, ...)
profile.mean, profile.variance, profile.histogram()

# 逐条序列拿到剖面
for profile in iter_coverage_profiles(reference, "mapped.sam"):
    print(profile.seq_id, profile.length, profile.mean)

# 一次跑完：分布 + 阈值 + 缺失区
for analysis in analyze_missing_coverage(reference, "mapped.sam"):
    print(analysis.seq_id, analysis.cutoff, len(analysis.regions))
    for region in analysis.regions:
        print(region.start, region.end, region.length,
              region.left_outside_cov, region.left_inside_cov,
              region.right_inside_cov, region.right_outside_cov)

# 也可以自己先拿剖面，再单独做检测（阈值可调）
analysis = find_missing_coverage(profile, tail_probability=0.01)
```

参数写在 `MissingCoverageSettings` 里：`tail_probability`（默认 0.05）与
`min_mapping_quality`（默认 1）。

## 3. 参数

| 参数 | 默认 | 含义与取舍 |
| --- | --- | --- |
| `min_mapping_quality` | 1 | 只有 MAPQ ≥ 它的 read 才计入覆盖。上游的 MC 看的是 **unique-only** 位置；我们比对器的 MAPQ 三档（60 / 20 / 0）里 0 就是多命中，故默认 1。设成 0 会把重复区的 read 也算进来，重复区就不会被当成低覆盖——那是另一个口径，不是更快 |
| `tail_probability` | 0.05 | 阈值取"左尾概率 = 它 / √L"的深度。上游写明的就是 0.05；调小更严（阈值更低、只报更彻底的缺口），调大更松 |
| 拟合方法 | 固定：矩估计 | **不做成参数**。上游只说"拟合负二项"，没说怎么拟合；矩估计（`r = μ²/(σ²−μ)`）是一行能写清、能反解的选法。要换极大似然是**方法**的替换，不是开关能解决的问题 |
| 阈值上限 | 固定：不超过平均深度 | 护栏：阈值高于均值意味着"一半以上的位置都算低覆盖"，那样找到的区域没有意义。见 `distribution.low_coverage_cutoff` 的说明 |

## 4. 必须知道的约定

1. **位置是 0-based**（与堆叠、共识调用一致）。`.gd` 是 1-based，写证据行时再换算。
2. **只有 `M`/`=`/`X` 贡献覆盖**（差分包记在 `[起点, 终点)`）。`D`/`N` 上 read 没有碱基、
   `S` 根本没比上，都不算覆盖——这与"覆盖度 = 这里被多少条 read 盖住"的直觉一致。
3. **读段末端被裁剪的碱基仍然算覆盖**。裁剪（`consensus_calling` 分片 D）只是让这些碱基
   对**突变判定**失去信息，不会让这条 read 消失；覆盖度问的是"这里有没有 read 来过"。
4. **不依赖其它子模块，也不复用堆叠**。输入只到公共层（参考集合 + SAM）；覆盖度只需要深度，
   从 CIGAR 摊差分数组比"为每个碱基观测建对象"省得多。同为 `RA` 线的两份输入契约也保持独立。
5. **分布是逐条参考序列拟合的**，阈值里的 `L` 也是**这条序列**的长度（不是全基因组总长）——
   与上游一致；这也让质粒（短、覆盖度另算）能独立判定。
6. **没有可用覆盖的序列不做分析**：均值 0 时 `fit_coverage_distribution` 返回 `None`，
   分析结果里 `distribution` / `cutoff` 都是 `None`、`regions` 为空。不能编一个阈值出来，
   否则"这里其实什么都没测到"会被读成"这里检测到缺失"。
7. **种子的硬信号是"零覆盖"**：一段低覆盖区要被报出来，其中**至少有一个位置的深度是 0**
   （"一条 read 都没有"）。只有"低但不为零"的区域不报——那更像可比对性差、不是缺失。
8. **扩展穿过的是"低覆盖"而不是"重复区"**：上游说"extend through repeat regions"，在覆盖
   剖面上重复区本来就低（重复区的 read 被 MAPQ 0 挡在外面），于是它自然被同一段吃进去；
   我们不再单独去查参考的重复区结构。
9. **高覆盖的间隔不合并**：两段低覆盖之间隔着"深度 > 阈值"的位置就不合并——中间有 read，
   说明那是两件事，不是一次缺失。
10. **报出来的是证据，不是突变**：`MC + JC → DEL`（端点靠 JC 或两侧同向重复）是编排层的规则。
    本模块不产出 `DEL`，也不决定长度从哪算到哪。

## 5. 怎么跑测试

```bash
python -m pytest modules/bio_analysis_function/submodules/missing_coverage -q
```

**34 项**全过（覆盖剖面 **12** + 分布与阈值 **15** + 缺失区检测 **7**）。用例全部**手写 SAM
记录**或**直接构造深度元组**，期望值能手算，不调用比对器——输入契约就是公共层的 SAM。

- **覆盖剖面**覆盖：一条 read 的比对区段被算到（`POS` 1-based ↔ 内部 0-based）、`D`/`N` 不
  贡献覆盖、`S`/`I` 不贡献覆盖、参考两端的零填充、MAPQ 过滤（默认只看 unique）、未比对与
  无 CIGAR 记录被跳过、多条 read 的深度叠加（均值/方差/直方图手算：`(1,2,1,0)` → μ=1、σ²=0.5）、
  多条参考序列按文件顺序各出一份、参考名不存在报错、比对越过参考末端报错、最低质量不能为负。
- **分布与阈值**覆盖：**矩估计能反解手算值**（`(0,0,5,5,5,5)` → μ=10/3、σ²=50/9、`r=5`、`p=0.6`）、
  未过度离散退回泊松、平坦覆盖（σ²=0）、全零覆盖不拟合、概率归一（负二项与泊松各一条）、
  负深度概率为 0、退化分布（μ=0）全部质量在 0、CDF 单调有界、**分位点的定义**（`cdf(q) ≥ α >
  cdf(q−1)`）、参考越长阈值越低、阈值不超过均值、参数校验。
- **缺失区检测**覆盖：正常覆盖中的零覆盖段被报出且四个边缘深度正确、**没有种子不报**（低但非零）、
  **扩展把邻近的低覆盖一起吃进来**、贴着序列两端的区间没有外侧值、全零序列不分析、以及
  **从 SAM 到缺失区的端到端**（整齐铺满的 read 里挖掉一段 → 缺口边界逐位吻合），
  外加 `min_mapping_quality` 真的会改变结果（全 MAPQ 0 时默认口径"什么都测不到"）。

## 附：设计记录

### 上游是什么（带出处）

- **覆盖分布**：上游把"各深度的位置数"先按泊松理解（read 随机撒开），再指出实际数据**过度离散**，
  改用**负二项**拟合。见 [Barrick 2014, BMC Genomics](https://pmc.ncbi.nlm.nih.gov/articles/PMC4300727/)
  与 [breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html) 的
  "Missing coverage evidence (MC)" 一节。
- **阈值**：上游原话是把种子区间"向外传播、并穿过 unique-only 覆盖低于**阈值**的区域；阈值由
  负二项拟合给出左尾概率 **0.05 / sqrt(当前参考序列长度)** 的那个覆盖值"（同上，BMC Genomics 正文）。
- **种子与扩展**："finds places in the genome where no reads align and then extends these
  intervals in both directions and through repeat regions. Extension of the MC interval is
  stopped when uniquely mapped read coverage exceeds a threshold"（Deatherage & Barrick,
  *Methods Mol Biol* 2014 的 MC 段）。
- **MC 的作用**：`MC` 本身只报"这一段没有覆盖"；`DEL` 的精确端点靠 `JC`（或两端是**同向的同一
  重复**时靠 MC 单独定）。见 [breseq · Output · Evidence Display](https://gensoft.pasteur.fr/docs/breseq/0.35.7/output.html)。
- **证据行**（真实产物，我们已内嵌在 `.gd` 的测试里）：
  `MC	62	.	NC_000913	1299499	1300697	1198	0	left_inside_cov=0	left_outside_cov=55	right_inside_cov=0	right_outside_cov=55`。

### 为什么不复用共识调用的堆叠

共识调用、多态档与 MC 都要"逐位点看数据"，但**问的问题不同**：前两者要"这个位置上有什么碱基"
（碱基、质量、链向、read 内下标），MC 只要"这里被多少条 read 盖住"。堆叠会为每个观测建一个对象，
覆盖度只需要一个计数器：一条 read 的每个 `M` 区段给差分数组加一。两条路各做各的，也正好符合
"子模块之间不互相依赖"的约定——不为了省几行代码把 MC 绑死在共识调用的实现上。

### 已定的实现取舍

- **差分数组 + 前缀和**：与"逐位点累加 read 数"相比，代价与参考长度线性相关而不随覆盖度膨胀；
  一条 read 只做 2 次数组写入。细菌基因组 4.6 Mb 在 Python 里是几十 MB 的整数元组，比堆叠省得多
  （但真实数据仍应换原生实现，见「已知限制」）。
- **`None` 而不是异常**：没有覆盖的序列返回 `None`。分析流程里"这条质粒一条 read 都没有"是可能
  出现且需要继续往下走的正常情形，不该让它把整批分析打断。
- **阈值不超过均值**：见参数表的说明。这是护栏，不是上游的规定，**已明确标注**。
- **不查参考的重复区结构**：上游那句"穿过重复区"在覆盖剖面上是自动发生的（重复区的 read 被
  挡在 unique 之外），单独再查一遍重复区会把 MC 与 `trimming` 的重复区间定义绑在一起，
  而两者的判据（覆盖 vs 模糊性）并不相同。
- **区间用 0-based 闭区间**：与库内其余部分（堆叠、共识调用）一致；`.gd` 写出去时再换算成
  1-based，换算规则连同 `end`/`size` 的口径一起，留给分片 D 与真实产物逐字段核对。

### 验证记录（分片 A / B / C）

`python -m pytest modules/bio_analysis_function/submodules/missing_coverage -q` → **34 passed**
（剖面 13 + 分布 14 + 检测 7）；`submodules` 整目录 **660 项**全过。

**写 A 的时候被测试抓到两处自己的错**：一是把 `ReferenceSet.ids` 当成方法调用（它是属性），
二是测试里 `SEQ` 长度与 CIGAR 消费的 query 长度不一致——`SamRecord` 的校验直接报了错，
这正是那条约定该起的作用。**写 C 的时候被测试抓到一个测试自己的错**：我以为"挖掉 6 bp 的窗口"
在覆盖剖面上就是 6 个零覆盖位置，实际是 **7 个**——6 bp 的 read 里，窗口左边的最后一条盖到
窗口前一位、右边第一条从窗口后一位才开始，缺口总比窗口宽一位。代码是对的，改的是断言，
并把原因写进了用例注释（这类"数据自己长什么样"的错比代码错更容易蒙混过去）。

### 已知限制与待办

- **分片 D 未做**：`MC` 证据行的构造，以及与真实产物**逐字段核对**。真实行
  `MC 62 . NC_000913 1299499 1300697 1198 0` 里，`end` 与 `size` 的口径还没定死：
  人读报告（`index.html`）里 `start / end / size` 是"首缺失位 / 末缺失位 / 长度"（长度 =
  末 − 首 + 1），而这一行数下来 `1300697 − 1299499 = 1198 = size`，两者对不上——要么 `.gd` 里
  的 `end` 是开区间端点，要么第 3 个数字不是长度。**在核对清楚之前不写这个构造器**，
  免得把错误的字段口径固化进代码。
- **`MC + JC → DEL` 没做**：判定在编排层。`JC`（junction 检测与打分）这条证据线还没开工，
  两条线的端点如何对齐（同向重复、`MC` 单独定端点）也属于那一步的事。
- **不做"离群点剔除"**：上游的分布拟合细节（是否先剔掉极端的覆盖峰）没有公开，
  我们用全部 unique 位置拟合。重复区/扩增区会抬高方差，从而抬高阈值、降低敏感度——
  这是已知偏差，等有真实数据再评估。
- **没有最小长度过滤**：1 bp 的零覆盖位置也会报出来（在真实数据里通常被下游的过滤挡掉）。
  要不要设最小长度、设多少，应该在看到真实数据的缺口长度分布之后决定，现在不预设。
- **只适合小规模**：每条序列一份长度相同的整数元组，4.6 Mb × 8 字节 ≈ 37 MB（外加 Python 对象
  开销）；阈值与分布还要遍历一遍。真实数据（几十倍覆盖、多个复制子）应换原生实现或流式统计。
- **不支持环状参考的跨越**：与 `read_mapping`、`consensus_calling` 同一条限制，比对必须完整
  落在参考范围内；跨复制原点的缺失是合法突变，但本版本直接报错而不是静默截断。
- **不做多重命中的取舍**：默认只看 MAPQ ≥ 1 的 read；上游还把"重叠了 repeat read matches 的
  位置"整体排除（unique-only **位置**，与"unique read"略有差别）。我们按 read 的 MAPQ 近似，
  差异体现在重复区边缘的若干位置，记为已知偏差。
