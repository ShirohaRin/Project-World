# mutation_annotation：突变注释

把"参考上某个位置变了"翻译成"这对基因与蛋白意味着什么"。这是参考比对与变异检测线
（breseq 方向）的第五项：变异调用给出的是坐标与碱基变化，而人要看的是
**哪个基因、第几个氨基酸、从什么变成了什么、基因受什么影响**。

上游 breseq 的报告就长这样（出自 breseq 官方 workshop 材料与运行输出）：

```text
RA  380,188  A→C   F239L (TTT→TTG)  araJ+   predicted transporter
RA  3,483,047  C→A   R455S (CGC→AGC)  malT→
RA  3,370,027  T→A   K117M (AAG→ATG)  rpsM+   30S ribosomal protein S13
RA  1,329,516  C→T   intergenic (-110/-179)  topA→
RA  3,045,069  Δ16 bp  coding (96-111/4554 nt)  yghJ
```

> **当前状态：A–D 四片全部完成。** 遗传密码与 CDS 翻译 → 变异效应 → 基因间与距离 →
> 端到端注释表，共 **62 项测试**；真实参考 phiX174 上做过**全位置扫描**（5386 个位置全部分类，
> 95.97% 落在编码区、1392 个位置属于多个重叠基因）。算法广场入口已登记契约
> （`bio-mutation-annotation`，分类「变异检测」，`beta`）——**只登记调用契约、执行链路未接通**，
> 界面会照实提示。

## 1. 现在能做什么

| 项 | 内容 |
| --- | --- |
| 输入 | 参考序列 + 特征表（`common/reference_io` 读出来的 `ReferenceSequence` / `Feature`），或直接给一段核酸序列；变异用 `Substitution`（参考名、1-based 位置、参考碱基、判定碱基）描述，基因间只需位置 |
| 输出①翻译 | `translate(sequence)` → 蛋白序列（标准遗传密码，含未知碱基的密码子译成 `X`） |
| 输出②CDS | `CdsTranslation`：CDS 核苷酸、蛋白、起始密码子、终止密码子、链方向、是否部分序列、被忽略的尾巴长度，以及"起始是不是 ATG / 是不是常见替代起始 / 有没有内部终止子"这几个判断 |
| 输出③效应 | `CodingEffect`（可能多条，重叠基因各一条）：基因名与产物、链方向、参考/判定碱基、**第几位核苷酸、第几个密码子、密码子总数**、两侧密码子与残基、效应取值；`description` 就是报告里那一列（`F239L (TTT→TTG)`） |
| 输出④基因间 | `IntergenicEffect`：两侧最近基因名与**距离**、按转录方向的**带符号距离**、是否真的夹在两基因之间；`description` 是 `intergenic (+22/-4)`（缺一侧写 `.`） |
| 输出⑤注释结果 | `VariantAnnotation`：一条变异的**分类**（`coding` / `intergenic` / `noncoding` / `unannotated`）+ 对应的详细结果 + `genes` / `effect` / `description` |
| 输出⑥注释表 | `iter_annotation_rows` 摊成**一个效应一行**的表行；`write_annotation_table` 写出 TSV（表头见 `ANNOTATION_COLUMNS`） |
| 输出⑦批量 | `translate_features(参考序列, *kinds)` 翻译全部 CDS；`GeneModel.build(参考集合)` 建一次模型、`model.annotate(替换)` 反复用；`annotate_variants` 成批注释（模型只建一次） |
| 另可用 | `CODON_TABLE`（64 个密码子 → 氨基酸）、`START_CODONS`、`coding_offset`（参考坐标 → CDS 内下标）、`DEFAULT_GENE_KINDS` |

## 2. Python API 用法

