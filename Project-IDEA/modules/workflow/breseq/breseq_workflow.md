# breseq 参考比对与变异检测工作流

把生物模块里"参考比对与变异检测线"已经做好的算法按上游 breseq 的顺序串起来：
**给一份参考（GenBank / FASTA）与一份（或一对）FASTQ，产出 `.gd`、注释表与摘要表**。

它回答的问题是：**这份重测序数据相对参考基因组有哪些突变，分别落在哪个基因上、意味着什么。**

> **当前状态：RA 线打通，客户端链路已接通（26 项测试）。** 参考 + FASTQ → 比对 → SAM →
> 错误率重校准 → 共识档 / 多态档调用 → 突变 → 注释 → `.gd` + 注释表 + 摘要表 + **两份报告**，
> 这条链端到端跑通；桌面端 JSON 入口（`wrapper.py`）、算法广场契约（`bio-breseq-workflow`）、
> **预设队列**与**客户端真实执行链路**（`electron/main.ts` 按方法 id 分派）都已接上。
> **未接**：MC（覆盖度与缺失区）、JC（junction）两条证据线；以及"从外部 SAM 起跑"的对拍入口。
> 见「已知边界」与「开发记录」。

---

## 1. 它住在哪、为什么不与 fastp 挤在一个文件里

它是 `modules/workflow/` 里的**第二条流程**（第一条是 fastp 预处理，见
[../workflow.md](../workflow.md)）。两者同属**能力模块**：自己不做任何判定，只固定调用顺序
并处理数据接力（边界判定见 [PROJECT_RULES.md](../../PROJECT_RULES.md) 7.1）。

代码放在 **`modules/workflow/breseq/`** 这个子目录里，而不是把 `config.py` / `runner.py`
铺在工作流目录的根上：一是两者的文件职责完全同名（配置、编排、报告、入口），铺平会立刻撞名；
二是 fastp 那四个文件的路径**写在客户端里**（`electron/main.ts` 的 `computeWrapperPath`），
挪它们就得动客户端源码并重出包——没必要为了一次目录调整去碰已经跑通的链路。

### 1.1 跨模块依赖（显式声明，规则 7.2）

| 依赖 | 用途 |
| --- | --- |
| `bio_analysis_function.common.reference_io` | 读参考（GenBank / FASTA）、特征表（注释要用） |
| `bio_analysis_function.common.fastq` | 读 FASTQ |
| `bio_analysis_function.common.alignment_io` | 写 SAM（头部与记录） |
| `bio_analysis_function.common.genome_diff` | 装配并写 `.gd` |
| `bio_analysis_function.submodules.read_mapping` | 建索引、逐条比对、转 SAM 记录 |
| `bio_analysis_function.submodules.consensus_calling` | read 端裁剪、错误率重校准、共识档 / 多态档调用 |
| `bio_analysis_function.submodules.mutation_annotation` | 变异注释（密码子、效应、基因间） |

> **与 fastp 工作流那一条的差别**：fastp 只依赖生物模块的**公共层与原生库**；
> 这条线的算法住在 `submodules/` 里，所以它必须依赖子模块。方向仍然是单向的
> （生物模块不依赖本模块），符合规则 7.1 的意图；把它写进这张表就是规则 7.2 要求的显式声明。

---

## 2. 与 fastp 工作流最大的结构差异：这条线不是单遍的

fastp 那条是"一次读入、逐条走完整条链、分批写出"；**breseq 这条做不到**：

1. **规模**：比对约 1 ms/条、堆叠与覆盖分析要吃全位置的状态。细菌基因组 100× 覆盖在这条
   Python 链上是小时级时间与 GB 级内存——把整条链塞进一次读入只会同时爆掉内存与耐心。
2. **契约**：算法清单 5.5.3 第 1 条要求"接受外部 SAM/BAM"。以 SAM 为交接之后，用户拿
   bowtie2 的结果也能从后半段接进来——这正是与上游做阶段级对拍的唯一手段。

