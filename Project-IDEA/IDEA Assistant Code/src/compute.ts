export type ComputeMethodStatus = "available" | "beta" | "offline";
export type ComputeExecutionMode = "local" | "remote" | "external";
export type ComputeJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type ComputeSchemaOption = string | { value: string; label: string };

/**
 * 字段在数据流里的角色，用于工作队列组装「前输出即后输入」的串联：
 *
 * - `upstream`：本算法的数据入口，队列里由上一块的输出提供（整条队列的第一块除外，那一块要用户给真实路径）
 * - `resource`：队列外部的旁路输入（参考基因组、样本元数据等），不由上游产生，始终要用户给
 * - `output`：输出路径。队列里由执行链路按步骤分配，界面上不再要求用户填
 *
 * 省略即普通参数。
 */
export type ComputeSchemaRole = "upstream" | "resource" | "output";

export type ComputeSchemaField = {
    name: string;
    label: string;
    type: "text" | "number" | "select" | "file" | "textarea" | "switch";
    required?: boolean;
    options?: ComputeSchemaOption[];
    defaultValue?: string;
    description?: string;
    role?: ComputeSchemaRole;
};

export type ComputeMethodDefinition = {
    id: string;
    name: string;
    version: string;
    category: string;
    provider: string;
    description: string;
    executionMode: ComputeExecutionMode;
    status: ComputeMethodStatus;
    inputSchema: ComputeSchemaField[];
    outputDescription: string;
};

export type ComputeJobEvent = {
    id: string;
    jobId: string;
    type: "created" | "progress" | "completed" | "failed" | "cancelled";
    message: string;
    progress?: number;
    createdAt: string;
};

export type ComputeJobResult = {
    jobId: string;
    status: "available" | "unavailable";
    outcome?: unknown;
    outputs?: string[];
    html?: string | null;
    log?: string | null;
    data?: unknown;
};

export type ComputeJob = {
    id: string;
    methodId: string;
    methodVersion: string;
    status: ComputeJobStatus;
    progress: number;
    createdAt: string;
    error?: string;
    resultReference?: string;
};

export const BIO_PCA_METHOD: ComputeMethodDefinition = {
    id: "bio-pca",
    name: "主成分分析 PCA",
    version: "0.1.0",
    category: "多元统计",
    provider: "Project IDEA 生物方法模块 · submodules/PCA",
    description: "对数值矩阵做无监督线性降维，输出样本得分、特征载荷、特征值与解释方差。不做假设检验，不替代差异分析。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "matrixPath", label: "表达矩阵文件", type: "text", required: true, description: "CSV/TSV 路径。行为样本、列为特征，首行为特征名，首列为样本 ID。", role: "upstream" },
        { name: "delimiter", label: "分隔符", type: "select", options: ["自动识别", "逗号", "制表符"] },
        { name: "transform", label: "数据变换", type: "select", options: ["center", "zscore", "log1p", "none"] },
        { name: "nComponents", label: "保留主成分数", type: "number", description: "留空则保留全部可用主成分。" },
        { name: "metadataPath", label: "样本元数据文件", type: "text", description: "可选。首列为样本 ID，其余列仅用于结果关联与绘图分组着色，不参与拟合。", role: "resource" },
    ],
    outputDescription: "返回样本得分、特征载荷、特征值、解释方差比例与累计比例，以及 PC1–PC2 散点坐标和特征载荷排序表。",
};

export const BIO_PCOA_METHOD: ComputeMethodDefinition = {
    id: "bio-pcoa",
    name: "主坐标分析 PCoA",
    version: "0.1.0",
    category: "多元统计",
    provider: "Project IDEA 生物方法模块 · submodules/PCoA",
    description: "对样本距离矩阵进行主坐标分析，输出低维坐标、特征值、解释方差和非欧距离诊断。不做假设检验，不替代 PERMANOVA 等组间检验。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "distancePath", label: "距离矩阵文件", type: "text", required: true, description: "CSV/TSV 路径。必须是带表头和行标签的方阵，行列样本 ID 完全一致，对角线为 0。", role: "upstream" },
        { name: "delimiter", label: "分隔符", type: "select", options: ["自动识别", "逗号", "制表符"] },
        { name: "distanceMetric", label: "距离度量", type: "select", options: ["已有距离矩阵", "Euclidean", "Bray-Curtis"], description: "选择已有距离矩阵，或由输入特征矩阵计算距离。" },
        { name: "nComponents", label: "保留主坐标数", type: "number", description: "留空则保留全部正特征值对应的主坐标。" },
        { name: "metadataPath", label: "样本元数据文件", type: "text", description: "可选。首列为样本 ID，其余列仅用于结果关联与绘图分组，不参与距离或坐标计算。", role: "resource" },
    ],
    outputDescription: "返回主坐标、特征值、解释方差比例与累计比例、负特征值、样本数和 isEuclidean 诊断，以及 PCoA1–PCoA2 散点坐标。",
};

export const BIO_QUALITY_TRIMMING_METHOD: ComputeMethodDefinition = {
    id: "bio-quality-trimming",
    name: "reads 质量修剪",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/quality_trimming",
    description: "按质量数据逐条修剪测序 reads 末端的低质量碱基，输出修剪后的 FASTQ。只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释或数据库，也不要求上游先做过其他处理。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。当前只处理单端数据。", role: "upstream" },
        { name: "cutMode", label: "剪切模式", type: "select", required: true, options: ["3' 端剪切", "5' 端与 3' 端剪切", "激进剪切", "不做质量剪切"], description: "3' 端剪切对应 cut_tail，最常用；激进剪切对应 cut_right，会砍掉局部低质量之后的区域；不做质量剪切时只按下面两个固定位置参数裁剪。" },
        { name: "windowSize", label: "窗口大小", type: "number", description: "判定时取连续多少个碱基的平均质量，默认 4。" },
        { name: "meanQuality", label: "窗口平均质量阈值", type: "number", description: "Phred 阈值，默认 20（即 Q20，对应 1% 错误率）。调高会切得更多。" },
        { name: "trimFront", label: "头部固定修剪碱基数", type: "number", description: "可选。从头切掉固定个数，用于去掉已知的 UMI 或 index。默认 0。" },
        { name: "trimTail", label: "尾部固定修剪碱基数", type: "number", description: "可选。从尾切掉固定个数。默认 0。" },
    ],
    outputDescription: "返回修剪后的 reads 文件引用，以及统计摘要：输入 reads 数、保留数、其中被修剪的条数、因修剪后过短而丢弃的条数、修剪前后总碱基数与碱基去除比例。",
};