```python
from modules.bio_analysis_function.common.reference_io import read_genbank
from modules.bio_analysis_function.submodules.mutation_annotation import (
    cds_translation,
    translate,
    translate_cds,
    translate_features,
)

# 裸序列翻译（含未知碱基的密码子会译成 X，末尾不足一密码子的碱基忽略）
translate("ATGGCTTAA")            # 'MA*'

# 一个 CDS：起始密码子按 M、末尾终止密码子不进蛋白
cds = translate_cds("GTGGCTTAA", gene="g1", seq_id="NC_001422")
cds.protein                       # 'MA'
cds.start_codon, cds.stop_codon   # ('GTG', 'TAA')
cds.start_is_alternative          # True（GTG 不是 ATG）

# 直接吃参考里的特征表
reference = read_genbank("NC_001422.gbk")
target = reference.get("NC_001422")
for item in translate_features(target):        # 默认只翻 CDS
    print(item.gene, item.residue_count, item.protein[:20], item.has_stop_codon)

# 单个特征也要翻时：
first = target.features_of("CDS")[0]
print(cds_translation(first, target).protein[:30])
```

变异的效应注释：给一个替换，拿到它造成的所有效应（重叠基因会给多条）。

```python
from modules.bio_analysis_function.submodules.mutation_annotation import (
    GeneModel,
    Substitution,
    annotate_substitution,
)

# 只注释一两条时够用（内部现建模型）
effects = annotate_substitution(reference, Substitution("NC_001422", 380189, "A", "C"))
for effect in effects:
    print(effect.gene, effect.effect, effect.description, effect.codon_index)

# 成批注释请自己建一次模型再反复用（否则每条变异都会把全部 CDS 重翻一遍）
model = GeneModel.build(reference)
for substitution in substitutions:
    for effect in model.annotate(substitution):
        ...

# 共识碱基调用给的是 0-based 坐标，用这个工厂转，别自己手工 ±1：
substitution = Substitution.from_zero_based("NC_001422", 380188, "A", "C")
```

**输入会被校验**：位置越界、给出的参考碱基与参考序列不符，都会直接报错——这类错一旦漏过，
注释结果会静默地错到别的位置上去。

落在基因之间的位置用另一个入口（它只需要位置，与碱基无关）：

```python
from modules.bio_analysis_function.submodules.mutation_annotation import intergenic_effect

effect = intergenic_effect(target, 380189)      # 1-based
if effect is not None:
    print(effect.description)                   # intergenic (+22/-4)
    print(effect.left_gene, effect.left_distance, effect.left_signed_distance)
    print(effect.right_gene, effect.right_distance, effect.right_signed_distance)
    print(effect.is_between_genes)              # 两侧是否都有基因
# 落在基因内部（或这条参考没有任何基因）时返回 None——前者归 effects.py 那一侧管。
```

一次拿到"这条变异到底算什么"，并写成可核对的表：

```python
from modules.bio_analysis_function.submodules.mutation_annotation import (
    annotate_variant,
    annotate_variants,
    iter_annotation_rows,
    write_annotation_table,
)

annotation = annotate_variant(reference, Substitution("NC_001422", 380189, "A", "C"))
annotation.kind            # 'coding' / 'intergenic' / 'noncoding' / 'unannotated'
annotation.genes           # ('araJ',) 或两侧邻居
annotation.description     # 'F239L (TTT→TTG)' / 'intergenic (+22/-4)'

# 成批：模型只建一次；返回顺序与输入一致
annotations = annotate_variants(reference, substitutions)
for row in iter_annotation_rows(annotations):   # 一个效应一行
    print(row["gene"], row["effect"], row["description"])
write_annotation_table("out/annotations.tsv", annotations)   # 返回行数
```

## 3. 七条必须知道的约定

1. **标准遗传密码（NCBI 表 1）**，不做非标准密码表、不做密码子偏好。细菌重测序绝大多数情况
   就是表 1；线粒体或某些原生生物的表是另一件事，不该混进来。
2. **起始密码子统一译成 `M`**。细菌常用的起始密码子除 `ATG` 还有 `GTG` / `TTG`（少数情况
   `ATT` / `ATA` / `CTG`）：按标准表它们分别是 V / L / I / I / L，但作为**起始**时生物实际装
   上去的是甲硫氨酸，上游 breseq 报告也是这个口径——它把 `ATG→ATA` 写成 `M1M`，正说明 `ATA`
   也在它的起始集合里。`START_CODONS` 就是这条规则用的集合；起始密码子不在这个集合里时
   **不硬塞 M**，而是如实按表翻译，并把 `start_is_unknown` 标出来。
