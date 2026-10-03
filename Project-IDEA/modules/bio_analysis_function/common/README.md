# 模块公共层（common/）：工具、数据与原生层

本目录放两类东西（判定标准见 `../开发规则.md` 3.2）：

1. **不能作为算法单独存在、但算法必须使用的工具**（FASTQ 读写、流水线、k-mer 编解码、前缀树、接头表）；
2. **后续会被多次复用、但自身产出价值不大、必须进一步处理的**（接头检测）。

**这里没有任何算法广场入口**——算法入口只在 `../submodules/` 下。

> 本节专门写清**每一件工具的产出是什么、由谁消费**。工具不进广场，但它们的产出会
> 出现在算法的结果里、也会出现在前端的报告里，因此在项目内部（含课题组讨论）需要
> 能一眼说清楚"它给出什么、给谁用"。

## 1. 工具与数据（纯 Python）

| 名称 | 角色 | **产出** | 使用者 |
| --- | --- | --- | --- |
| `fastq.py` | FASTQ 流式读写与通用管道 | ① 逐条读出的记录（名字 / 序列 / 质量）；② 写出的 FASTQ 文件——`write_fastq` 写单文件，`FastqWriter` 供"同时写多个输出"的成对算法用；③ `FastqStreamSummary` 统计（输入条数、保留、改动、丢弃、前后碱基数与去除比例） | 全部预处理算法 |
| `io.py` | 表格矩阵读取 | 读入的数值矩阵与行列标签 | 统计线（PCA / PCoA 等） |
| `report.py` | 绘图用数据整理 | 散点坐标数据（供前端画图） | 统计线 |
| `svg_report.py` | **工具**：自包含 SVG 报告的绘图原语 | 页面骨架与图形片段——内嵌样式 `STYLE`、画布尺寸常量，以及坐标轴 / 折线 / 柱状 / 图例 / 卡片 / 整节的 SVG 片段。全是静态字符串，不执行脚本、不引用外部资源 | read_stats 报告（`submodules/read_stats/report.py`）与工作流报告（本模块外的 `modules/workflow/report.py`） |
| `known_adapters.py` | **数据**：234 条已知接头序列 | 无计算产出；提供按字典序排列的接头序列表 `KNOWN_ADAPTERS` | 接头检测、后续的接头裁剪 |
| `read_names.py` | **工具**：read 名字解析与规范化 | ① 名字里第一段 index（`first_index`）；② 最后一段 index（`last_index`）；③ 修好的 MGI 名字（`fix_mgi_name`）。解析方式照抄上游"倒数第 3 个字符往左扫"，不依赖字段位置 | UMI 提取（按 index 取 UMI）、按 index 过滤（拿 index 比黑名单） |
| `sequences.py` | **工具**：序列层面的小工具 | ① 反向互补串；② **单个碱基的互补**；③ 两条等长序列前若干个碱基的错配数（带提前退出的版本与完整版各一）。C++ 侧同款在 `native/src/sequence.h` | 接头裁剪（错配计数）、双端 overlap 分析、双端重叠区碱基校正（逐位比较要用单碱基互补） |
| `adapter_detection/` | **工具**：接头检测（两段式：已知表容错匹配 + k-mer 富集拼接） | **一条接头序列** + 来源（`known` 命中已知表 / `kmer` 从数据拼出）+ 证据（采样 read 数与碱基数、种子 k-mer、出现次数、富集倍数）；没检出时给出原因。**不写任何文件、不改动数据** | 后续的接头裁剪（作为"接头来源：自动检测"选项）；单独跑时它的结论只作诊断 |
| `paired_overlap/` | **工具**：双端 read 的 overlap 分析（错位量扫描：正向/反向 × 无缺口/允许 1 个缺口） | 一个 `OverlapResult`：**是否重叠、错位量 offset、重叠长度、错配数、是否带缺口**（+ 片段长度，按上游 `statInsertSize` 的两个分支算）。**不写文件、不改动输入** | 三个双端算法都要它：双端合并、双端按 overlap 裁接头、双端重叠区碱基校正（`submodules/paired_end_merging` / `paired_end_adapter_trimming` / `paired_end_base_correction`）——全部已实现 |
| `reference_io/` | **工具**：参考资料读取（GenBank / FASTA） | 一个 `ReferenceSet`：若干 `ReferenceSequence`（**序列 + 拓扑 + 描述**）与它们上面的 `Feature`（类型、位置、限定符）；另有 `parse_location` 单独解析 GenBank 位置写法。**不写文件、不做任何判定** | 参考比对与变异检测线的地基：read 比对器（要参考序列）、突变注释（要特征表与坐标）——比对器未开工，注释未开工 |
| `alignment_io/` | **工具**：比对结果读写（SAM 文本） | 一个 `SamHeader`（原始 `@` 行 + 参考序列表）与若干 `SamRecord`（11 个必填字段 + 标签、FLAG 具名属性、`reference_end`）；`Cigar` 给出 query / reference 消费长度与剪裁、indel、`is_simple` 等判定口。**不做任何比对判定**（不筛 MAPQ、不挑最佳命中） | read 比对器（写出自己的结果，供 IGV 查看）；与上游做**阶段级对拍**的读入端（读 bowtie2 的 SAM）。BAM 未支持 |

