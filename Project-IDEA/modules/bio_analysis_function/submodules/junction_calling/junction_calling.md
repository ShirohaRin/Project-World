# junction_calling：junction 检测与打分（JC）

从嵌合比对里找出"参考上原本不相连的两段被接上了"——这是**结构变异**的证据线。上游 breseq 的
`JC` 证据（new junction evidence）靠它报出移动元件插入、扩增、精确端点的大片段缺失，
以及插入/缺失/替换的 junction 形态。

> **当前状态：分片 A–C 完成（47 项测试）。** 分段 → 五条判据选候选对 → 拼连接序列 → 位置哈希分
> 与最小重叠分排序截断 → **四条支撑判据 + 位置哈希分下限决定接受**，这条链路已经跑通；
> 合成"跨同一个缺失的一批 read"上，候选被正确合并成一条、位置哈希分等于不同起点的个数。
> **两处明确没做**：① 分片 C 里的**重比**（把 read 再比到候选连接上）——它只是增加敏感度，
> 需要比对器注入，留给编排层接通时一起做；② 上游接受阈值用的 `neg_log10_pos_hash_p_value`
> （"skew"）**没有复刻**（零假设未公开），改为直接给位置哈希分一个可调下限，字段写 `NT`。
> 分片 D（`JC` 证据行与端到端）也未做。算法广场入口**未登记**（等编排层连成完整流程再谈）。

## 1. 现在能做什么

| 项 | 内容 |
| --- | --- |
| 输入 | 比对结果（**SAM 文件路径，或一堆 `SamRecord`**）+ 参考集合 |
| 输出①分段 | `AlignmentSegment`：一条 read 的一段连续比对（query 区间 / 参考区间 / 链），**> 2 bp 的空位处切开** |
| 输出②候选对 | `ChimericPair`：满足上游五条判据的两段，带 overlap / unique / union / intervening 四个派生量 |
| 输出③连接序列 | `build_junction_sequence`：左段贴断点的参考碱基 + 中间那截 read 碱基 + 右段从断点起的参考碱基 |
| 输出④候选 | `JunctionCandidate`：连接身份（`JunctionKey`）、连接序列、支撑 read，以及**位置哈希分**与**最小重叠分** |
| 输出⑤判定 | `JunctionEvidence`：四条支撑判据（两条链 / 两侧各 14 bp / 每条链 9 bp / 较短一侧 3 bp）与位置哈希分下限的通过情况、没过的判据名、两条链各自的延伸长度；`skew_score` 恒为 `None`（对应上游的 `NT`） |
| 一次跑完 | `call_junctions`：参考 + SAM → 排好序、按上游两条上限截断的候选连接；再交给 `accept_junctions` 取通过的那些 |

## 2. Python API 用法

```python
from modules.bio_analysis_function.common.reference_io import read_genbank
from modules.bio_analysis_function.submodules.junction_calling import (
    call_junctions,
    iter_chimeric_pairs,
    segments_of,
)

reference = read_genbank("NC_001422.gbk")

# 一条 read 怎么被切成段（>2 bp 空位处断开）
record = next(iter_records)
for segment in segments_of(record):
    print(segment.query_start, segment.query_end,
          segment.reference_start, segment.reference_end, segment.is_reverse)

# 逐条 read 找嵌合对（返回 (记录, 对)，记录用来取 read 序列）
for record, pair in iter_chimeric_pairs(records):
    print(pair.union_length, pair.unique_first, pair.unique_second, pair.intervening)

# 一次跑完：候选连接，按位置哈希分降序、已按上游两条上限截断
for candidate in call_junctions(records, reference):
    print(candidate.key.label, candidate.read_count,
          candidate.pos_hash_score, candidate.min_overlap_score, len(candidate.sequence))

# 判定：过四条支撑判据 + 位置哈希分下限的才算"被接受为证据"
for evidence in evaluate_junctions(candidates):
    print(evidence.candidate.key.label, evidence.accepted,
          evidence.pos_hash_score, evidence.longest_extension, evidence.failures)

accepted = accept_junctions(candidates)          # 只要通过的
relaxed = accept_junctions(candidates, settings=AcceptanceSettings(min_pos_hash_score=2))
```

参数走 `CandidateSettings`（五条判据里的四个数字）、`AcceptanceSettings`（四条支撑判据里的
三个长度 + 位置哈希分下限 + 是否强制两条链），以及 `call_junctions` 的两个上限
（`max_candidates` / `max_cumulative_fraction`）。

## 3. 参数