3. **终止密码子不进蛋白**。完整 CDS 末尾通常是 `TAA` / `TAG` / `TGA`，它记在
   `stop_codon` 里、不写进 `protein`——这样"第几个氨基酸"的编号才和报告里的 `F239L` 对得上。
4. **负链基因写进密码子前要取互补**。替换的两侧（`reference_base` / `call_base`）是**参考正链**
   口径的（与 SAM / VCF 一致）；而负链基因的密码子是**编码方向**的（那一段被反向互补过）。
   所以 `CodingEffect` 里 `reference_base` / `call_base` 记的是参考正链上的变化，
   而 `reference_codon` / `call_codon` 是编码方向的——两者都保留，报告里都能用到。
   这一层有测试专门钉住（忘记取互补时负链基因的氨基酸会算错）。
5. **重叠基因各给一条效应**。细菌基因组里同一个碱基属于两个基因是常态（phiX174 大量重叠），
   所以注释结果是**元组**：同一个碱基可能对 A 基因是内部错义、对 B 基因是起始子丢失。
   效应取值一共六种：

   | 取值 | 含义 |
   | --- | --- |
   | `synonymous` | 同义：氨基酸没变（含"终止密码子换成另一个终止密码子"） |
   | `nonsynonymous` | 错义：氨基酸变了，且都不是终止子 |
   | `nonsense` | 无义：新密码子成了终止密码子 |
   | `stop_lost` | 终止密码子被破坏，蛋白会读通下去 |
   | `start_codon_change` | 起始密码子换成另一个起始密码子（残基仍是 `M`，如 `M1M (ATG→ATA)`） |
   | `start_lost` | 起始密码子不再是起始密码子（如 `M1T (ATG→ACG)`） |

另外两条边界：**含未知碱基的密码子译成 `X`**（参考里 `N` 很常见，硬报错会让整条链读不下去）；
**结构性问题报错、注释口径差异如实记录**——长度不是 3 的倍数无法按读码框翻译，直接报错；
而"完整 CDS 却没有终止密码子"是命名方口径不同（有的文件不把终止密码子算进 CDS），
不报错、记在字段里，由调用方决定要不要在意。

6. **基因间的距离符号按"转录方向"给**。`intergenic (+22/-4)` 里 `+` 表示变异在该基因
   **转录下游（3' 侧）**、`-` 表示上游（5' 侧）——所以同一个位置对正链基因与负链基因的符号
   恰好相反。两侧基因的判定也各有边界：**参与比较的特征类型默认只有"基因"那一类**
   （`DEFAULT_GENE_KINDS`：CDS / tRNA / rRNA / tmRNA / ncRNA / misc_RNA，`misc_feature`、
   `repeat_region` 不算，否则"基因间"会被无关注释切碎）；**落在这些特征内部（含端点）的位置
   不是 intergenic**（返回 `None`，交给 `effects.py` 那一侧）；`join` 的多段特征**按段参与比较**，
   所以跨复制原点的基因可能同时是左右两侧的邻居。
7. **每条变异恰好归一类，判定顺序是"编码区 → 基因间 → 非编码特征 → 无注释"**：

   | 分类 | 条件 | 附带结果 |
   | --- | --- | --- |
   | `coding` | 落在某个 CDS 的读码框里 | `CodingEffect` 一条或数条（重叠基因） |
   | `intergenic` | 两侧至少一个有基因，且自己不在任何基因内 | `IntergenicEffect` |
   | `noncoding` | 落在非 CDS 的基因特征里（tRNA / rRNA …） | `NoncodingEffect`（`noncoding (4/8 nt)`） |
   | `unannotated` | 以上都不是（这条参考没有可参照的基因） | 无 |

   编码区优先——一个位置既在 CDS 里、又"看起来夹在别的基因之间"时，它是 `coding`。
   摊成表格时**一个效应一行**（重叠基因占多行，基因间的行里 `gene` 列写两侧邻居如 `beta/trnaA`）。
   `annotate_variant` 是单条入口，`annotate_variants` 成批（模型只建一次、顺序保持）。

## 4. 参数