export const BIO_POLY_TRIMMING_METHOD: ComputeMethodDefinition = {
    id: "bio-poly-trimming",
    name: "reads 尾部 poly 修剪",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/poly_trimming",
    description: "切掉 reads 3' 端一连串同种碱基的尾巴。只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释或数据库，也不要求上游先做过其他处理。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。当前只处理单端数据。", role: "upstream" },
        { name: "trimMode", label: "修剪类型", type: "select", required: true, options: ["任意同种碱基尾巴（polyX）", "仅 G 尾巴（polyG）", "两者都做"], description: "polyG 针对 NovaSeq / NextSeq 等双色合成仪器的系统性假象——G 被编码为「两个荧光通道都没有信号」，信号变暗时会被误读成一串 G；polyX 不限碱基种类，会自行判定尾巴是 A/T/C/G 中的哪一种。" },
        { name: "minLength", label: "最短尾巴长度", type: "number", description: "至少要扫过多少个碱基才认为存在 poly 尾巴，默认 10。调小会更激进，也更容易误伤。" },
    ],
    outputDescription: "返回修剪后的 reads 文件引用，以及统计摘要：输入 reads 数、保留数、其中被修剪的条数、修剪前后总碱基数与碱基去除比例。本算法只裁剪、不丢弃 read。",
};

export const BIO_ADAPTER_TRIMMING_METHOD: ComputeMethodDefinition = {
    id: "bio-adapter-trimming",
    name: "reads 接头裁剪",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/adapter_trimming",
    description: "把接头从 read 上剪掉，FASTQ 进、FASTQ 出。接头来源可选自动检测（先按内置的 234 条已知接头与 k-mer 富集找出接头）或手动指定；容错规则为每 8 个碱基允许 1 个错配，并允许 1 个插入或缺失，也支持接头二聚体（read 开头缺了几个碱基）。只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释或数据库。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "输入 FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。当前只处理单端数据。", role: "upstream" },
        { name: "outputPath", label: "输出文件", type: "text", required: true, description: "结果 FASTQ 的输出路径；父目录会自动创建。", role: "output" },
        { name: "adapterSource", label: "接头来源", type: "select", required: true, description: "手动指定：直接用给定序列；候选表：依次尝试多条；自动检测：先从数据里检测接头再裁剪。最可靠的是手动指定，因为接头取决于建库试剂盒。", options: [ { value: "auto", label: "自动检测" }, { value: "sequence", label: "手动指定" }, { value: "list", label: "候选表" } ] },
        { name: "adapterSequence", label: "接头序列", type: "text", description: "「手动指定」时填，大写 ACGT，至少 4 个碱基。" },
        { name: "adapterList", label: "候选接头表", type: "text", description: "「候选表」时填，多行或逗号分隔；留空则用内置的 234 条已知接头。" },
        { name: "matchRequired", label: "最短匹配长度", type: "number", description: "接头比它还短就直接不裁，默认 4。候选多于 16 条时自动抬到 5、多于 256 条抬到 6。" },
        { name: "allowOneGap", label: "允许 1 个插入/缺失", type: "switch", defaultValue: "true", description: "默认开启；关掉只做等长比对，更严格但可能漏掉含有插入/缺失的接头。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：gzip 输入就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出裁剪后的 FASTQ，并返回统计：总 read 数、被裁过的 read 数与占比、去掉的碱基数，以及切下来的接头序列及其出现次数（最多 5 条）。本算法不丢弃任何 read——长度被裁到 0 的会原样写出，交给后续的 reads 过滤按长度丢弃。",
};

export const BIO_READ_FILTERING_METHOD: ComputeMethodDefinition = {
    id: "bio-read-filtering",
    name: "reads 过滤",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/read_filtering",
    description: "按质量、N 含量、长度与复杂度逐条判定 reads 的去留，只输出通过的 reads，并按失败原因分类统计。只需要一份 FASTQ 本身即可运行，不依赖参考基因组、注释或数据库，也不要求上游先做过其他处理。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。当前只处理单端数据。", role: "upstream" },
        { name: "qualifiedQuality", label: "达标质量（Phred）", type: "number", description: "低于它的碱基算低质量，默认 15。调高会更严格（Q20 对应 1% 错误率）。" },
        { name: "unqualifiedPercentLimit", label: "低质量碱基比例上限（%）", type: "number", description: "低质量碱基数超过「上限 × 读长 / 100」即丢弃该 read，默认 40。恰好等于上限时通过。" },
        { name: "nBaseLimit", label: "N 碱基数量上限", type: "number", description: "含 N 个数超过它即丢弃该 read，默认 5。" },
        { name: "averageQual", label: "平均质量下限", type: "number", description: "整条 read 的平均 Phred 质量低于它即丢弃，默认 0 表示不设要求。" },
        { name: "requiredLength", label: "最短长度", type: "number", description: "短于它的 read 丢弃，默认 15。" },
        { name: "maxLength", label: "最长长度", type: "number", description: "长于它的 read 丢弃，默认 0 表示不限。" },
        { name: "complexityMode", label: "低复杂度过滤", type: "select", options: ["不启用", "启用"], description: "拦掉 AAAAAA… 这类序列，默认不启用（与 fastp 一致）。" },
        { name: "complexityThreshold", label: "复杂度阈值（%）", type: "number", description: "仅在上面选「启用」时生效。复杂度定义为相邻碱基不同的比例，默认 30。" },
    ],
    outputDescription: "返回通过过滤的 reads 文件引用，以及统计摘要：输入条数、通过条数、通过率、丢弃条数（按原因分为低质量 / N 过多 / 过短 / 过长 / 低复杂度）、过滤前后的总碱基数。长度为 0 的 read 一律计入「过短」。可选再产出一份失败 reads 归档：被丢弃的 read 原样另存，名字后追加失败原因标签（如 failed_too_short），条数与丢弃条数相等。",
};