| 参数 | 默认 | 含义与取舍 |
| --- | --- | --- |
| 拆段阈值 `MIN_SPLIT_GAP` | 2 | 空位超过它才拆段（上游："> 2 bp"）。**不做成参数**：这是上游写死的预处理口径 |
| `min_unique_each` | 5 | 判据 3：两段各自至少要有这么多 read 碱基不与对方重叠 |
| `min_unique_one` | 10 | 判据 4：其中一段至少要有这么多（比上一条更严） |
| `max_intervening` | 20 | 判据 5：两段之间最多夹这么多 read 碱基 |
| `coverage_margin` | 2 | 判据 2：两段合起来要比最好的一段单独解释**多出**这么多才算 |
| `max_candidates` | 5000 | 截断：候选数上限（上游的数字） |
| `max_cumulative_fraction` | 0.1 | 截断：累计连接长度相对**全参考总长**的上限（上游的数字） |
| `min_extension_each_side` | 14 | 判据 2：至少有一条 read 在连接**两侧**各延伸这么多（上游的数字） |
| `min_extension_per_strand` | 9 | 判据 3：**每条链上**至少有一条 read 在两侧各延伸这么多（上游的数字） |
| `min_extension_any` | 3 | 判据 4：至少有一条 read 在两侧各延伸这么多（上游的数字；实际被判据 2 蕴含，留着是为了与上游逐条对应） |
| `require_both_strands` | `True` | 判据 1：是否要求两条链都有 read。上游是要求的，留成开关只为调试——**单独关掉它没意义**，判据 3 本身仍要求每条链都有 |
| `min_pos_hash_score` | 3 | 位置哈希分下限。**这是我们的取值**，上游用的是 `neg_log10_pos_hash_p_value`（其零假设未公开，我们没有复刻） |
| 连接序列两端的参考碱基数 | 由数据定 | 取**整个数据集里最长 read 的长度**（上游口径），见 `call_junctions`；单独调 `build_junction_sequence` 时用 `flank=` |

## 4. 必须知道的约定

1. **位置是 0-based 闭区间**（与堆叠、共识调用、MC 一致）。`.gd` 是 1-based，写证据行时再换算。
2. **段的 query 区间含段内插入**（插入的 read 碱基确实属于这一段），但**不含**前导/尾随软剪裁，
   也不含贴在段外的插入：段是"从第一个比对碱基到最后一个比对碱基"。
3. **判据 1 只在"靠前的那一段"上检查**：按 read 方向排序后，要求 `first.query_start == 0`。
   一条 read 有多段时，只有从 read 第一个碱基开始的那一段才有资格当左侧。
4. **判据 2 拿的是"最好的一段"**，不是"这一对里的某一段"：`union_length > best_single + 2`，
   `best_single` 是这条 read 所有段里 query 覆盖最长的那个。这是"两段拼起来明显更划算"的量化。
5. **判据 5 数的是"夹在两段之间的 read 碱基"**：两段重叠或相邻记为 0；只在中间真的空出一截时
   才是那截的长度。它限制的是"新连接中间最多夹多少非参考碱基"。
6. **连接序列两端取"最长 read 的长度"**：上游是为**重比**准备上下文（分片 C 要用）；序列贴到
   参考两端不够长时**截断**，环状参考上跨复制原点的连接本版本不做。
7. **两段必须同链**，否则 `build_junction_sequence` 直接报错。理由：本项目把 `SEQ` 当作
   **与参考同向**存（见 `consensus_calling/pileup.py`），一正一反时"中间那截 read 碱基朝哪边读"
   就没有统一答案——反转类连接（倒位那种）本版本不猜。
8. **候选按"连接身份"合并**：身份 = 左右两段的参考位置 + 中间那截 read 碱基。同一条连接的多个
   read 合成一个候选；连接点相同但中间夹的碱基不同，那是两件事。
9. **位置哈希分是"不同 read 起点数"**，不是 read 条数：同一位置压过来的一堆 read 只算一个。
   起点按 read 自己的方向量（`AlignmentSegment.start_anchor`：正链取 `reference_start`、
   负链取 `reference_end`）。
10. **最小重叠分是"每条 read 两侧独有碱基数较小值之和"**，只在位置哈希分并列时用来排序。
11. **截断是"加到会超就停"**，不是"跳过这一条再试下一条"：候选一旦触到上限，后面的全部不要。
12. **"延伸进某一侧多少"就是那条 read 在该侧的独有碱基数**（`SupportingRead.unique_*`）——
    这正是"read 从连接点往这一侧铺了多远"。判据 2 看**所有**支撑 read 里这个量的最大值，
    判据 3 看**正链、负链各自**的最大值（两者取较小值再比），判据 4 同样看所有 read 的最大值。