| 参数 | 默认 | 含义与取舍 |
| --- | --- | --- |
| `stop_symbol`（`translate`） | `*` | 终止密码子在蛋白里的符号；写成 `""` 就完全去掉 |
| `partial`（`translate_cds`） | `False` | 该 CDS 的端点带 `<` / `>`（序列不完整）。此时不要求长度是 3 的倍数，末尾不足一个密码子的碱基被忽略并记进 `trailing_bases` |
| `kinds`（`translate_features`） | `("CDS",)` | 要翻译哪些特征类型；按 GenBank 原样比较 |

## 5. 怎么跑测试

```bash
python -m pytest modules/bio_analysis_function/submodules/mutation_annotation -q
```

**62 项**全过（翻译 **19** + 替换效应 **21** + 基因间 **10** + 端到端注释表 **12**）。用例分六组：

- **密码表**：用**氨基酸简并度**（教科书上每个氨基酸由几个密码子编码）独立核对 64 个密码子
  ——不是抄实现自己的表；再逐条核对代表密码子（`ATG→M`、`TGG→W`、三个终止子、`TTG→L`、
  `GTG→V` …）。
- **翻译与 CDS**：忽略末尾不足一密码子的尾巴、含 `N` 的密码子译成 `X`、`stop_symbol` 可配、
  接受小写；完整 CDS（起始 `ATG` / 替代起始 `GTG`、`TTG`、`ATT`）、无终止密码子如实记录、
  内部终止子出现在蛋白里、长度不是 3 的倍数报错、部分 CDS 记录尾巴长度、空序列与过短序列报错；
  从特征表取序列（正链）、负链自动反向互补、**混合链位置报错**、`kinds` 筛选生效。
- **替换效应**：坐标映射（正链单段 / 负链单段 / 多段 `join` / `complement(join)` 的段序与翻转）、
  错义、同义、无义、终止换终止、终止丢失、起始密码子改变（`M1M (ATG→ATA)`）、起始丢失、
  **负链取互补**（`ATG→ACG`、`GCT→GAT` 两条）、**跨原点 `join` 的坐标映射**、**重叠基因各给一条**
  （同一个碱基对 first 是 `M3T`、对 second 是 `M1T`）、编码区外返回空、输入校验
  （越界 / 参考碱基不符 / 序列名不存在 / 同类碱基替换）。
- **基因间**：两正链基因之间的距离与符号（`intergenic (+6/-5)`）、**右侧是负链基因时符号翻转**
  （`(+7/+5)`）、只有一侧邻居（`(-7/.)`、`(./-5)`）、落在基因内部（含端点）返回 `None`、
  没有基因的参考返回 `None`、`kinds` 过滤决定 `misc_feature` 算不算基因、`join` 的多段特征
  按段参与（同一基因可以同时出现在两侧）、位置越界报错、默认特征类型集合。
- **端到端注释表**：四类分类各来一遍（编码区单基因 / 重叠基因各给一条 / tRNA 内 `noncoding` /
  基因间两侧邻居 / 落在 `misc_feature` 里仍算基因间 / 把 tRNA 排除出"基因"后同一位置改判为基因间 /
  没有注释的参考给 `unannotated` / 序列名不存在报错）；成批注释保持输入顺序；表格**一个效应一行**
  展开正确；TSV 写出去再读回来（表头、行数、某一行的描述都对得上，父目录自动创建）。
- **真实参考对拍**：phiX174 的 **11 个 CDS** 全部翻译，要求长度是 3 的倍数、不标 partial、
  有终止密码子、无内部终止子、蛋白以 `M` 开头，并且**与我们翻译出来的结果与 NCBI 自己写在
  `/translation` 限定符里的蛋白逐条相等**；再对每个 CDS 的第 2 个密码子第 1 位造一次替换，
  要求注释落回同一个基因、`nucleotide_index = 4`、`codon_index = 2`、密码子与数据一致
  （这条同时覆盖跨复制原点的多段坐标映射）；最后把**全部 5386 个位置**都注释一遍，检查
  分类自洽（`coding` 必有效应、`intergenic` 必有邻居且描述以 `intergenic (` 开头、描述非空），
  并核对两项由参考自身特征表决定的统计量：**编码区占比 5169/5386 = 95.97%**、
  **1392 个位置属于多个重叠基因**（平均每个编码位置 1.357 个基因）。