export const BIO_PAIRED_END_MERGING_METHOD: ComputeMethodDefinition = {
    id: "bio-paired-end-merging",
    name: "双端 read 合并",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/paired_end_merging",
    description: "把一对双端 read 按 overlap 拼成一条更长的 read：R2 取反向互补后与 R1 对齐，重叠区采用 R1，R2 只补上重叠之后的那一段。只要一对原始的 R1/R2 FASTQ 就能运行，不依赖参考基因组、注释或数据库。重叠区不做碱基校正。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "R1 文件", type: "text", required: true, description: "正向 reads 的 FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 文件", type: "text", required: true, description: "反向 reads 的 FASTQ 路径；记录数必须与 R1 完全一致，不一致会报错并删除半成品输出。", role: "upstream" },
        { name: "outputPath", label: "输出文件", type: "text", required: true, description: "合并结果 FASTQ 的输出路径；父目录会自动创建。", role: "output" },
        { name: "diffLimit", label: "最大错配数", type: "number", description: "重叠区允许的错配数上限，默认 5。" },
        { name: "require", label: "最短重叠长度", type: "number", description: "重叠短于它一律判为不重叠，默认 30。调高更严格、合并率更低。" },
        { name: "diffPercentLimit", label: "错配比例上限（%）", type: "number", description: "错配数还不得超过「重叠长度 × 上限 / 100」，默认 20。" },
        { name: "allowGap", label: "允许 1 个插入/缺失", type: "switch", defaultValue: "false", description: "默认关闭。打开后会再扫一轮容错比对，但只在很短的重叠加末端 indel 时才会改变结论。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出合并后的单端 FASTQ，只包含成功合并的 read——未找到 overlap 的 read 对不会出现在输出里（上游 merge 模式下它们会另写入 R1/R2 输出文件；本算法只产出一个合并输出）。同时返回统计：总 read 对数、合并数与未合并数、输入与输出碱基数、走缺口路径的合并数。",
};

export const BIO_PAIRED_END_ADAPTER_TRIMMING_METHOD: ComputeMethodDefinition = {
    id: "bio-paired-end-adapter-trimming",
    name: "双端接头裁剪（按 overlap）",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/paired_end_adapter_trimming",
    description: "按两条 read 的重叠关系判断片段到哪里为止，把 3' 端的接头剪掉——成对 R1/R2 进、成对 R1/R2 出。不需要预先知道接头序列，只需要一对原始的 R1/R2 FASTQ。只处理「插入片段比读长还短、两端都读进接头」这一种情形；片段长于读长的数据没有接头可裁，应改用双端合并。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "R1 文件", type: "text", required: true, description: "正向 reads 的 FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 文件", type: "text", required: true, description: "反向 reads 的 FASTQ 路径；记录数必须与 R1 完全一致，不一致会报错并删除两个半成品输出。", role: "upstream" },
        { name: "output1Path", label: "输出 R1 文件", type: "text", required: true, description: "裁剪后的 R1 输出路径；父目录会自动创建。", role: "output" },
        { name: "output2Path", label: "输出 R2 文件", type: "text", required: true, description: "裁剪后的 R2 输出路径。", role: "output" },
        { name: "diffLimit", label: "最大错配数", type: "number", description: "重叠区允许的错配数上限，默认 5。" },
        { name: "require", label: "最短重叠长度", type: "number", description: "重叠短于它一律判为不重叠、也就不会裁，默认 30。" },
        { name: "diffPercentLimit", label: "错配比例上限（%）", type: "number", description: "错配数还不得超过「重叠长度 × 上限 / 100」，默认 20。" },
        { name: "allowGap", label: "允许 1 个插入/缺失", type: "switch", description: "默认关闭。打开后会再扫一轮容错比对，但只在很短的重叠加末端 indel 时才会改变结论。" },
        { name: "frontTrimmed1", label: "R1 头部已剪碱基数", type: "number", description: "仅当数据在到本算法之前已做过头部固定修剪时填写（例如先跑过带头部修剪的质量剪切）。填错会裁多或裁少且不报错，不确定时保持 0。" },
        { name: "frontTrimmed2", label: "R2 头部已剪碱基数", type: "number", description: "同上，对应 R2。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出裁剪后的 R1 与 R2 两份 FASTQ。本算法不丢弃任何 read——没检出可裁接头的 read 对原样写出，因此两份输出的条数恒等于输入的对数。同时返回统计：总 read 对数、裁过接头的对数与占比、输入与输出碱基数、切下的接头碱基数，以及切下来的接头序列（最多 5 条）。",
};

export const BIO_PAIRED_END_BASE_CORRECTION_METHOD: ComputeMethodDefinition = {
    id: "bio-paired-end-base-correction",
    name: "双端重叠区碱基校正",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/paired_end_base_correction",
    description: "同一个片段被测了两遍，同一位点有两个读数。当一边可信（质量≥Q30）、另一边不可信（≤Q14）而两者又不一致时，把低质量那边改成对侧碱基，并把高质量的质量值一并赋过去。成对 R1/R2 进、成对 R1/R2 出，不改长度、不丢 read。两个质量门槛是上游写死的常量，不可调。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "R1 文件", type: "text", required: true, description: "正向 reads 的 FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 文件", type: "text", required: true, description: "反向 reads 的 FASTQ 路径；记录数必须与 R1 完全一致，不一致会报错并删除两个半成品输出。", role: "upstream" },
        { name: "output1Path", label: "输出 R1 文件", type: "text", required: true, description: "校正后的 R1 输出路径；父目录会自动创建。", role: "output" },
        { name: "output2Path", label: "输出 R2 文件", type: "text", required: true, description: "校正后的 R2 输出路径。", role: "output" },
        { name: "diffLimit", label: "最大错配数", type: "number", description: "重叠区允许的错配数上限，默认 5。" },
        { name: "require", label: "最短重叠长度", type: "number", description: "重叠短于它一律判为不重叠、也就不会校正，默认 30。" },
        { name: "diffPercentLimit", label: "错配比例上限（%）", type: "number", description: "错配数还不得超过「重叠长度 × 上限 / 100」，默认 20。" },
        { name: "allowGap", label: "允许 1 个插入/缺失", type: "switch", description: "默认关闭。打开后若重叠带缺口则跳过校正（与上游一致：带缺口的重叠里位置对应不可靠）。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出校正后的 R1 与 R2 两份 FASTQ。校正只改碱基、不改长度也不丢 read，因此两份输出的条数恒等于输入的对数，前后碱基数相等。同时返回统计：总 read 对数、改过碱基的对数与占比、涉及的 read 条数、改正的碱基数。",
};

export const BIO_UMI_PROCESSING_METHOD: ComputeMethodDefinition = {
    id: "bio-umi-processing",
    name: "UMI 提取",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/umi_processing",
    description: "把 UMI 从 read 序列开头或名字里的 index 提取出来、挂到 read 名字上，序列里那一段会被剪掉。UMI 是建库时给每个原始 DNA 分子贴的随机标签，先搬到名字里，下游才能用它区分真重复与 PCR 重复。单端与双端都支持。只搬 UMI，不聚合也不去重。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "reads 文件（R1）", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "output1Path", label: "输出文件（R1）", type: "text", required: true, description: "处理后的 R1 输出路径；父目录会自动创建。", role: "output" },
        { name: "read2Path", label: "reads 文件（R2）", type: "text", description: "双端数据的 R2。与「输出文件（R2）」要么都填、要么都不填。选了 index2 / read2 来源时必须有它。", role: "upstream" },
        { name: "output2Path", label: "输出文件（R2）", type: "text", description: "处理后的 R2 输出路径。", role: "output" },
        { name: "location", label: "UMI 来源", type: "select", required: true, description: "read1/read2 从序列开头取（会剪掉）；index1/index2 从 read 名字里的 index 取（不剪序列）；per_index/per_read 把两条的拼起来。", options: [ { value: "read1", label: "R1 序列头部" }, { value: "read2", label: "R2 序列头部" }, { value: "index1", label: "R1 名字里的第一段 index" }, { value: "index2", label: "R2 名字里的最后一段 index" }, { value: "per_index", label: "两段 index 拼起来" }, { value: "per_read", label: "两条 read 的序列头部拼起来" } ] },
        { name: "length", label: "UMI 长度", type: "number", description: "read1 / read2 / per_read 三种来源用；0 表示不取。默认 0。" },
        { name: "skip", label: "跳过碱基数", type: "number", description: "剪掉 UMI 之后再额外跳过几个碱基，默认 0。" },
        { name: "prefix", label: "UMI 前缀", type: "text", description: "留空时标签写成 :UMI；填写后写成 :前缀_UMI。" },
        { name: "delimiter", label: "标签分隔符", type: "text", description: "标签与名字之间的分隔符，默认 :。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出处理后的 FASTQ：UMI 已写进 read 名字（插在第一个空格之前），序列里那一段已剪掉。不丢弃任何 read——UMI 取不到时名字与序列原样保留。同时返回统计：输入 read 数、挂上 UMI 的 read 数与占比、从序列里剪掉的碱基数、处理前后的总碱基数。",
};

export const BIO_DEDUPLICATION_METHOD: ComputeMethodDefinition = {
    id: "bio-deduplication",
    name: "重复检测与去重",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/deduplication",
    description: "用布隆过滤器判断哪些 read（或 read 对）与前面出现过的重复，报出重复率，并可选择把重复的丢掉。重复的来源可能是 PCR 扩增、测序深度过高或建库偏差；重复率过高会让下游的定量与变异检出出现假信号。只需要一份（或一对）FASTQ 本身即可运行，不依赖参考基因组、注释或数据库。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 文件（双端）", type: "text", description: "留空按单端处理。填写后按 read 对判重：一对的 R1 与 R2 首尾相接后一起比较，重复时成对丢弃。", role: "upstream" },
        { name: "mode", label: "处理方式", type: "select", required: true, options: ["只评估重复率", "评估并去重"], description: "只评估不产出文件（对应 fastp 的默认行为）；选去重才会写出丢掉重复后的 FASTQ。默认只评估。" },
        { name: "accuracyLevel", label: "内存档位", type: "select", description: "档位越高，位图越大、假阳性越少，占用内存依次为 1/2/4/8/16/32 GiB。默认：只评估用 1，去重用 3。", options: [ { value: "auto", label: "按默认（推荐）" }, { value: "1", label: "1 · 1 GiB" }, { value: "2", label: "2 · 2 GiB" }, { value: "3", label: "3 · 4 GiB" }, { value: "4", label: "4 · 8 GiB" }, { value: "5", label: "5 · 16 GiB" }, { value: "6", label: "6 · 32 GiB" } ] },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。只评估时该项无意义。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "选「只评估」时只返回统计、不产出文件；选「去重」时输出丢掉重复后的 FASTQ（双端则两份，始终成对对齐，保留第一次出现的那一条/对）。统计含：输入条数（双端按对计）、判为重复的条数、重复率、丢弃与保留条数、处理前后的碱基数、实际使用的内存档位。判定按文件顺序进行，因此同一份输入的结果完全可复现。",
};

export const BIO_READ_STATS_METHOD: ComputeMethodDefinition = {
    id: "bio-read-stats",
    name: "reads 质量统计",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/read_stats",
    description: "统计一份（或一对，分别跑两次）FASTQ 的质量画像：Q20/Q30/Q40 碱基比例、GC 含量、读长分布、按测序位置的质量曲线与碱基含量曲线、质量值分布、5-mer 频次。用来判断这批数据能不能用、该不该做质量剪切、从第几个循环开始变差。只需要一份 FASTQ 本身，不依赖参考基因组、注释或数据库。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。双端数据请对 R1 与 R2 各跑一次——两份的质量特征本来就该分开看。", role: "upstream" },
    ],
    outputDescription: "不产出新的测序文件，只返回结构化统计：条数与碱基数、平均读长与读长分布、Q20/Q30/Q40 碱基数及比例、GC 含量、每一条按测序位置的质量曲线（整体均值与 A/T/C/G 各自）与碱基含量曲线（A/T/C/G/N/GC）、质量值分布、1024 个 5-mer 的频次表。质量最低的位置会单独点出来——只看全局平均看不出质量是从第几个循环开始塌的。",
};

export const BIO_INSERT_SIZE_METHOD: ComputeMethodDefinition = {
    id: "bio-insert-size",
    name: "双端插入片段长度分布",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/insert_size_distribution",
    description: "靠两条 read 的重叠关系倒推每一对的插入片段长度，汇成直方图并找出峰值。片段长度的分布比平均值更能说明建库质量：太宽说明片段筛选不干净、多个峰说明混了两次建库。只要一对原始的 R1/R2 FASTQ，不需要参考基因组。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "R1 文件", type: "text", required: true, description: "正向 reads 的 FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 文件", type: "text", required: true, description: "反向 reads 的 FASTQ 路径；记录数必须与 R1 完全一致，不一致会报错。", role: "upstream" },
        { name: "maxSize", label: "直方图上限", type: "number", description: "片段长度超过它的并入「判不出/超上限」桶，默认 512。峰值只在上限之内找。" },
        { name: "require", label: "最短重叠长度", type: "number", description: "重叠短于它一律判为不重叠、也就判不出片段长度，默认 30。调高更严格、判不出的更多。" },
        { name: "diffLimit", label: "最大错配数", type: "number", description: "重叠区允许的错配数上限，默认 5。" },
        { name: "diffPercentLimit", label: "错配比例上限（%）", type: "number", description: "错配数还不得超过「重叠长度 × 上限 / 100」，默认 20。" },
        { name: "allowGap", label: "允许 1 个插入/缺失", type: "switch", defaultValue: "false", description: "默认关闭。打开后多扫一轮容错比对。" },
    ],
    outputDescription: "不产出新的测序文件，只返回统计：输入 read 对数、能判出片段长度的对数与占比、片段长度直方图（下标即长度，最后一项是「判不出/超上限」桶）、峰值片段长度。片段长度是推算出来的——找不到重叠的那些对进「判不出」桶，那个桶有多大由占比直接反映。",
};