所以本工作流**分阶段、以 SAM 为交接**：比对一次落盘成 `mapped.sam`，后面的步骤都从 SAM 读。
代价是每个阶段要重新打开一次 SAM（多几遍 I/O），换来的是每一步都能单独重跑、单独对拍。

---

## 3. 逐步顺序

预设队列的步骤顺序必须与本节一致（规则 7.4）。

| # | 步骤 | 在哪做 | 有独立算法块？ |
| --- | --- | --- | --- |
| 1 | 读参考（GenBank / FASTA） | `common.reference_io` | 否——工具，写进预设的 `unmapped` |
| 2 | 扫一遍 FASTQ（读长、条数、碱基数） | `common.fastq` | 否——阶段 A 的统计，写进 `unmapped` |
| 3 | 建参考索引 | `read_mapping:ReferenceIndex` | 否——比对器内部一步，写进 `unmapped` |
| 4 | 逐条比对 → 写 SAM | `read_mapping:Mapper` + `alignment_io` | 否——比对本身没有独立入口，写进 `unmapped` |
| 5 | read 端裁剪表（参考 1–18 bp 完全重复） | `consensus_calling:build_trimming` | 否——证据线内部一步，写进 `unmapped` |
| 6 | 碱基错误率重校准 | `consensus_calling:build_error_rates` | 否——同上 |
| 7 | 共识档 / 多态档调用 | `consensus_calling:call_variants` | **是**（`bio-consensus-calling`） |
| 8 | 证据 → 突变（RA 线规则） | `breseq/variants.py` | 否——编排层的固定规则，写进 `unmapped` |
| 9 | 突变注释 | `mutation_annotation` | **是**（`bio-mutation-annotation`） |
| 10 | 写 `.gd` / 注释表 / 摘要表 | `common.genome_diff` + 本模块 | 否——工具，写进 `unmapped` |

顺序上两处容易记错、也是**照抄上游**的地方：

- **裁剪与错误率都在调用之前**：错误率表是拿"被裁过之后的证据"数出来的，裁剪一旦放到后面，
  表就白建了；
- **注释在 `.gd` 之前**：注释结果是 `.gd` 突发行里的属性（`annotation=`），而注释表是另一份
  产物——两者同源，不能各算一遍。

---

## 4. 配置与默认值

`BreseqConfig` 的字段分三组，默认值尽量走上游 breseq 的行为：

| 分组 | 字段 | 说明 / 上游对应 |
| --- | --- | --- |
| 输入 | `reference` | 可给多个文件（染色体 + 质粒），按后缀识别 GenBank / FASTA |
| | `read1` / `read2` | 单端只给 `read1`；本版不使用配对信息（上游同样把双端当单端用） |
| 输出 | `output_dir` | 产物目录；`.gd` / SAM / 注释表 / 摘要表的名字固定 |
| 调用 | `consensus` | `ConsensusSettings`，默认 E-value 10 / 频率 0.8（上游默认值） |
| | `polymorphism` | `PolymorphismSettings`；给 `None` 表示**只跑共识档**（上游默认模式） |
| | `default_quality` | `QUAL` 为 `*` 时用的质量，默认 0（最保守） |
| | `trim_read_ends` | 是否用 read 端裁剪，默认开（上游默认裁） |
| 比对 | `mapping` | `MappingParams`（种子长度 16、步长 4、错配 ≤4、软剪裁 ≤10……） |
| 运行 | `max_reads` | 每个文件最多读多少条（调试与小样本复现）；`None` = 不限 |
| | `program` | 写进 `.gd` 头的 `#=PROGRAM` |

`__post_init__` 挡住自相矛盾的组合（参考为空、双端给了同一个文件、`max_reads < 1`、
默认质量为负）。

> **`output_dir` 是唯一的位置旋钮**：这条流程的产物是**一组相互引用**的文件（`.gd` 引 SAM、
> 报告引 `.gd`、注释表与 `.gd` 同行数），逐个给路径只会让调用方有机会把本该配套的文件拆开。

