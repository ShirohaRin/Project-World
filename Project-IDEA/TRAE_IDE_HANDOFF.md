# Program IDEA - TRAE IDE 交接包

更新日期：2026-07-29

将本文件作为本项目在 TRAE IDE 中新会话的首要上下文。先阅读本文件与根目录 `README.md`，再检查实际工作区状态；不要基于猜测重建已有功能，不要删除现有文件或覆盖用户未要求的变更。

## 可直接粘贴的启动提示词

```text
你正在接手 Program IDEA 项目。请先完整阅读 `TRAE_IDE_HANDOFF.md` 和根目录 `README.md`，再检查当前工作区的真实文件状态与 Git 状态（如有）。

工作原则：
1. 以现有代码与文档为准，先理解后修改；不要重建已存在功能。
2. 当前优先级是完善离线桌面客户端 `IDEA Assistant Code/`，暂不扩展线上服务、Linux 部署、飞书接入或 Agent 运行时，除非我明确提出。
3. 所有项目说明、架构、开发记录和操作文档使用 Markdown。
4. 不要改动 `D:\TRAE SOLO CN`；它只允许用于分析 TRAE 的界面与技术组织。
5. 完成每项修改后执行与改动匹配的构建或测试，并简要说明修改的文件、验证结果与遗留风险。

现在请先用不超过 10 条要点复述你对项目状态、边界和下一步的理解，等待我给出具体任务。
```

## 项目目标

Program IDEA 是一个本地优先的智能体工作平台，包含两个长期部分：

- `IDEA Assistant`：面向用户的 Windows 桌面平台，提供 IDE 与 Work 两种使用模式。
- `IDEA Agents`：IDEA 与三个独立专精智能体的定义、运行时与协作体系。

现阶段只完善离线版桌面客户端。Agent 在线运行时、跨设备、Linux MCP 与飞书工作流均已保留设计与服务端基础，但不是当前默认开发范围。

## 当前目录

```text
Program-IDEA/
├── IDEA Assistant Code/       # 当前桌面客户端源码，后续客户端开发的主目录
├── IDEA Assistant/            # 已封装的 Windows 客户端目录，作为用户入口
├── release/                   # 构建输出与归档目录
├── Agents/                    # 四个 Agent 的角色与系统定义层
├── server/                    # Python Agent Host、工具和 API 基础
├── .trae/agents/              # TRAE 项目级 Subagent 适配定义
├── docs/                      # 部署、跨设备和 TRAE 接入文档
├── README.md                  # 总览、目录边界与开发命令
└── TRAE_IDE_HANDOFF.md        # 本交接文档
```

不要将 `desktop/` 视为当前客户端源码入口。它属于早期遗留内容；清理或迁移前必须先确认没有仍被引用的文件。

## 桌面客户端状态

源码位于 `IDEA Assistant Code/`，技术栈为 Electron、React、TypeScript、Vite 与 CodeMirror。Electron 使用 `index.html` 作为 React 挂载入口；这不是传统 HTML 界面开发，实际界面逻辑在 React/TypeScript 中。

已具备的离线能力：

- IDE 与 WORK 两种模式，在各自的上下文工具栏中切换。
- 本地工作区选择、文件树读取、文件打开与保存。
- CodeMirror 编辑器，包含 TypeScript、JavaScript、Python、JSON、HTML、CSS、Markdown、Java、C/C++、Go、Rust、YAML、XML 的基础语言支持。
- Markdown 的“隐编辑”、源码、同步预览与预览模式。
- WORK 模式的 Chat 式离线任务会话与 Agent 选择器。
- 本地插件市场、插件开关、视觉设置面板。
- Windows x64 目录式打包配置。

关键文件：

| 文件 | 作用 |
|---|---|
| `IDEA Assistant Code/src/App.tsx` | 主界面、IDE/WORK 布局、工作区与插件状态 |
| `IDEA Assistant Code/src/CodeEditor.tsx` | CodeMirror 编辑器及语言映射 |
| `IDEA Assistant Code/src/MarkdownLiveEditor.tsx` | Markdown 隐编辑模式 |
| `IDEA Assistant Code/src/styles.css` | 主界面样式 |
| `IDEA Assistant Code/src/plugins/registry.ts` | 插件注册表 |
| `IDEA Assistant Code/src/plugins/marketplace-theme.css` | 插件市场独立配色变量 |
| `IDEA Assistant Code/electron/` | Electron 主进程与 IPC 桥接 |
| `IDEA Assistant Code/package.json` | 开发、构建与 Windows 打包命令 |

