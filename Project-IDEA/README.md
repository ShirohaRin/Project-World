# Program IDEA

Program IDEA 是一个本地优先的智能体工作平台。当前开发重心是 `IDEA Assistant` 桌面应用、Python Agent Host、工作区访问控制与任务会话能力；Agent 的运行时装配、版本管理和扩展加载将在后续阶段接入。

## 文档格式约定

项目总览、架构说明、平台设计、Agent 定义、开发记录与操作指南统一使用 Markdown（`.md`）格式维护。新增相关文档应存放在项目根目录、`docs/` 或所属模块目录中，并使用 `.md` 扩展名；HTML 仅保留为已生成的展示材料或独立界面资源，不作为项目文档的源文件。

## 当前目录架构

```text
Program-IDEA/
├── Agents/                         # 四个 Agent 的定义层；当前不参与运行时加载
│   ├── IDEA/                       # L0：主智能体角色与系统定义
│   ├── IDEA-ProgramWorldAdminister/# L1：项目管理 Agent 定义
│   ├── IDEA-Reasearcher/           # L1：科研分析 Agent 定义
│   ├── IDEA-AgentProducer/         # L1：Agent 生产 Agent 定义
│   └── README.md                   # 定义层约定与后续扩展位置
│
├── IDEA Assistant/                 # IDEA Assistant Windows 客户端
│   ├── src/                        # React + TypeScript 用户界面
│   ├── electron/                   # Electron 主进程与安全 IPC 桥
│   ├── public/                     # 桌面应用静态资源
│   ├── dist/                       # 前端构建产物，可重新生成
│   ├── dist-electron/              # Electron 编译产物，可重新生成
│   └── release/                    # Windows 安装包与未打包程序
│
├── server/                         # Python Agent Host 与平台服务层
│   ├── main.py                     # FastAPI API、会话、任务与工作区入口
│   ├── agent_runner.py             # LLM 推理与工具调用循环
│   ├── agents/                     # 当前平台内置的执行实现，不等同于 Agents/ 定义层
│   ├── tools/                      # 文件、命令、网络等受控工具
│   ├── llm/                        # 多模型客户端与提供方配置
│   ├── memory/                     # 本地会话记忆与 SQLite 数据
│   ├── static/                     # 旧版 Web 界面，保留作 API 调试入口
│   └── config.yaml                 # 服务、模型和运行参数
│
├── .trae/agents/                   # TRAE 项目级 Subagent 适配定义
├── docs/                           # 部署、跨设备与接入文档
├── program-idea-proposal/          # 项目提案与技术说明材料
└── README.md                       # 本文件
```

## 层级边界

| 层级 | 目录 | 当前职责 | 后续方向 |
|---|---|---|---|
| 平台界面 | `IDEA Assistant/` | 本地 IDE、Work 模式与工作区文件编辑 | 文件树、编辑器、终端记录、Diff 审阅 |
| 平台服务 | `server/` | API、会话、工具权限和本地执行 | 流式响应、任务队列、权限审批、运行日志 |
| Agent 定义 | `Agents/` | 四个 Agent 的角色卡与系统能力说明 | 可加载配置、版本、工具清单、知识与测试 |
| TRAE 适配 | `.trae/agents/` | 在 TRAE 内调用项目级 Subagent | 与平台定义层同步或自动生成 |

## Agent 定义结构

每个已存在的 Agent 目前有两个基础文件：

```text
Agents/<agent-name>/
├── system.md                        # 职责、能力、约束与输出规范
└── character.md                     # 人格、行为风格与记忆设定
```

后续由平台接管 Agent 开发时，统一在对应目录内追加以下文件或子目录，不改变现有路径：

```text
Agents/<agent-name>/
├── config.yaml                      # 模型、上下文与运行策略
├── tools.yaml                       # 工具白名单与权限声明
├── knowledge/                       # 专属知识与检索配置
├── examples/                        # 对话与任务示例
└── tests/                           # 行为、边界和回归测试
```

## 已授权工作区

平台服务目前只允许 Agent 工具访问以下目录：

- `Program-IDEA`：平台自身代码与配置。
- `shared_rag`：独立的共享知识库服务。
- `ShirohaV1.1`：独立的个人网站项目。

这些目录会在桌面端作为可切换工作区显示。新增目录必须先在服务端白名单中显式登记，不能由 Agent 自行扩大访问范围。

## 开发命令

在 `IDEA Assistant/` 目录中执行：

```powershell
npm run dev:electron
npm run build
npm run package:win
```

当前客户端是离线版，启动时不连接 `server/`、Agent Host 或线上服务。Electron 以 `index.html` 作为 React 应用的最小挂载入口，实际界面和功能代码分别位于 `src/App.tsx`、`src/styles.css` 与 `electron/`。