---

## 5. 输出

| 产物 | 文件 | 给谁看 |
| --- | --- | --- |
| 比对结果 | `mapped.sam` | IGV / samtools；也是"外部比对结果"的对拍接口 |
| 突变 | `output.gd` | 机读产物（与上游 breseq 同一格式；与上游对拍就是比这个） |
| 注释 | `annotations.tsv` | 一个效应一行（重叠基因占多行），表头见 `ANNOTATION_COLUMNS` |
| 摘要 | `variant_summary.tsv` | 一条突变一行，给人扫一眼（含 `annotation` 一列） |
| 报告 | `html_path`（调用方给） | **用户**：概览卡片、突变表、频率分布、读段与比对、产物清单 |
| 日志 | `log_dir/breseq_workflow.json` | **我们**：阶段 A 统计、比对构成、完整参数、每条突变的证据与注释 |

分工与 fastp 那份报告一致（上级目录 `workflow.md` 5.1）：HTML 只呈现结论、**不执行脚本、
不引外部资源**、能离线打开；JSON 面向排障与调参，宁可多留过程量。**两份都不带时间戳**，
同样输入每次渲染逐字节相同。不给 `html_path` / `log_dir` 就只跑不写报告。

`.gd` 的头部写 `#=PROGRAM` / 每条参考一行 `#=REFSEQ` / 每个读段文件一行 `#=READSEQ`；
**不写时间戳**（同样输入两次跑出同一个文件，报告本身也是可复现的产物）。

`.gd` 突变行的属性只有两处是我们自己定的、必须讲清楚：

- `frequency`（上游确证过的键）；
- `annotation`（**我们的键**）：把注释压成一句话（基因 + 效应 + 变化），因为上游 0.33.1 的
  突发行里除了 `frequency` 没有确证过别的键，我们不替它发明 `gene_name` 那一套。

多态档的证据行把打分写在 **`mixed_model_score`**（我们的键）下，不去占用上游的
`polymorphism_score`——那个统计量我们没有复刻。

---

## 6. 与上游 breseq 的差异

1. **比对器是我们自己的**（`read_mapping`，MAPQ 只有 60/20/0 三档），不是 bowtie2；
   因此"哪些 read 被比到哪里"与上游不同，突变集合的差异里必然有一部分来自这里。
2. **只做 RA 线**：碱基替换（共识档 + 多态档）。短 indel、`SUB`、MC / JC 那两条线与
   `MC+JC → DEL`、`JC+JC → MOB`、`JC → AMP` 的规则都还没有。
3. **没有报告层**：HTML 与 JSON 报告、算法广场入口、桌面端 JSON 入口都还没做。
4. **不做配对信息、不做 BAM**：上游也不用配对信息做证据（文档明说），但我们同样不读 BAM。
5. **不给 `#=CREATED`/`#=COMMAND`**：可复现优先（要写就由调用方显式给）。

---

## 7. 已知边界

- **规模**：这条链是 Python 单线程实现的。真实细菌基因组（几百 Mb 碱基的输入、几十倍覆盖）
  目前只能靠 `max_reads` 截断着跑小样本；正式跑大数据要等原生移植（`read_mapping.md`、
  `consensus_calling.md`、`missing_coverage.md` 里各自记了同一条限制）。
- **比对器的种子口径有已知的敏感度损失**：`step=4` 的种子遇到"错配落在 read 中段"时会失去
  全部可用种子，那条 read 直接比不上（测试里用 5 条 read 钉住了这个行为）。
- **注释表的 `unannotated`** 是纯 FASTA 参考下的常态（没有特征表就没有基因信息）——
  上游文档推荐用 Prokka 补注释，本项目这条线还没做。
- **`.gd` 里除 `frequency` 外的属性键名是我们的**（见第 5 节）。
- **取消/中断**：目前没有清理机制，跑到一半被中断会在 `output_dir` 里留下半成品。