13. **判据 1 与判据 3 是绑在一起的**：只有一条链时，判据 1 不过，判据 3（"每条链上都要有"）
    也必然不过。所以 `require_both_strands=False` 单独用没有意义，要一起放宽
    `min_extension_per_strand` 才行——这条被测试钉住。
14. **`skew_score` 恒为 `None`**（对应上游产物里的 `NT`）。上游的接受阈值是
    `neg_log10_pos_hash_p_value`：它的算法文档写了（截断负二项 + 起点基线 + 两倍读长次二项试验，
    **skew > 3.0 判不通过**），但复刻它要重做一套覆盖度拟合与"起点基线"，且关键的函数形式
    没有公开依据。我们没有去编一个看着像 p 值的东西，改成直接给位置哈希分一个可调下限
    （`min_pos_hash_score`）。这与多态档"不复刻统计检验、把阈值显式留成参数"是同一条纪律。
15. **接受与否单独看 `failures`**：`evaluate_junctions` 把**全部**候选都判一遍并给出没过的
    判据名，`accept_junctions` 只是筛出通过的。没过的原因要能解释，不是悄悄丢掉。

## 5. 怎么跑测试

```bash
python -m pytest modules/bio_analysis_function/submodules/junction_calling -q
```

**47 项**全过（分段 **12** + 候选对与连接序列 **14** + 打分与排序 **10** + 接受判据 **11**）。
用例全部**手写 SAM 记录**或**直接构造分数对象**，期望值能手算，不调用比对器——输入契约就是
公共层的 SAM。

- **分段**覆盖：`2D` 留在段内、`3D`/`3I` 拆成两段（各段 query/参考区间逐位核对）、`1I` 留在段内、
  软剪裁不属于任何段、软剪裁在前时第二段的 query 坐标跟着平移、未比对/无序列/只有插入的记录
  返回空、`start_anchor` 按链取不同端点、`query_overlap`。
- **候选对**覆盖：五条判据**各自**的边界用例（长缺失成对、短缺失不成对、靠前那段必须从 read
  第一个碱基开始、判据 4 单独拦下 `8M3D8M`、独有碱基数恰好 5 与 4 的两侧、中间夹 15 与 25 个
  read 碱基的两侧）、一条 read 的两条部分比对也能成对、`CandidateSettings` 能放宽判据、
  最长 read 长度的统计；连接序列三条：左右两侧拼接的手算值、中间夹 read 碱基、两端不足时截断；
  另有同链校验与 `flank` 参数校验。
- **打分与排序**覆盖：位置哈希分去重、最小重叠分取两侧较小值、身份标签、两个分数并列时的第二
  排序键、两条截断上限（候选数 / 累计长度预算）、参数校验；以及**端到端**——25 条起点各不相同的
  read 跨过同一个 3 bp 缺失，候选合并成 1 条、位置哈希分正好是 25，同一 read 出现两次只算一条。
- **接受判据**覆盖：四条支撑判据**各自**的门槛（只有一条链时判据 1 与判据 3 一起不过、两侧 20 与
  13 的两侧、每条链 20/8 的两侧、较短一侧 2 与 3 的两侧）、位置哈希分下限（四条 read 压在同一
  起点 → 只有 `pos_hash_score` 没过；三个起点 → 通过）、阈值放宽后同一条候选转通过、
  空支撑把五条全列出来、放宽判据 1 时判据 3 仍要求两条链、`accept_junctions` 只留通过的；
  最后一条用**真实几何**造的候选（25 条跨同一缺失的 read）核对位置哈希分 25 与最长延伸 20，
  并确认"全是正链"会被两条链的判据挡下。

## 附：设计记录

### 上游是什么（带出处）