export const BIO_OVERREPRESENTED_SEQUENCES_METHOD: ComputeMethodDefinition = {
    id: "bio-overrepresented-sequences",
    name: "过表达序列分析",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/overrepresented_sequences",
    description: "找出数据里异常高频的片段——接头残留、污染、rRNA 之类的线索。与接头检测的区别是：接头检测只找接头序列，这里凡是异常高频的片段都报，是更一般的污染排查。只在文件开头一段数据上找候选，再在全文件上按采样量化。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "sampling", label: "采样率", type: "number", description: "量化阶段每多少条取一条来数，默认 100。取 1 表示全查（更准也更慢）。" },
    ],
    outputDescription: "不产出新的测序文件，只返回结论：检出多少条过表达序列、每条的序列、采样计数、推算总量、占碱基比例，以及它在 read 各位置上的分布。干净数据检不出任何一条是正常的；有检出时先看是不是接头残留，再看它的位置分布是否集中在固定一端。注意这一步的内存开销与扫描碱基数同量级（默认扫描 151 万碱基），这是上游的取舍。",
};

export const BIO_READ_NORMALIZATION_METHOD: ComputeMethodDefinition = {
    id: "bio-read-normalization",
    name: "reads 规范化",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/read_normalization",
    description: "处理链最前面的一步：把名字或质量编码上需要先归置的东西归置好。两件事各自可开关——质量编码从 Phred+64 转成通行的 Phred+33（老数据、部分平台用 64；不转的话所有按质量判定的算法都会偏移 31，而且不会报错）；把 MGI 风格的 xxx/1 改成 xxx /1（很多下游 BAM 工具要求的形态）。只动质量字符与名字，不改碱基。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "outputPath", label: "输出文件", type: "text", required: true, description: "规范化后的输出路径；父目录会自动创建。", role: "output" },
        { name: "read2Path", label: "R2 文件", type: "text", description: "双端数据填写；与「输出文件（R2）」要么都填、要么都不填。", role: "upstream" },
        { name: "output2Path", label: "输出文件（R2）", type: "text", description: "规范化后的 R2 输出路径。", role: "output" },
        { name: "phredMode", label: "输入质量编码", type: "select", options: ["Phred+33（默认）", "Phred+64（转成 33）"], description: "选 33 表示不改质量。**不做自动判断**——两种编码在高质量区间完全重叠，猜错会让所有质量结论偏移 31。" },
        { name: "fixMgi", label: "修复 MGI 名字", type: "switch", defaultValue: "false", description: "把 xxx/1 改成 xxx /1（斜杠前插一个空格）。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出规范化后的 FASTQ（双端两份），另外返回统计：输入 read 条数、改过名字的条数、重编码质量的条数、处理前后的碱基数（两者必然相等——规范化不改长度）。不丢弃任何 read、不改碱基。",
};

export const BIO_INDEX_FILTERING_METHOD: ComputeMethodDefinition = {
    id: "bio-index-filtering",
    name: "按 index 过滤",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 生物方法模块 · submodules/index_filtering",
    description: "按 index（barcode）黑名单把不属于本样本的 read 筛掉——一次 run 里混着多个样本，建库或拆分出错时会冒出别的样本的 index（交叉污染、index hopping），在比对之前筛掉能省一大堆假结果。给一份黑名单（每行一个 index 序列），命中就丢。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "readPath", label: "reads 文件", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "outputPath", label: "输出文件", type: "text", required: true, description: "保留下来的 reads 的输出路径；父目录会自动创建。", role: "output" },
        { name: "read2Path", label: "R2 文件", type: "text", description: "双端数据填写；与「输出文件（R2）」要么都填、要么都不填。任一端命中就丢整对。", role: "upstream" },
        { name: "output2Path", label: "输出文件（R2）", type: "text", description: "保留的 R2 输出路径。", role: "output" },
        { name: "blacklist1", label: "index 黑名单（R1）", type: "textarea", description: "每行一个 index 序列，与 R1 名字里的第一段 index 比对。" },
        { name: "blacklist2", label: "index 黑名单（R2）", type: "textarea", description: "每行一个；与 R2 名字里的最后一段 index 比对。单端时忽略。" },
        { name: "threshold", label: "允许的错配数", type: "number", description: "默认 0（完全一致）。调高更宽松，但会误伤。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出过滤后的 FASTQ（双端两份，始终成对对齐），以及统计：输入条数（双端按对计）、被过滤掉的条数、过滤前后的碱基数。两份黑名单都空时不过滤任何 read。注意匹配只比两者中较短的长度——名字里取不到 index 的 read 会被任何非空黑名单命中，那一批会被全丢。",
};

export const BIO_WORKFLOW_METHOD: ComputeMethodDefinition = {
    id: "bio-workflow",
    name: "fastp 预处理工作流",
    version: "0.1.0",
    category: "序列预处理",
    provider: "Project IDEA 能力模块 · modules/workflow",
    description: "把生物模块的预处理算法按 fastp 的标准顺序串成一条完整流程，一次读入、逐条走完、分批写出：规范化 → 按 index 过滤 → 去重判定 → 首尾修剪与滑窗质量剪切 → polyG → 接头裁剪 → （双端）overlap 分析、碱基校正、按 overlap 裁接头 → 重叠区输出 → polyX → 限长截断 → （双端）合并 → reads 过滤。每一步与单独调用对应算法时口径完全一致；因为只读一遍、只写一遍，比逐步调用省掉十几倍 I/O。只需要原始 FASTQ（单端或一对双端），不依赖参考基因组、注释或数据库。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "read1Path", label: "R1 原始数据", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip，gzip 按内容自动识别）。", role: "upstream" },
        { name: "read2Path", label: "R2 原始数据", type: "text", description: "双端数据的 R2。留空按单端处理。", role: "upstream" },
        { name: "output1Path", label: "输出 R1", type: "text", required: true, description: "处理后 R1 的输出路径；父目录会自动创建。", role: "output" },
        { name: "output2Path", label: "输出 R2", type: "text", description: "双端时必填；与 R2 输入要么都给、要么都不给。", role: "output" },
        { name: "qualityCutMode", label: "滑窗质量剪切", type: "select", options: ["不做质量剪切", "3' 端剪切", "5' 端与 3' 端剪切"], description: "默认不做（与 fastp 一致）。3' 端剪切按窗口平均质量修剪末端低质量碱基，最常用。" },
        { name: "trimPolyG", label: "polyG 修剪", type: "select", options: [ { value: "auto", label: "由数据决定" }, { value: "on", label: "开启" }, { value: "off", label: "关闭" } ], description: "默认由数据决定：双色测序仪（NextSeq/NovaSeq 等）的数据自动开启 polyG 修剪——那种化学里 G 是「两个荧光通道都没信号」，信号变暗的一簇会被误读成成片的 G。" },
        { name: "adapterTrimming", label: "接头裁剪", type: "switch", defaultValue: "true", description: "默认开启。单端自动检测接头；双端默认按 overlap 判断，找不到时退化为按给定序列匹配。" },
        { name: "detectAdapterForPE", label: "双端自动检测接头", type: "switch", defaultValue: "false", description: "双端默认不检测（与 fastp 一致）；打开后对 R1/R2 各自检测。单端本就默认检测，不受该项影响。" },
        { name: "correction", label: "重叠区碱基校正", type: "switch", defaultValue: "false", description: "仅双端。用高质量一侧改正低质量一侧的错配碱基，不改长度、不丢 read。默认关闭。" },
        { name: "dedup", label: "重复处理", type: "select", options: [ { value: "none", label: "不做" }, { value: "evaluate", label: "只评估重复率" }, { value: "filter", label: "评估并去重" } ], description: "默认不做。判定在读取端按文件顺序串行进行，因此结果可复现（与单独的去重算法口径一致）；去重会丢数据。" },
        { name: "merge", label: "合并模式", type: "switch", defaultValue: "false", description: "仅双端。打开后把能拼上的 read 对按 overlap 合并成一条写入合并输出，未合并的默认丢弃。" },
        { name: "splitRecords", label: "分卷（每卷条数）", type: "number", description: "默认 0 表示不分卷。分卷产物名为序号前缀加原文件名（如 0001.out.fq）。分卷时只写主输出，不能同时要求落单/失败/合并/重叠区输出。" },
        { name: "threads", label: "线程数", type: "number", description: "默认 0 由实现决定。线程数只影响速度，不影响结果（输出逐字节相同）。" },
        { name: "compress", label: "输出压缩", type: "select", description: "默认跟随输入：任一输入是 gzip 就输出 gzip。", options: [ { value: "follow", label: "跟随输入" }, { value: "gzip", label: "强制 gzip" }, { value: "plain", label: "强制不压缩" } ] },
    ],
    outputDescription: "输出预处理后的 FASTQ（双端两份，始终成对对齐），以及一份汇总报告。统计含：读入/写出的 read 条数与碱基数、保留比例、过滤失败明细（按 read 计）、各步改动量（规范化 / 按 index 过滤 / UMI / 质量剪切 / poly 修剪 / 接头裁剪 / 碱基校正 / 去重）、重复率、过滤前后按位置的质量与碱基含量曲线、读长分布，双端还含插入片段长度分布；另外给出预扫描阶段的结论（是否二色系统、检测到的接头序列与来源）。同时产出两份文件：一份自包含的 HTML 报告（内嵌 SVG、不执行脚本、可离线查看），以及一份机器可读的 workflow.json 日志——两者都写在输出文件所在目录下的 .idea-workflow/<任务 id>/ 里（日志供收集反馈与排障用，不呈现给用户）。",
};