## 2. 原生层（`native/`）

C++ 实现的算法核心，通过 C ABI 由 ctypes 调用；构成、构建与验证见 [native.md](./native/native.md)。

| 名称 | 角色 | **产出** |
| --- | --- | --- |
| `native/src/fastq.{h,cpp}` | 工具：FASTQ 流式读写（含 gzip） | 与 Python 侧同口径的记录读写与统计 |
| `native/src/pipeline.h` | 工具：**单端**读取 / 计算 / 写出三段并行流水线 | 与单线程**逐字节相同**的输出与统计（含按结果码分类的计数）；给了 `dropped_path` 时额外产出一份**被丢弃 read 的归档**（名字后追加原因标签，`verdict_label` 与上游 `FAILED_TYPES` 同口径） |
| `native/src/paired_pipeline.h` | 工具：**双端**成对流水线（同步读 R1/R2 → worker×N → 保序写出） | 与单线程逐字节相同的输出与统计；**成对顺序恒定**，两份输出始终对齐 |
| `native/src/nucleotide_tree.h` | 工具：碱基前缀树 | 从一批序列里走出的"占优路径"，以及是否走到尽头（`reached_leaf`） |
| `native/src/sequence.h` | 工具：反向互补、错配计数（Python 侧 `sequences.py` 的对应物） | 反向互补串；给定前缀长度的错配数（带提前退出与完整两版） |
| `native/src/adapter_detect.{h,cpp}` | 工具：接头检测（Python 版的移植，导出 `bio_detect_adapter`） | 同 `adapter_detection/`：接头序列 + 来源 + 证据 |
| `native/src/overlap.{h,cpp}` | 工具：双端 overlap 分析（Python 版的移植，导出 `bio_analyze_overlap`） | 同 `paired_overlap/`：是否重叠 + 错位量 + 重叠长度 + 错配数 + 是否带缺口 |
| `native/src/quality_trim.{h,cpp}` | **算法**质量剪切的实现 | 修剪后的 FASTQ + 统计（**算法入口**在 `submodules/quality_trimming`） |
| `native/src/poly_trim.{h,cpp}` | **算法**poly 修剪的实现 | 同理，入口在 `submodules/poly_trimming` |
| `native/src/read_filter.{h,cpp}` | **算法**reads 过滤的实现 | 通过的 reads + 按原因分类的失败统计；给了 `failed_out` 时另出一份**被丢弃 read 的归档**，入口在 `submodules/read_filtering` |
| `native/src/adapter_trim.{h,cpp}` | **算法**接头裁剪的实现（导出 `bio_trim_adapter_fastq`） | 裁剪后的 FASTQ + 通用统计，入口在 `submodules/adapter_trimming` |
| `native/src/paired_adapter_trim.{h,cpp}` | **算法**双端按 overlap 裁接头的实现（导出 `bio_trim_paired_adapter_fastq`） | 裁剪后的 **R1/R2 两份** FASTQ + 成对统计，入口在 `submodules/paired_end_adapter_trimming` |
| `native/src/paired_base_correction.{h,cpp}` | **算法**双端重叠区碱基校正的实现（导出 `bio_correct_paired_fastq`） | 校正后的 **R1/R2 两份** FASTQ + 成对统计（不改长度），入口在 `submodules/paired_end_base_correction` |
| `native/src/paired_merge.{h,cpp}` | **算法**双端合并的实现（导出 `bio_merge_paired_fastq`） | 合并后的单端 FASTQ + 成对统计（含缺口路径计数），入口在 `submodules/paired_end_merging` |
| `native/src/umi_process.{h,cpp}` | **算法**UMI 提取的实现（导出 `bio_process_umi_fastq`） | 名字里带 UMI 的 FASTQ；**单端与双端共用同一个入口**，入口在 `submodules/umi_processing` |
| `native/src/dedup.{h,cpp}` | **算法**重复检测与去重的实现（导出 `bio_deduplicate_fastq`） | 重复率统计；去重模式下另出丢掉重复后的 FASTQ（双端两份）。**单端与双端共用同一个入口**，入口在 `submodules/deduplication` |
| `native/src/read_stats.{h,cpp}` | **算法**reads 质量统计的实现（导出 `bio_stats_*` 一组） | 一份**报告**：标量汇总、按位置的质量与含量曲线、质量值分布、1024 个 5-mer 计数、读长分布。**产物不是文件**，入口在 `submodules/read_stats` |
| `native/src/insert_size.{h,cpp}` | **算法**双端插入片段长度分布的实现（导出 `bio_insert_size_fastq`） | 片段长度直方图 + 峰值 + 判不出的条数。**产物不是文件**，入口在 `submodules/insert_size_distribution` |
| `native/src/overrep.{h,cpp}` | **算法**过表达序列分析的实现（导出 `bio_overrep_*` 一组） | 异常高频的片段列表（序列 + 计数 + 位置分布）。**产物不是文件**，入口在 `submodules/overrepresented_sequences` |