---

## 8. Python API 用法

```python
from pathlib import Path
from modules.workflow.breseq import BreseqConfig, run_breseq_workflow

outcome = run_breseq_workflow(
    BreseqConfig(
        reference=(Path("NC_000913.gb"),),
        read1=Path("sample_R1.fq.gz"),
        read2=Path("sample_R2.fq.gz"),
        output_dir=Path("breseq_out"),
    )
)

print(outcome.scan.reads, outcome.mapped_reads, outcome.mapped_fraction)
for variant in outcome.variants:
    print(variant.seq_id, variant.position, variant.label,
          variant.frequency, variant.prediction)
print(outcome.config.diff_path, outcome.config.summary_path)
```

`run_breseq_workflow` 返回的 `BreseqOutcome` 带阶段 A 结论（`scan`）、比对统计与突变清单；
产物路径从 `config` 的派生属性上取。**只跑共识档**就给 `polymorphism=None`；
**要报告**就再给 `html_path=`（用户看的那份）与 `log_dir=`（JSON 日志）。

### 8.1 在算法广场中使用

| 项 | 值 |
| --- | --- |
| 方法 id | `bio-breseq-workflow` |
| 名称 | breseq 参考比对与变异检测 |
| 分类 | 变异检测 |
| 执行方式 | `local` |
| 状态 | `beta` |
| 契约位置 | `IDEA Assistant Code/src/compute.ts` 的 `BIO_BRESEQ_WORKFLOW_METHOD` |

它在变异检测线里是**流程型入口**：给参考与 FASTQ，拿到 `.gd`、注释表、摘要表与报告。
**已接通真实执行链路**（与 fastp 那条并列，是本模块里的第二条）：在广场上填好路径与选项、
提交任务后，由 Electron 主进程执行 `modules/workflow/breseq/wrapper.py`，
完成后列出产物、HTML 报告与日志路径，并可按任务 id 取消。

客户端侧为此改了三处（都在 `IDEA Assistant Code/`）：

- `electron/main.ts`：入口按**方法 id 分派**——`prepareBreseqInput`（参考可多文件、产物是一个
  目录）、wrapper 路径指向 `workflow/breseq/wrapper.py`、**跳过原生库检查**
  （这条链纯 Python，不需要 `bio_native`）；
- `src/compute.ts`：`bio-breseq-workflow` 加进 `LOCALLY_EXECUTABLE_METHOD_IDS`
  （与 `bio-workflow` 一起由 `WORKFLOW_METHOD_IDS` 定义）；
- `src/WorkflowQueue.tsx`：队列组装页把**两条流程型入口都排除**在"步骤块"之外——
  工作流本身不是一步算法。

### 8.2 桌面端 JSON 入口

```bash
python -u modules/workflow/breseq/wrapper.py < request.json
```

输入字段与广场上的 `BIO_BRESEQ_WORKFLOW_METHOD` 一一对应：`referencePath`（多个文件用
`；`/`;`/`,` 分隔）、`read1Path`、`read2Path`、`outputDir`、`polymorphismMode`（`on`/`off`）、
`trimReadEnds`、`maxReads`、`eValueCutoff`、`frequencyCutoff`，外加桌面端自己给的
`htmlPath` 与 `logDir`。

成功时 stdout 是一行 JSON `{"ok": true, "outcome": …, "outputs": [...], "html": …,
"log": …}`，退出码 0；失败时是 `{"ok": false, "error": {"type": …, "message": …}}`，
退出码 1——**不让调用方去解析 stderr 文本**。表单送来的数字是字符串，入口按数值字符串解析、
空串取默认值。

---

## 9. 验证

```bash
python -m pytest modules/workflow/breseq -q
```

**26 项**，按文件分四组：