export const BIO_BRESEQ_WORKFLOW_METHOD: ComputeMethodDefinition = {
    id: "bio-breseq-workflow",
    name: "breseq 参考比对与变异检测",
    version: "0.1.0",
    category: "变异检测",
    provider: "Project IDEA 能力模块 · modules/workflow/breseq",
    description: "给一份参考（GenBank / FASTA）与一份（或一对）FASTQ，做重测序分析：比对 → 碱基错误率重校准 → 共识档 / 多态档调用 → 突变注释，产出 .gd（与上游 breseq 同一格式）、注释表、摘要表，以及一份自包含 HTML 报告。**当前只做 RA 线（碱基替换）**：MC（覆盖度与缺失区）、JC（junction）两条证据线尚未接入。**不是单遍流程**：比对先落盘成 SAM，后续步骤都从 SAM 走——因此也能接外部比对结果。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "referencePath", label: "参考文件", type: "text", required: true, description: "GenBank（.gb/.gbk）或 FASTA（.fa/.fasta）路径，可 gzip。多个文件用中文分号或英文分号分隔（染色体 + 质粒）。GenBank 带基因特征，注释才报得出落在哪个基因上；纯 FASTA 只能报「某个位置变了」。", role: "resource" },
        { name: "read1Path", label: "reads 文件（R1）", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip）。", role: "upstream" },
        { name: "read2Path", label: "reads 文件（R2）", type: "text", description: "双端数据的 R2。按上游口径当单端处理：不使用配对的距离与插入片段信息，R1/R2 各自独立比对。", role: "upstream" },
        { name: "outputDir", label: "产物目录", type: "text", required: true, description: "产物的落点；文件名固定（mapped.sam / output.gd / annotations.tsv / variant_summary.tsv）。这条线的产物互相引用，所以只给目录、不逐个指定文件名。", role: "output" },
        { name: "polymorphismMode", label: "多态档", type: "select", options: [{ value: "on", label: "共识档 + 多态档" }, { value: "off", label: "只跑共识档" }], description: "默认开。多态档报的是「只有一部分 read 支持」的少数派碱基（混合群体、尚未固定的突变）；上游 breseq 默认只跑共识档。" },
        { name: "trimReadEnds", label: "read 端裁剪", type: "switch", defaultValue: "true", description: "默认开启（与上游一致）：参考里 1–18 bp 的完全重复会让 read 末端那几位碱基变得多解，裁掉它们才不会被读成「这里没变化」的反证。" },
        { name: "maxReads", label: "最多读入条数", type: "number", description: "默认 0 表示不限。**这条链目前是 Python 单线程实现**，真实细菌基因组请先用一个较小的上限试跑。" },
        { name: "eValueCutoff", label: "共识打分下限", type: "number", description: "默认 10（上游默认值）。**越大越显著**（它是负对数尺度上的量）；调大更严、漏检更多。" },
        { name: "frequencyCutoff", label: "频率下限", type: "number", description: "默认 0.8（上游默认值）：支持判定碱基的观测占比下限。" },
    ],
    outputDescription: "产出四个文件（都落在产物目录下）：mapped.sam（比对结果，可直接用 IGV 打开，也是接外部比对结果的对拍接口）、output.gd（突变，与上游 breseq 同一格式：突变一行、证据一行）、annotations.tsv（注释，一个效应一行，重叠基因占多行）、variant_summary.tsv（一条突变一行，给人扫一眼）。另给一份自包含的 HTML 报告（内嵌样式与 SVG、不执行脚本、可离线查看）与一份机器可读的 breseq_workflow.json 日志（含阶段 A 统计、比对构成、完整参数与每条突变的证据，供排障用）。**已知边界**：比对器是本项目自研的（MAPQ 只有 60/20/0 三档），不是 bowtie2，所以「哪条 read 比到哪里」与上游不同；目前只做碱基替换，大片段缺失与结构变异还没有。",
};