## 客户端构建

在 `IDEA Assistant Code/` 内执行：

```powershell
npm install
npm run dev:electron
npm run build
npm run package:win
```

`package:win` 当前配置为 Windows x64 的目录式打包，输出路径由 `package.json` 的 `build.directories.output` 控制。发布前应确认 `IDEA Assistant/` 内的实际启动程序可正常打开，而不是只验证构建命令成功。

## 界面与代码约束

- 用户已明确要求移除多余的最外层标题栏；不要重新加入全局标题栏。
- 模式切换应放在 `IDEA / 编辑器` 或 `IDEA / 工作台` 的上下文顶部区域。
- 插件市场的配色应只在 `src/plugins/marketplace-theme.css` 中调整，优先修改 CSS 自定义属性，不要把同类颜色散回主样式表。
- 离线版不得强依赖 Python 后端、模型 API 或线上服务；无网络时基本 IDE 功能必须可用。
- 保持偏 IDE/工作台的紧凑信息密度，避免将界面改成营销型卡片首页。
- 继续使用现有 Electron + React 技术路线，除非用户明确要求并同意一次架构迁移。

## Agent 定义

`Agents/` 是定义层，不代表当前离线客户端已加载或可执行。每个 Agent 目前包含 `system.md` 与 `character.md`。

| Agent | 定位 | 权限关系 |
|---|---|---|
| `IDEA` | 总调度、任务拆解、结果汇总与最终决策 | 最高权限，可调度其他三者 |
| `IDEA-ProgramWorldAdminister` | 世界项目管理与执行 | 独立对话/工作，不可调度其他 Agent |
| `IDEA-Reasearcher` | 科研、文献检索、数据分析、论文辅助 | 独立对话/工作，不可调度其他 Agent |
| `IDEA-AgentProducer` | Agent 创建、配置与测试 | 独立对话/工作，不可调度其他 Agent |

注意目录和标识中现存拼写为 `IDEA-Reasearcher`。在未进行全项目引用检查并获得用户确认前，不要擅自更名为 `Researcher`。

## 服务端与 TRAE 接入

`server/` 已有 FastAPI、LLM 客户端、Agent 执行循环、受控工具和本地记忆基础。它面向后续线上平台，而非当前离线客户端的启动依赖。

TRAE 接入采用双轨设计：

- `.trae/agents/`：项目级 Subagent，适合当前机器上的 TRAE IDE 本地调用。
- Linux HTTP MCP Server：面向跨设备、持久化记忆、后台任务和后续飞书接入。

具体历史设计见 `docs/trae-integration-plan.md` 与 `docs/cross-device-guide.md`。不要把文档中的部署步骤视作已经完成的线上部署事实。

云端知识库与长期记忆已纳入后续规划，专项路线图见 `docs/cloud-knowledge-memory-roadmap.md`。仅在离线客户端完成当前确认范围的开发、构建与使用验证，并且用户明确启动服务端与基础 Agent 开发后，才重新提出该议题。

## 已授权工作区

平台设计中允许受控访问的项目包括 `Program-IDEA`、`shared_rag` 与 `ShirohaV1.1`。新增工作区或扩大自动化访问范围时，需在服务端权限白名单中明确配置；Agent 不应自行扩大文件访问范围。

## 后续任务入口

用户下一条具体请求将决定优先级。若用户只要求“继续完善”，应先审阅 `IDEA Assistant Code/` 的当前构建状态、界面可用性与打包入口，再提出一项范围清晰的改进；不要自动开启 Agent 运行时、Linux 服务部署或飞书集成。

## 风险与注意事项

- 代码目录、已打包目录与 `release/` 可能同时存在不同版本，修改前必须确认目标目录。
- 曾出现 Electron 打包过程阻塞，使用目录式打包和隔离输出目录缓解；遇到问题应保留日志并避免清空已有发布目录。
- 用户允许查看 `D:\TRAE SOLO CN` 以学习组织方式，但明确禁止修改该安装目录。
- 根目录文档要求统一 Markdown；新建项目说明不要改为 HTML、DOCX 或其他格式，除非用户重新指定。