---

## 附：设计记录

### 上游是什么（带出处）

annotate 这一步在上游是 `breseq` 的"突变预测 :: 突变注释"两个阶段：先由证据线（`RA` / `MC` /
`JC`）得出"哪个位置发生了什么变化"，再把变化**投影到参考的特征表上**，产出报告里那一列
（`F239L (TTT→TTG)`、`intergenic (-110/-179)`、`coding (96-111/4554 nt)`、`Δ16 bp` …）。
出处：breseq 0.35.7 文档 Methods（`https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html`）
与官方 workshop 材料里给出的报告样例。

分片 A 只做其中"把 CDS 翻成蛋白"这一步——后面所有效应判定（同义/错义/无义、第几个氨基酸）
都要拿蛋白与密码子序列来比。

### 为什么先做翻译

因为它是**唯一一个能用真实数据硬验证的环节**：GenBank 的 CDS 自带 `/translation`，那是别人
独立算出来的蛋白，可以直接逐条对拍。把它钉死之后，分片 B 的"氨基酸改变"才有可靠的基线
——否则后面每一个注释结果都得怀疑是不是翻译就错了。

### 已定的实现取舍

- **密码表在代码里当常量写死**（按 NCBI 表 1 的经典排列生成，而不是手打 64 行），
  测试用**简并度**独立核对。这样"表抄错一行"这类错误会被结构性发现。
- **起始密码子统一按 M，但不掩盖"起始不常见"这件事**：非 `ATG` 的替代起始标
  `start_is_alternative`，不在集合里的标 `start_is_unknown`。上游报告把起始改变写成 `M1M`
  这种形式，说明它就是按 M 记的；我们把原始密码子单独留在 `start_codon` 里，信息不丢。
- **终止密码子单独记、不进蛋白**：报告里的 `F239L` 这类编号是"第几个密码子"，
  终止密码子占一个密码子但不占一个氨基酸，两者的对应关系必须写清楚。
- **长度不是 3 的倍数报错，缺终止密码子只记录**：前者是结构上无法翻译（数据错了），
  后者是命名口径差异（数据没错）。这条区分与项目一贯的"早报错、但不替数据做决定"一致。
- **只依赖公共层**：本模块只 import `common/reference_io` 与 `common/sequences`，
  变异调用结果通过**数据**进来（位置 + 碱基），不通过 `import` 进来（开发规则 3.1 第 4 条）。

**分片 B（替换效应）另加的取舍：**

- **注释结果是元组，不是单值**：重叠基因是常态，一个碱基可能同时属于两个 CDS，两边的影响还
  可能完全不同（对 A 是内部错义、对 B 是起始子丢失）。返回元组比"挑一个"诚实。
- **`GeneModel` 建一次、用很多次**：一个基因组上会有成千上万条调用，每条都重翻全部 CDS 太浪费。
  单条调用也走同一条路径（`annotate_substitution` 内部建一次临时模型），不维护两套逻辑。
- **替换的两侧保留参考正链口径**：`reference_base` / `call_base` 记的是参考上的变化
  （与 SAM / VCF、与共识调用的输出一致），而密码子对是编码方向的。两者都留着，
  报告层要哪个用哪个——把它们统一成一种口径反而会让某一层看不懂。
- **先校验再注释**：位置越界、参考碱基与参考序列不符直接报错。这类错误一旦放过，
  注释会静默地落到别的位置上，比报错难查得多。
- **落在部分 CDS 尾巴上的变异不注释**（那里读码框不确定）；**混合链 CDS 不收进模型**
  （没法当读码框），但会计数在 `GeneModel.skipped_features` 里，不静默。

**分片 C（基因间）另加的取舍：**

- **符号按转录方向，而不是按参考坐标**：`+` = 该基因的转录下游（3' 侧）、`-` = 上游。
  这样同一个位置对两个方向相反的基因会得到相反的符号，读报告的人一眼能看出"这个变异在哪个
  基因的上游/下游"。代价是符号规则本身要解释清楚，所以单列成第 3 节第 6 条并用测试钉住。