export const BIO_READ_MAPPING_METHOD: ComputeMethodDefinition = {
    id: "bio-read-mapping",
    name: "reads 参考比对",
    version: "0.1.0",
    category: "参考比对",
    provider: "Project IDEA 生物方法模块 · submodules/read_mapping",
    description: "把 FASTQ reads 比到参考序列上，给出每条的参考名、位置与链方向——这是重测序变异检测（breseq 方向）的第一步。做法是种子-扩展：先把参考按固定长度 k-mer 建索引（稀疏建、重复 k-mer 整条剔除），再让 read 的 k-mer 去查表投票，票数最高的若干条对角线逐位扩展、数错配，取出错配最少的一条。上游 breseq 用 bowtie2 做这一步，我们自研是因为 bowtie2 没有 Windows 构建；**不承诺与 bowtie2 的比对选择逐位一致**，验收口径是与上游比「预测出的突变集合」。需要参考基因组（FASTA / GenBank），不依赖数据库，也不需要先跑别的算法。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "referencePath", label: "参考文件", type: "text", required: true, description: "FASTA 或 GenBank 路径；可 gzip（按魔数识别）。FASTA 只提供序列，GenBank 还能提供基因注释（影响下游的突变注释，不影响比对本身）。", role: "resource" },
        { name: "read1Path", label: "reads 文件（R1）", type: "text", required: true, description: "FASTQ 路径（.fq/.fastq，可 gzip）。", role: "upstream" },
        { name: "read2Path", label: "reads 文件（R2）", type: "text", description: "双端数据的 R2。**按上游口径当单端处理**：breseq 不使用配对的距离与插入片段信息，因此 R1/R2 各自独立比对，不做配对一致性判定。", role: "upstream" },
        { name: "outputSamPath", label: "输出 SAM", type: "text", required: true, description: "比对结果写出为 SAM 的路径；父目录会自动创建。可直接用 IGV 等工具打开查看。", role: "output" },
        { name: "seedLength", label: "种子长度 k", type: "number", description: "默认 16。越大越特异、命中越少，但越容易被一个错配打断。" },
        { name: "step", label: "建索引步长", type: "number", description: "默认 4（每 4 个位置建一个窗口）。稀疏只降低灵敏度、不影响正确性：只要 read 在真实位置上有一段长度 ≥ 步长的连续完全匹配，种子就一定会命中。" },
        { name: "maxHits", label: "单 k-mer 命中上限", type: "number", description: "默认 64。超过上限的 k-mer 整条剔除（不是只留前几个位置），剔除条数会在统计里报出来。" },
        { name: "maxMismatches", label: "允许的错配数", type: "number", description: "默认 4（约 100~150 bp 读长的 3%）。**不能调得太紧**：真实的变异位点在 read 上就是错配，调紧会把它们连同比对一起丢掉，下游就看不到证据了。" },
    ],
    outputDescription: "每条 read 的比对结果：参考名（SEQ_ID）、0-based 起点、链方向（+1 / -1）、CIGAR、错配 / 插入 / 缺失 / 软剪裁数、编辑距离、映射质量（60 表示唯一命中、20 表示另有更差的位置、0 表示有同样好的另一处）、种子票数与候选对角线数；比不上的 read 明确记为未比对。同时写出 SAM 文件（头部含参考序列名与长度，记录含 FLAG / POS / CIGAR / MAPQ / NM / MD），可供 IGV 直接打开。**当前进度**：算法本体（A–F 分片：索引 / 种子投票 / 带内比对与 CIGAR / 软剪裁 / MAPQ 与 SAM 输出 / 端到端对拍与性能实测）已完成，90 项测试与性能基准都在，合成读集上可映射的全部找回、0 误报；**但桌面端的执行链路尚未接通**，因此本方法目前只登记调用契约（提交会被拒绝）。",
};