- **`tests/test_variants.py`（8 项）**：0-based → 1-based 的换算、共识档与多态档各自
  `Variant` 的取值、排序与去重（同位置不同碱基是两条）、`.gd` 突发行与 `RA` 证据行的写法
  （共识档写 `consensus_score`、多态档写我们的 `mixed_model_score` 且不占用上游键）、
  装配后的编号与证据列、缺失突变必须给长度。
- **`tests/test_runner.py`（7 项）**：端到端——固定种子的 300 bp 合成参考 + 整齐铺开的
  30 bp read（其中一个位置全改成另一个碱基）→ 断言阶段 A 的统计、比对条数、唯一的 SNP、
  `.gd`（头、突发行、证据行、注释属性）、注释表与摘要表的形状；另有 `max_reads` 截断、
  FASTA 参考（没有特征表时注释为空）、认不出的参考后缀报错、读段文件缺失报错、配置校验。
- **`tests/test_report.py`（7 项）**：HTML **自包含**（无 `<script>`、无外部 URL）、
  **逐字节可复现**、突变与注释确实出现在表里、没有突变时如实说明；JSON 能直接 `dumps`
  且关键数字对得上；给了路径就写两份报告、不给就一份也不写。
- **`tests/test_wrapper.py`（4 项）**：走**真正的子进程**跑 `wrapper.py`（stdin 进、
  stdout 出一行 JSON）——成功时的输出与产物文件、失败时翻译成 JSON 且退出码 1、
  多个参考文件的分隔符、数字字段填错要报出来。

数据全部**现造**（不落盘外部数据）：参考是固定种子的伪随机序列，期望值可以直接从序列算出来。

---

## 10. 开发记录

### 开发状态

- **已完成**：阶段 A（读参考 + 扫 FASTQ）、比对与写 SAM、read 端裁剪、错误率重校准、
  共识档与多态档调用、证据 → 突变（RA 线）、突变注释、写 `.gd` / 注释表 / 摘要表、
  **报告层（HTML + JSON）**、**桌面端 JSON 入口 `wrapper.py`**、**算法广场契约
  （`bio-breseq-workflow`）**、**预设队列条目（`breseq-variant-calling`，已同步）**、
  **客户端真实执行链路（`main.ts` 按方法 id 分派 + `LOCALLY_EXECUTABLE_METHOD_IDS`
  + 队列过滤）**、单元测试
- **未完成**：MC / JC 两条证据线接入；"从外部 SAM 起跑"的对拍入口
- 文件构成：`config.py`（配置与结果类型）、`variants.py`（证据 → 突变的规则）、
  `runner.py`（阶段 A 与编排入口）、`report.py`（JSON 日志与 HTML 报告）、
  `wrapper.py`（桌面端 JSON 入口）、`breseq_workflow.md`、`tests/`
- 目录位置：`modules/workflow/breseq/`（与 fastp 工作流同属能力模块，2026-09-29 定）

### 为什么先做 RA 线（2026-09-29 白羽奈绪拍板）

算法清单 5.5.3 第 2 条定的 v1 范围就是 RA 线（比对 + 错误率重校准 + 共识碱基调用 +
突变注释 + 报告）。MC / JC 的算法已经有了（`missing_coverage` / `junction_calling`），
但先把一条线端到端打通，才能拿真实数据反过来验前面那一堆算法——**先有出口，再扩出口**。

### 待办

- 接 MC / JC：证据线与 `MC+JC → DEL`、`JC → INS/DEL` 的规则往 `variants.py` 加
- 与上游做阶段级对拍：拿外部 bowtie2 的 SAM 进后半段，比突变集合（验收口径见 5.5.3 第 3 条）
  ——`run_breseq_workflow` 目前总是自己比对；要做对拍就得允许"从外部 SAM 起跑"的入口
- **打包验收**：`electron-builder.*.json` 是整目录拷 `modules/workflow`（排除 tests 与
  `__pycache__`），所以 `breseq/` 会跟着进包；但**打包后的真实安装包还没人工跑过一遍**
  （与 fastp 那条同一笔账）。改动涉及客户端源码，需要重新出包才生效。