- **只把"基因"那一类特征当边界**（`DEFAULT_GENE_KINDS`）：`misc_feature` / `repeat_region`
  这类注释不是基因，拿它们切"基因间"会让距离失去生物学意义；`kinds` 参数留给需要时的覆盖。
- **落在基因内返回 `None`，不返回"距离 0"**：`None` 明确表示"这里不是基因间区域"，
  调用方据此去走编码区那条路；用 `0` 或负距离表达会把这个区分弄模糊。
- **`join` 按段参与比较**：跨复制原点的基因在参考坐标上是两段，变异确实可能夹在它们中间。
  此时同一个基因名会同时出现在左右两侧——这不是 bug，而是"这个基因跨过了这里"的如实表达。
- **缺一侧邻居时写 `.`**：基因组最左/最右之外的变异只有一侧有邻居，`intergenic (-7/.)`
  比编一个 0 或省略更能说明"这一侧没有基因"。

**分片 D（端到端注释表）另加的取舍：**

- **补上"非编码特征内"这一类**：不补的话，落在 tRNA 里的变异会被归成 `unannotated`——
  那是错的，它明明有注释。多十行代码就让"没有注释"这个词保持诚实。
- **分类是互斥的一元组，判定顺序写死**：编码区 → 基因间 → 非编码特征 → 无注释。一个位置既在
  CDS 里、又夹在别的基因之间时，它是 `coding`（读码框的解释更具体）。
- **表格"一个效应一行"而不是"一条变异一行"**：重叠基因本来是不同基因上的不同后果，
  挤在一格里会让下游没法筛选（"列出所有错义突变"这种问题会因为一格塞了多条而漏）。
- **`position` 一律 1-based、不再多给一个 0-based 列**：本模块内部口径统一（与 `reference_io`
  一致），需要 0-based 的地方由共识调用那一侧转换（库里有 `Substitution.from_zero_based`）。
  表格里同时摆两种坐标迟早有人看错。
- **写出用 TSV、不做格式化报告**：报告排版是展示层的事（HTML 归工作流那层），本模块只保证
  "列的含义稳定、能机读"。

### 验证记录（分片 A–D）

`python -m pytest modules/bio_analysis_function/submodules/mutation_annotation -q` → **62 passed**
（翻译 19 + 替换效应 21 + 基因间 10 + 端到端注释表 12）。三处最硬的证据都在真实数据上：
**phiX174 的 11 个 CDS 翻译结果与 NCBI 的 `/translation` 逐条相等**（文件是仓库里已有的真实
公开数据 `tests/data/phiX174_NC_001422.1.gbk`）；对每个 CDS 造替换后能正确落回同一个基因、
且覆盖跨复制原点的多段坐标映射；以及**全部 5386 个位置的全扫描**（分类自洽 + 编码密度 95.97%
+ 1392 个重叠位置）。

**写代码时抓到的三件事**（都值得记）：

1. **起始密码子集合少了一个 `ATA`**。我最初按"E. coli 常见替代起始 = GTG/TTG/ATT/CTG"来定集合，
   结果 `ATG→ATA` 被判成 `start_lost`；而上游报告里这个情形写的是 `M1M`——说明它把 `ATA` 也当
   起始密码子。改成六元集合后与上游样例一致。**教训**：上游样例里的具体写法就是口径的证据，
   能和它对齐就先对齐。
2. **负链基因必须把判定碱基取互补再写进密码子**。替换两侧是参考正链口径，而密码子是编码方向；
   不取互补时负链基因的氨基酸会算错（我在实现时先想到了，测试也钉住了这条）。
   **教训**：一旦一个量有两种"朝向"，就要在文档里把两个朝向各自的字段写清楚。
3. **`iter_annotation_rows` 在"只有一侧邻居"时会炸**：`"/".join((左, 右))` 里有一个是 `None`
   时直接 `TypeError`。写分片 D 时被测试当场抓到（`intergenic (+15/.)` 那条用例）。
   **教训**：凡是"可能缺一半"的拼接，都要先想清楚缺的时候输出什么。