export const BIO_CONSENSUS_CALLING_METHOD: ComputeMethodDefinition = {
    id: "bio-consensus-calling",
    name: "共识碱基调用",
    version: "0.1.0",
    category: "变异检测",
    provider: "Project IDEA 生物方法模块 · submodules/consensus_calling",
    description: "从比对结果判定样本在每个参考位置上的碱基，报出与参考不一致的地方——这是重测序变异检测（breseq 方向）里真正下结论的一步。三步：先把比对摊成逐位点的堆叠；再**用数据自己校准碱基错误率**（按「参考碱基 × read 碱基 × 质量」计数、每格加伪计数 1、按行归一成条件概率，不直接相信 FASTQ 里报的 Phred 质量）；最后对每个位置给四种碱基算似然比，取最大者，用「log₁₀ 似然比 − log₁₀ 参考总长」打分（减参考总长是把「全基因组每个位置都试过」的多重比较折进去），配合默认阈值「打分 ≥ 10 且频率 ≥ 0.8」报出**碱基替换**。可选开启 read 端裁剪：落在参考 1–18 bp 完全重复里的 read 末端碱基位置本身多解（既支持没变化、也支持重复拷贝数变了），排除它们能避免把真变异当成反证压下去。输入是 SAM——我方比对器的产物或外部比对结果（bowtie2 等）都行，这也是与上游做阶段级对拍的入口；需要参考基因组，不依赖数据库。**不承诺与上游 breseq 逐位一致**，验收口径是与上游比「预测出的突变集合」。当前只判碱基替换，小 indel 的判定尚未做。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "referencePath", label: "参考文件", type: "text", required: true, description: "FASTA 或 GenBank 路径；可 gzip（按魔数识别）。用来取每个位置的参考碱基、划定 read 端裁剪的重复区间、以及算共识打分里的「参考总长」。", role: "resource" },
        { name: "samPath", label: "比对结果（SAM）", type: "text", required: true, description: "SAM 路径（可 gzip）。可以是本模块的「reads 参考比对」写出的结果，也可以是外部工具（如 bowtie2）的比对结果——后者正是与上游做阶段级对拍的入口。", role: "upstream" },
        { name: "outputCallsPath", label: "输出调用结果（TSV）", type: "text", required: true, description: "报出的碱基替换写出为 TSV 的路径；父目录会自动创建。列：参考名、位置（0-based）、参考碱基、判定碱基、似然比、共识打分、变异频率、支持观测数、总观测数。", role: "output" },
        { name: "eValueCutoff", label: "共识打分下限", type: "number", description: "默认 10，与上游一致。**越大越显著**（它是负对数尺度上的量，不是通常那种「越小越好」的 E 值）：调大更严、漏检更多，调小假阳性更多。" },
        { name: "frequencyCutoff", label: "变异频率下限", type: "number", description: "默认 0.8，与上游一致。共识模式假定变异在样本里占 100%，这个阈值是给「少数 read 恰好带测序错误」留的余量。" },
        { name: "defaultQuality", label: "缺失质量的默认值", type: "number", description: "默认 0：SAM 的 QUAL 为 * （没有质量）时按这个值处理。0 是最保守的假设（把未知质量当最差的碱基），不会凭空给证据加分。" },
        { name: "useTrimming", label: "read 端裁剪", type: "switch", defaultValue: "true", description: "默认开启（与上游一致）：按参考里 1–18 bp 的完全重复，把 read 两端落在重复里的碱基排除出证据，每端至少排除 1 个。库里默认是不裁剪、由调用方显式传表；这里作为产品入口默认打开。" },
    ],
    outputDescription: "报出的碱基替换列表：参考名、0-based 位置、参考碱基、判定碱基、似然比 log₁₀(L(判定)/L(参考))、共识打分（似然比减去 log₁₀ 参考总长）、变异频率、支持观测数与总观测数；没有通过阈值的位置不报出。**位置是 0-based**——下一站「突变注释」吃的是 1-based，接线时 +1（库里有 `Substitution.from_zero_based` 专门做这件事）。同时给出统计摘要：入表观测数、各类被排除的观测数（参考碱基为 N / read 碱基为 N / 长缺失 / 被端裁剪）、以及按质量的经验错误率表（便于核对「这个数据的错误率长什么样」）。**当前进度**：算法本体（分片 A–D：堆叠 / 错误率重校准 / 打分与判定 / read 端裁剪）已完成，51 项测试全过，合成已知突变的数据集上检出集合与真值集合逐项吻合（精确率与召回率都是 1）；**但桌面端的执行链路尚未接通**，因此本方法目前只登记调用契约（提交会被拒绝）。",
};