[breseq · Methods](https://gensoft.pasteur.fr/docs/breseq/0.35.7/methods.html) 的
"New junction evidence (JC)" 一节，逐段对应：

1. **预处理**："> 2 bp 插入或缺失的比对被拆成子比对"（理由：长空位在简单重复附近本来就不稳）。
   → 本模块 `segments.py`。
2. **五条判据**：原文四条并列要点（一个比对从 read 首碱基开始 / 两段合起来比任何单段多 2 bp 以上 /
   两段各 ≥5 bp 独有 / 其中一段 ≥10 bp 独有 / 中间最多 20 bp）。→ `find_pairs`。
3. **连接序列**：由参考序列与"中间那段只属于 read 的碱基"拼成；两端各留"整个数据集最长 read
   的长度"的参考碱基。→ `build_junction_sequence`。
4. **位置哈希分**：支持该连接的 read 里不同起点位置的个数；并列时用"每条 read 两侧独有碱基数的
   较小值之和"（最小重叠分）。→ `JunctionCandidate` 的两个属性。
5. **截断**：按这两个分数保留到累计长度超过 **0.1×参考总长**或候选数超过 **5000**。→ `rank_candidates`。
6. **接受**：四条支撑判据——两条链都有 read / 有 read 在两侧各延伸 ≥14 bp / 每条链上有 read 在
   两侧各延伸 ≥9 bp / 有 read 的较短一侧也延伸 ≥3 bp。→ `acceptance.py`。
   上游的接受阈值本来是"位置哈希分按 `neg_log10_pos_hash_p_value`（"skew"）超过临界值"，
   **这个统计量的算法文档里写清楚了**：对 unique-only 位置的深度拟合**截断负二项** →
   统计"有多大的「位置 × 链」组合有 read 起点落在那里"作**基线** → 用基线算出"某位置某链在
   给定深度下至少有一条 read 从那里起"的概率 → 按**二项分布**（试验次数取"两倍读长"）算观察到
   实际位置哈希分的概率 → 取负 log10。**默认 skew > 3.0 判为不通过**（p < 0.001）；
   0.34.0 起还有"饱和"修正（未被起点占用的比例在深处收敛到 0.10，
   `--junction-minimum-pr-no-read-start-per-position`）。
   也就是说它是**双向的异常检验**（太挤、太散都算异常），不是"越大越显著"。
   **我们没有复刻它**：第 1、2 步要重做一套覆盖度拟合与"起点基线"，而"给定深度下至少有一条
   read 从那里起"的具体函数形式文档没给。宁可给一个能解释的阈值，也不编一个像 p 值的数——
   改成位置哈希分的可调下限（默认 3），字段写 `NT`。方向上我们更严（"不够散就拒" vs "异常就拒"），
   代价是"少数 read 从同一位置压过来但确实为真"的连接可能被漏掉。
7. **重比**（分片 C 的另一半，**未做**）：把所有 read 再比到候选连接序列上，判定它的最佳比对
   落在参考还是某条连接上（打分 = 匹配的参考碱基数 − 空位数；覆盖不足 28 bp 的比对丢弃；
   与参考并列好的 read 也算支持）。它的作用是**提高敏感度**（捞回那些没有种出嵌合对的跨读），
   不影响"接受与否"的判据本身。

### 已定的实现取舍

- **吃 SAM、按 read 名聚合**：一条 read 的多条比对在 SAM 里就是多条同名记录（我们比对器的
  多重命中、外部 bowtie2 的 partial alignment 都是这个形态）。这样本模块同样**只依赖公共层**，
  不 import `read_mapping`（开发规则 3.1 第 4 条）。分片 C 要"重比"时，比对器以**参数注入**
  的方式进来，而不是 import 进来。
- **`intervening` 进候选身份**：同一条连接序列才合并。代价是"同一位置插了不同序列"会被当成
  两条候选——那本来就是两件事。
- **同链限制**：见约定 7。这是本项目 `SEQ` 口径的直接后果，明确报错而不是猜。
- **不实现"trims each alignment to remove portions with mismatched bases or indels"**：上游在
  两段重叠时会先修剪掉错配/空位部分再分配重叠。我们按 query 区间的几何重叠直接算
  unique/overlap，**不修剪**——这是一处已知差异，影响的是边界读数，不影响"是不是一对"。
- **不实现 `repeat_region` 偏好**：上游"若参考有 `repeat_region` 注释，偏好让连接正好落在其边界上"。
  我们的 `reference_io` 里还没有这类特征的使用场景，先不做（记进待办）。

**分片 C（接受判据）另加的取舍：**

- **"延伸进某一侧"= 该侧的独有碱基数**：上游写的是"reads that extend at least 14 base pairs
  into each side of the reference"，我们在嵌合对上已经有精确的两个 `unique_*`，直接用它们，
  不再去解析二次比对的坐标——那需要引入比对器，收益只是同一个量的近似值。
- **不复刻 skew**：见约定 14。宁可给一个能解释的阈值，也不编一个像 p 值的数。
- **判据 4（≥3）明知被判据 2（≥14）蕴含，仍然单独实现**：上游列了四条，我们逐条对应、
  逐条给名字（`failures` 里能看出是哪条没过）；哪天有人要放松判据 2，判据 4 还在。
- **四条判据的量都预先算进 `JunctionEvidence`**（两条链的 read 数、两条链的最长延伸、总最长延伸），
  判定"为什么没过"时不必再回查原始 read。
- **不做"按最佳比对数排序后再判"**：上游说"junctions are tested in order from those with the
  most best alignments to those with the least"，那是**重比之后**的顺序。我们没有重比，
  就按 `rank_candidates` 的分数顺序判——顺序不影响单条候选的接受与否，只影响上游内部的
  并列归属（记进待办）。

### 验证记录（分片 A / B / C）

`python -m pytest modules/bio_analysis_function/submodules/junction_calling -q` → **47 passed**
（分段 12 + 候选对与连接序列 14 + 打分与排序 10 + 接受判据 11）；`submodules` 整目录
**707 项**全过。

**写测试时被自己的数据抓到三次**：一是造"同一条 read 的两条部分比对"时给 `25M` 配了 40 bp 的
`SEQ`，被 `SamRecord` 的"SEQ 长度必须等于 CIGAR 消费的 query 长度"拦下（真实世界里这条 read
也该写成 `25M15S`，校验把我拽回了正确的构造）；二是算 36 bp 参考上 `flank=5` 该取到哪几位碱基时
数错了下标（写成 `GGGTT`，实际是 `GGTTT`）；三是"3M3D3M"这种小例子里每段只有 3 个 read 碱基，
根本过不了判据 3（每段 ≥5），测试用例本身不成立——**判据的数字要先满足，例子才有意义**。

**写 C 的时候被测试抓到一个真实的性质**：造"只有正链 read"的候选时，我只预期判据 1 不过，
测试却报了 `('both_strands', 'extension_per_strand')`——判据 3 要求"**每条链上**都有 read 在两侧
延伸 ≥9 bp"，一条链上压根没有 read 时，它必然不过。也就是说**判据 1 和判据 3 是绑在一起的**，
单独放宽判据 1 没有意义。这条写进了约定 13 与参数表，并且有个专门的用例盯着
（`test_both_strands_can_be_waived` 必须同时放宽两个阈值才通过）。

### 已知限制与待办

- **分片 C 里的"重比"未做**：接受判据本身已经实现（四条支撑判据 + 位置哈希分下限），
  但上游会先把所有 read 再比到候选连接序列上，用"最佳比对落在谁身上"重新分配支撑 read。
  这一步的作用是**提高敏感度**（捞回没有种出嵌合对、却仍然完整跨过连接点的 read），
  以及给上游内部的并列归属定序。要做它就得让比对器注入进来（`read_mapping` 或外部比对器），
  所以留给**编排层接通时**一起做，接口形状已经写在取舍里（打分 = 匹配参考碱基数 − 空位数、
  覆盖 <28 bp 丢弃、与参考并列也算支持）。
- **不复刻上游的 skew 分位阈值**：`neg_log10_pos_hash_p_value` 的算法文档里写了（截断负二项 +
  起点基线 + 两倍读长次二项试验，skew > 3.0 判不通过），但**要复刻就得先重做一套覆盖度拟合与
  "起点基线"**，而"某位置某链在给定深度下至少有一条 read 从那里起"的具体函数形式没有公开依据。
  现在用位置哈希分的可调下限代替（默认 3），`skew_score` 恒为 `None`（上游产物里对应 `NT`）。
  这意味着**我们的接受集合不会与上游逐条相同**——验收口径本来就是"比突变集合、允许证据细节不同"。
- **分片 D 未做**：`JC` 证据行构造与真实产物逐字段核对。真实行是
  `JC	69	.	NC_000913	1	1	NC_000913	4641652	-1	0	alignment_overlap=0	...`——
  这一串数字里哪几个是两段的坐标、`-1`/`0` 是什么，还没定死；与 `MC` 一样，**口径没定死前不开写**。
- **不写 `key=` 属性**：上游证据行里有一长串 `key=NC_000913__1__1__...` 的内部指纹，
  我们的 `JunctionKey.label` 是自己的一套，够用且确定，但不与上游一致。
- **反转类连接不支持**（两段不同链直接报错）、**不修剪重叠区的错配**、**不利用 `repeat_region`
  边界偏好**、**环状参考跨原点不处理**——四条已知差异。
- **位置哈希分只用 read 起点**：上游还强调它同时偏好"两条链上均匀分布"，本实现只数起点；
  链方向的均衡由接受判据的判据 1、3 去管（这也是上游把画分成两个分数与一串判据的原因）。
- **规模**：分段与分组都是"把记录收进内存"，与堆叠、MC 同一条限制（真实数据要换流式或原生）。