另外，**手写合成参考这个环节一共错了三次**，都是被不变式或断言当场拦下的：序列长度数错两次
（11 bp 的序列、CDS 却标到第 12 位，被 `ReferenceSequence` 的"特征不得越界"拦住）；碱基排布
算错一次（以为重叠区是 `TTT`，实际被前一个 CDS 的注释覆盖成了 `TAA`，于是 beta 的第一个密码子
从"起始子"变成"终止子"——我按意图写的断言先失败，才回头核对的）。**这一类错的共同点**：
我按"设计时的意图"记数据，而实际数据是"谁最后写的说了算"。教训是合成数据后要**从数据反推
一遍期望值**，别凭记忆写断言。

### 已知限制与待办

- **indel 还没做**：本片只处理碱基替换。缺失/插入对读码框的影响（移码、`coding (n/m nt)`、
  `Δ16 bp`、同聚物 `(T)7→8` 那种写法）是另一套语义，归后续分片。
- **基因间的符号语义是对上游的重建**：`+` = 转录下游、`-` = 转录上游（第 3 节第 6 条）。
  手上有多个上游样例与它吻合，但**没有**逐条核对过 breseq 的实现；对拍时以突变集合一致为准。
- **非编码特征内部只给"位置"，不给效应**：落在 tRNA / rRNA 里现在归 `noncoding (n/m nt)`，
  但不会说"这个 tRNA 的哪个结构域坏了"之类——那需要 RNA 二级结构知识，不在本线范围内。
- **`noncoding (n/m nt)` 这个写法没有与上游逐条核对**：我们的写法是对上游风格的模仿，
  与 `intergenic` 的符号一样属于重建口径。
- **效应取值是对上游口径的重建**：六种取值（`synonymous` / `nonsynonymous` / `nonsense` /
  `stop_lost` / `start_codon_change` / `start_lost`）来自我们对 breseq 输出的理解，
  **没有**逐条核对过上游内部的分类型号；真做阶段级对拍时以"突变集合一致"为准（算法清单 5.5.3）。
- **没收上游报告里的基因方向标记**：`araJ+` / `malT→` 里的箭头表示基因方向与操纵子关系，
  属于报告层的展示细节，本模块只给 `strand`，不猜箭头。
- **落在部分 CDS 尾巴上的变异不注释**（读码框不确定）；**混合链 CDS 不进模型**（计数上报）。
- **非标准遗传密码表不支持**：线粒体、某些原生生物（如 `TGA` 编码色氨酸）需要表 2–33 之一；
  细菌重测序用不到，故不做（记在待办，真要用时再加表号参数）。
- **不支持 RNA 参考的 U/T 混用**：输入统一按 DNA 的 `T`；参考序列里若出现 `U`
  （`reference_io` 的字母表不含 `U`）会在读参考那一步就报错，不会走到这里。
- **`/transl_except`（硒代半胱氨酸、吡咯赖氨酸）未处理**：这类 CDS 的翻译与标准表不同，
  真实注释里很少见；将来在对拍中发现再补，现在**不静默忽略**——我们的翻译会与 `/translation`
  不同，测试会暴露出来。
- **GFF3 注释来源未支持**（`reference_io` 的待办）：目前只吃 GenBank 的特征表。
- **算法广场入口已登记契约**（`bio-mutation-annotation`，分类「变异检测」，`status: beta`，
  输入字段按数据流角色标注：参考文件 `resource`、变异清单 `upstream`、输出表 `output`），
  但**执行链路未接通**——界面会照实提示"只登记调用契约，提交会被拒绝"。要接通还需两步：
  给本模块加一个 JSON 入口（照 `modules/workflow` 的 `wrapper.py` 那样收 JSON、吐 JSON），
  再把方法 id 加进 `LOCALLY_EXECUTABLE_METHOD_IDS`；这两步属于后续任务。
- **坐标口径要在接线上当心**：本模块一律 1-based，而「共识碱基调用」的表格是 0-based。
  契约里已经写明（两个方法的字段说明都提了），库里有 `Substitution.from_zero_based` 做转换；
  真接线时要专门验一次这条链路，别让它成为静默的一位偏移。