> 原生层里既有工具也有算法的实现，判断依据仍是同一条：**工具没有广场入口，算法有**。
> `quality_trim` / `poly_trim` / `read_filter` / `adapter_trim` / `paired_adapter_trim` /
> `paired_base_correction` / `paired_merge` / `umi_process` / `dedup` / `read_stats` /
> `insert_size` / `overrep` 在 `submodules/` 下都有对应的算法子模块与入口；
> `adapter_detect` / `overlap` 没有，它们以选项或内部调用的形式被算法使用。

> `paired_pipeline.h` 原先长在 `paired_merge.cpp` 内部。按 `开发规则.md` 3.2 的
> "公共能力至少出现两个真实使用方之后再提取"，它在**第二个双端算法（按 overlap 裁接头）
> 落地时**上移到公共层——两条双端流水线从此共用同一份编排，批量大小与线程口径不会各自漂移。

## 3. 测试

```bash
python -m pytest modules/bio_analysis_function/common -q
```

公共层每一项都有测试：`tests/test_fastq.py`（FASTQ 读写与管道 18 项）、
`tests/test_io.py`（表格矩阵 9 项）、`tests/test_sequences.py`（序列工具 6 项）、
`tests/test_read_names.py`（名字解析 5 项）、
`adapter_detection/tests/`（接头检测 30 项）、`paired_overlap/tests/`（overlap 分析 11 项）、
`native/tests/`（原生层对拍 207 项）。

`svg_report.py` 没有自己的测试文件——它画出来的东西由两个使用方的报告测试
覆盖（`submodules/read_stats/tests/test_report.py` 与 `modules/workflow/tests/test_report.py`），
断言的正是"图上有多少个点、页面不执行脚本、两次渲染逐字节相同"这类硬性质。