export const BIO_MUTATION_ANNOTATION_METHOD: ComputeMethodDefinition = {
    id: "bio-mutation-annotation",
    name: "突变注释",
    version: "0.1.0",
    category: "变异检测",
    provider: "Project IDEA 生物方法模块 · submodules/mutation_annotation",
    description: "把「参考上某个位置变了」翻译成「这对基因与蛋白意味着什么」——上游 breseq 报告里 `F239L (TTT→TTG) araJ+ predicted transporter`、`intergenic (-110/-179)`、`coding (96-111/4554 nt)` 那一列就是这一步的产物。输入是变异清单（参考名、位置、参考碱基、判定碱基——通常直接接上一步「共识碱基调用」的输出），配合参考基因组的特征表，判定每个变异落在哪里、是什么意思：落在编码区就给氨基酸改变（同义 / 错义 / 无义 / 终止密码子丢失 / 起始密码子改变 / 起始子丢失）与「第几个密码子、密码子怎么变」；落在**重叠基因**里每个基因各给一条（细菌基因组里同一个碱基属于两个基因是常态，phiX174 有 1392 个位置如此）；落在基因之间就给两侧最近基因与距离（`intergenic (+22/-4)`，符号按转录方向：`+` 是该基因的转录下游）；落在 tRNA / rRNA 等非编码基因特征内给 `noncoding (n/m nt)`。口径：标准遗传密码（NCBI 表 1）、起始密码子统一译成 M、终止密码子不进蛋白、负链基因的密码子按编码方向取互补。**不承诺与上游 breseq 逐字一致**，验收口径是「预测出的突变集合」。",
    executionMode: "local",
    status: "beta",
    inputSchema: [
        { name: "referencePath", label: "参考文件（带注释）", type: "text", required: true, description: "FASTA 或 GenBank 路径；可 gzip（按魔数识别）。注释这一步需要 GenBank（或带特征表的参考）——FASTA 只有序列，算不出基因与密码子。", role: "resource" },
        { name: "variantsPath", label: "变异清单（TSV）", type: "text", required: true, description: "每行一条变异：参考名、**1-based** 位置、参考碱基、判定碱基。注意上一步「共识碱基调用」的表格给的是 0-based 位置，接线时要 +1（库里有 `Substitution.from_zero_based` 专门做这件事，别手工加减）；外部调用结果（breseq / GATK 导出的位点表）按同样四列整理即可复用本方法。", role: "upstream" },
        { name: "outputTablePath", label: "输出注释表（TSV）", type: "text", required: true, description: "注释表写出路径；父目录会自动创建。列：参考名、位置、参考碱基、判定碱基、分类（coding / intergenic / noncoding / unannotated）、基因、效应、描述、产物——**一个效应一行**，重叠基因占多行。", role: "output" },
        { name: "geneKinds", label: "算作「基因」的特征类型", type: "text", description: "默认 CDS,tRNA,rRNA,tmRNA,ncRNA,misc_RNA（逗号分隔）。它们既决定「落在基因内还是基因间」，也决定两侧邻居是谁；misc_feature、repeat_region 这类不算，否则基因间会被无关注释切碎。" },
    ],
    outputDescription: "注释表：每条变异一行或数行（重叠基因各一行），含分类、基因、效应、描述（`F239L (TTT→TTG)` / `intergenic (+22/-4)` / `noncoding (4/8 nt)`）与基因产物；同一批还会给出统计（每条变异落在哪一类、编码区 / 基因间 / 非编码各多少位置）。**当前进度**：算法本体（分片 A–D：遗传密码与 CDS 翻译 / 变异效应 / 基因间与距离 / 端到端注释表）已完成，62 项测试全过，并在真实参考 phiX174 上做过全位置扫描（5386 个位置全部分类；95.97% 落在编码区、1392 个位置属于多个重叠基因——与「这个基因组基因挤在一起」的常识一致）；**但桌面端的执行链路尚未接通**，因此本方法目前只登记调用契约（提交会被拒绝）。",
};

export const COMPUTE_METHODS: ComputeMethodDefinition[] = [
    BIO_PCA_METHOD,
    BIO_PCOA_METHOD,
    BIO_QUALITY_TRIMMING_METHOD,
    BIO_POLY_TRIMMING_METHOD,
    BIO_ADAPTER_TRIMMING_METHOD,
    BIO_READ_FILTERING_METHOD,
    BIO_PAIRED_END_MERGING_METHOD,
    BIO_PAIRED_END_ADAPTER_TRIMMING_METHOD,
    BIO_PAIRED_END_BASE_CORRECTION_METHOD,
    BIO_UMI_PROCESSING_METHOD,
    BIO_DEDUPLICATION_METHOD,
    BIO_READ_STATS_METHOD,
    BIO_INSERT_SIZE_METHOD,
    BIO_OVERREPRESENTED_SEQUENCES_METHOD,
    BIO_READ_NORMALIZATION_METHOD,
    BIO_INDEX_FILTERING_METHOD,
    BIO_WORKFLOW_METHOD,
    BIO_BRESEQ_WORKFLOW_METHOD,
    BIO_READ_MAPPING_METHOD,
    BIO_CONSENSUS_CALLING_METHOD,
    BIO_MUTATION_ANNOTATION_METHOD,
    {
        id: "sequence-analysis-placeholder",
        name: "序列分析方法接口",
        version: "0.1.0",
        category: "序列分析",
        provider: "待接入方法服务",
        description: "用于验证生物计算方法输入、任务提交、进度与结果接口的占位方法。当前不执行真实计算。",
        executionMode: "external",
        status: "beta",
        inputSchema: [
            { name: "sequence", label: "序列数据", type: "textarea", required: true },
            { name: "format", label: "数据格式", type: "select", required: true, options: ["FASTA", "FASTQ", "纯文本"] },
        ],
        outputDescription: "返回标准化的计算结果引用与结构化结果数据。",
    },
    {
        id: "clinical-nutrition-placeholder",
        name: "临床营养评估接口",
        version: "0.1.0",
        category: "临床营养",
        provider: "待接入公共服务",
        description: "用于承载后续临床营养测算服务的统一调用契约。当前仅提供接口展示。",
        executionMode: "remote",
        status: "offline",
        inputSchema: [
            { name: "caseData", label: "病例数据", type: "textarea", required: true },
            { name: "assessmentType", label: "评估类型", type: "select", required: true, options: ["基础评估", "综合评估"] },
        ],
        outputDescription: "返回评估任务状态和结果下载地址。",
    },
];

export type ComputeApi = {
    listMethods: () => Promise<ComputeMethodDefinition[]>;
    createJob: (methodId: string, input: Record<string, string>) => Promise<ComputeJob>;
    getJob: (jobId: string) => Promise<ComputeJob>;
    cancelJob: (jobId: string) => Promise<ComputeJob>;
    listJobEvents: (jobId: string) => Promise<ComputeJobEvent[]>;
    getJobResult: (jobId: string) => Promise<ComputeJobResult>;
    openJobResult: (jobId: string, target: "report" | "output") => Promise<{ path: string }>;
};

/** 两条"流程型"入口：给的是整条流程（参考 + reads 进、产物与报告出），不是一个算法步骤。 */
export const WORKFLOW_METHOD_IDS: readonly string[] = ["bio-workflow", "bio-breseq-workflow"];
/** 已经接通真实执行链路的方法：由 Electron 主进程起 Python 子进程调用对应的 wrapper。 */
export const LOCALLY_EXECUTABLE_METHOD_IDS: readonly string[] = WORKFLOW_METHOD_IDS;

export const computeApi: ComputeApi = {
    async listMethods() { return COMPUTE_METHODS; },
    async createJob(methodId, input) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.createComputeJob(methodId, input);
    },
    async getJob(jobId) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.getComputeJob(jobId);
    },
    async cancelJob(jobId) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.cancelComputeJob(jobId);
    },
    async listJobEvents(jobId) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.listComputeJobEvents(jobId);
    },
    async getJobResult(jobId) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.getComputeJobResult(jobId);
    },
    async openJobResult(jobId, target) {
        if (!window.ideaDesktop) throw new Error("桌面执行接口不可用");
        return window.ideaDesktop.openComputeJobResult(jobId, target);
    },
};
