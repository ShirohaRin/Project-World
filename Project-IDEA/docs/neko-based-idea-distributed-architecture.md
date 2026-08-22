# 基于 N.E.K.O 范式的 IDEA 分布式架构

> 状态：架构方向稿
>
> 目的：以 N.E.K.O 的三服务、Agent Server、`brain/` 执行层、能力通道与插件宿主作为 IDEA 的底层范式，完整承接现有 IDEA Assistant Harness、桌面端与 Android 客户端能力。
>
> 本文定义目标边界与渐进迁移原则，不表示所有目标组件已经实现，也不要求立即移动既有目录。

## 1. 核心定义

IDEA 是同一个身份在本地与云端的分布式运行时，不是两个各自独立的助手。

```text
                         IDEA Identity
                               |
              +----------------+----------------+
              |                                 |
              v                                 v
   Local IDEA Runtime                    Cloud IDEA Runtime
   - 本地基础智能体                      - 云端 IDEA 执行实例
   - 本地模型优先推理                    - 高算力模型与持续任务
   - 文件、终端、工作区                  - RAG、MCP、服务端插件
   - 本机工具链与 GPU                    - 任务调度、恢复与观测
              |                                 |
              +-------------+-------------------+
                            v
                   Cloud State Plane
       身份、设备、Agent Registry、记忆、任务、审计
```

普通桌面端本地常驻 `IDEA Assistant`；SRH 端本地常驻 `IDEA`。两者共享同一套云端身份、记忆、Agent 注册信息和任务协议，但具有不同的角色与权限范围。

## 2. 目标与边界

### 2.1 云端是统一事实来源

云端保存以下跨设备状态：

- 账户、主体、设备、空间与权限；
- 长期记忆、会话索引、任务摘要、审计记录；
- Agent 定义、提示词、版本、工具策略、记忆范围与模型要求；
- 任务、运行状态、事件、审批、产物和引用；
- 云端 RAG、MCP、插件和模型能力的注册信息。

客户端可缓存必要状态以支持离线体验，但缓存不构成第二份独立人格、长期记忆或授权体系。

### 2.2 本地是受控执行平面

桌面端不只是 UI，而是 Local Device Runtime 宿主。它保留并扩展以下能力：

- 本地工作区选择、浏览、读写、编辑、Diff、备份和回滚；
- 本机工具链、构建、测试、调试、进程取消与 stdout/stderr 输出；
- 本地模型推理、GPU 资源和将来的专用计算能力；
- Electron 安全 IPC、系统安全存储、稳定设备身份和离线队列；
- 用户可见的审批、执行时间线、结果、错误和恢复操作；
- 云端不可用时的工作区浏览、编辑、预览和基础本机运行。

云端不得因为设备已连接而直接获得本地文件、命令、屏幕、键鼠或 GPU 的无限访问权。每次本地动作都要经过设备能力声明、任务授权、本地二次策略校验和必要审批。

### 2.3 推理和执行位置可以交接

默认流程是本地 Runtime 根据云端注册的 Agent 定义装配上下文，并优先使用本地可用模型。发生下列情况时，可以将任务交给云端 Runtime：

- 本地模型、上下文窗口或计算资源不足；
- 任务需要云端 RAG、MCP、服务端插件或特定模型；
- 任务需要异步、长时间、可恢复执行；
- 用户主动选择云端执行；
- 任务执行中需要的能力只在另一端声明可用。

交接不是重新开一个对话，而是提交带版本信息的任务快照：目标、会话引用、已完成步骤、上下文引用、可用能力、审批状态和取消语义。执行位置改变后，任务仍属于同一个 IDEA 身份和同一条任务时间线。

## 3. 目标拓扑

```text
Clients
  |
  +-- IDEA Assistant Desktop ---------- Local Device Runtime
  |       |                              - Assistant 主体
  |       |                              - Workspace / Terminal / Local Model
  |       |                              - Device capabilities / local events
  |       |
  |       +-- IDEA Assistant SRH ------- Local Device Runtime
  |                                      - IDEA 主体与 Owner 控制面
  |
  +-- Android -------------------------- 轻量交互、记忆、会话和任务续接
  |
  v
Cloud Main Server
  - Identity / Device / Space / Session
  - Conversation / Sync / API Gateway
  - Agent Registry / Capability Registry
  - Task Dispatch / Handoff / Event subscription
  |
  +--------------------------+----------------------------+
  |                          |                            |
  v                          v                            v
Memory Server            Agent Server               Knowledge Service
- 长期记忆                - 云端 IDEA Runtime        - RAG / 文档解析
- 检索与修订              - Task / Run 生命周期       - 证据与引用
- 冲突与审计              - Capability channels       - 项目授权回调
- 记忆策略                - Plugin host
                           - Queue / recovery
                           |
                           v
                         brain
                         - planning / routing
                         - context assembly
                         - agent delegation
                         - execution adapters
                         - verification / memory decisions
```

## 4. 服务职责

### 4.1 Main Server

Main Server 是客户端与其他服务的稳定控制面。它不负责把所有 Agent 逻辑、记忆策略和工具执行细节堆入一个入口。

职责包括：

- 身份、设备、空间、会话与同步；
- 普通端与 Owner 端的主体路由；
- Agent Registry 和 Runtime Capability Registry 查询；
- 创建、交接、取消和查询任务；
- 向客户端发布统一事件流；
- 统一认证、授权、审计和速率控制；
- 为 RAG、MCP、插件服务校验用户与项目权限。

当前 IDEA 的 `server/main.py`、`platform_auth.py`、会话 API、同步 API、Owner API 和 MCP 挂载将逐步收敛到这一职责边界。

### 4.2 Memory Server

Memory Server 是本地与云端 IDEA 共同使用的唯一长期记忆服务。客户端不直接访问 SQLite、向量库或 RAG 存储。

职责包括：

- 个人、Owner、共享、项目、会话和任务记忆；
- 结构化读取、写入、检索、修订、删除、失效和导出；
- revision 并发控制、跨设备冲突、同步事件和审计；
- 记忆提炼、去重、证据关联、候选记忆与反思流程；
- 面向本地 Runtime、云端 Runtime、MCP 的统一授权接口。

当前 `PlatformStore.long_term_memories`、记忆 CRUD API、Memory MCP、Owner Memory MCP 是这一服务的早期基础。既有 `MemoryStore` 与 `PlatformStore` 的双轨实现必须最终收敛为单一正式入口。

### 4.3 Agent Server

Agent Server 是任务与能力执行控制面，承载云端 IDEA Runtime，但不等同于某一次模型请求。

职责包括：

- 创建与维护 Task、Run、Step、ToolCall 的生命周期；
- 为云端 Runtime 分配模型、队列、超时和预算；
- 管理能力注册、插件宿主、通道适配器和任务结果；
- 接收或发起 Local Device Runtime 的受控任务交接；
- 统一处理取消、暂停、恢复、重试和等待用户；
- 保存事件、产物、引用、执行日志和可恢复快照。

当前 `agent_runtime/`、`agents/`、`tool_runtime/`、`tools/`、`jobs.py` 和一部分 `main.py` 的运行组装逻辑，会逐步收敛到 Agent Server 与 `brain/`。

### 4.4 brain

`brain/` 是 Agent 执行逻辑层，不承担 HTTP 鉴权，也不直接持有数据库或客户端 UI 状态。

```text
任务输入
  -> Context Assembly
  -> Planning / Routing
  -> Agent / Skill / Model selection
  -> Tool or Agent execution
  -> Verification
  -> Result / Artifact / Citation
  -> Memory decision
```

它应包含：

- IDEA、IDEA Assistant、Researcher、PWA、AgentProducer 等主体与专业 Agent 的运行装配；
- 任务规划、上下文预算、记忆读取和记忆写入决策；
- 子 Agent 委派、结果合并、验证和错误恢复；
- 本地 Runtime、云端工具、MCP、RAG、Browser/Computer 等能力适配器；
- 明确的模型能力与执行位置路由。

`brain/` 不恢复旧式固定 `Orchestrator -> PWA -> Researcher` 流水线。子智能体是通过 Agent Registry 发现的能力，是否调用和如何组合由当前主 Agent 根据任务决定。

## 5. Agent 与能力注册

Agent Registry 是云端版本化配置，而不是散落在本地目录中的角色文本。每个 Agent 至少应声明：

```text
agent_id
role / prompt version
allowed capability classes
memory scopes
model requirements
context and output budget
delegation policy
runtime requirements
owner/public visibility
release and rollback version
```

本地 Runtime 在启动、登录或缓存失效时拉取其可见 Agent 定义。普通端获得 `IDEA Assistant` 和允许的子 Agent；SRH 端获得 `IDEA` 与 Owner 范围能力。提示词、版本、工具策略和记忆范围由云端统一定义，本地只保留经过签名或版本校验的缓存。

Runtime Capability Registry 描述具体设备或云端实例能做什么：

```text
runtime_id / device_id
runtime_type: desktop | srh-desktop | cloud | android
models and context windows
workspace and toolchain availability
GPU / browser / computer / plugin capabilities
sandbox strength
network state
approval and grant requirements
```

任务路由基于 Agent 需求与 Runtime 能力匹配，而不是由客户端猜测云端是否可执行。

## 6. 统一任务协议

### 6.1 核心对象

```text
Conversation
  -> Task
      -> AgentRun
          -> RunStep
              -> ToolCall / AgentCall
                  -> Event / Artifact / Citation / Approval
```

建议状态：

```text
Task: created / queued / running / waiting_user / completed / failed / cancelled
Run: created / planning / running / waiting_approval / paused / handoff_pending /
     handed_off / recovering / completed / failed / cancelled
Step: pending / running / waiting_approval / completed / failed / skipped
ToolCall: proposed / approved / running / succeeded / failed / rejected / timed_out
```

### 6.2 本地到云端交接

```text
Local Runtime
  -> 创建 handoff 请求
  -> 上传或引用 Context Snapshot
  -> Cloud Agent Server 校验 Agent、权限、版本和预算
  -> Cloud Runtime 接管 Run
  -> 事件与结果写入同一 Run 时间线
  -> 本地订阅并显示执行过程
```

快照应引用而非盲目复制大文件或全部历史，至少含有：会话版本、用户目标、已完成步骤、已批准能力、记忆/知识引用、临时文件上下文摘要、模型与 Agent 版本、取消与恢复信息。

### 6.3 云端到本地交接

云端任务若需要本地文件、构建、GPU、浏览器或其他设备能力，必须创建带目标 Runtime 的步骤，而不是直接执行远程命令：

```text
Cloud Agent Server
  -> 选择已登记的目标 Device Runtime
  -> 生成最小权限的 action request
  -> 本地 Runtime 二次策略校验与用户审批
  -> 本地执行并回传事件、日志、产物引用与状态
  -> 云端继续同一 Run
```

本地 Runtime 可以拒绝、超时、离线或撤销授权；这些都是任务状态的一部分，不能被伪装成成功执行。

## 7. 现有 Harness 的完整承接

现有 Harness 不被替换，而是成为目标架构的初始实现。

| 现有模块 | 当前能力 | 目标归属 |
| --- | --- | --- |
| `server/main.py` | API、聊天入口、会话、MCP 挂载、路由 | Main Server gateway 与早期组装层 |
| `server/platform_auth.py` | 账户、设备、空间、记忆、同步、审批、审计 | Main Server + Memory Server 的持久化基础 |
| `server/agent_runtime/runner.py` | 模型工具循环、流式事件雏形 | `brain` 的 executor / Agent Server 的 Run 执行器 |
| `server/model/client.py` | 模型调用与上下文预算 | Model Gateway / Provider Router |
| `server/tool_runtime/` | 工具策略、审批、沙箱、审计、变更审查 | Agent Server capability policy 与 Local Runtime 策略基础 |
| `server/agents/` | IDEA、Assistant、Researcher 等能力 | `brain/agents` 与 Agent Registry 对应实现 |
| `server/mcp/` | Memory、Owner Agent MCP | Agent Server / Memory Server 的 MCP adapters |
| `shared_rag/` | 专业知识检索与管理 MCP | Knowledge Service 的独立能力通道 |
| `jobs.py` | Owner 定时工具作业 | Agent Server 的后台任务早期基础 |

必须保留并继续发展的既有 Harness 特性：

- `ExecutionContext` 与请求身份、空间、设备绑定；
- `ToolPolicy`、审批、长期 capability grant、审计与文件变更审查；
- 模型工具调用循环和 1M 上下文预算控制；
- 会话、任务、记忆和同步事件的服务端持久化；
- Owner 与普通客户端的能力隔离；
- MCP 经过统一身份、设备、空间和审计链路访问。

目标架构补齐而非绕开以下缺口：统一 Run/Event/Artifact/Citation 数据模型，流式事件贯通，长任务 Worker，任务恢复，云端与本地 Runtime 的交接协议，以及统一 Agent Registry。

## 8. 客户端页面与本地 Runtime 的承接

### 8.1 IDEA Assistant Desktop

现有 Electron + React 页面必须完整保留为主力工作台，而不是退化为纯聊天壳。

| 现有页面或能力 | 在目标架构中的定位 |
| --- | --- |
| 登录与连接状态 | Cloud Main Server 身份、设备注册与 Runtime 健康状态 |
| Work 模式对话、会话、任务 | Conversation / Task / Run 的主交互界面 |
| IDE 模式、工作区、文件树、编辑器 | Local Device Runtime 的工作区能力界面 |
| Markdown 预览、多文件编辑 | 本地创作与代码工作台能力 |
| 运行、调试、输出面板 | Local Runtime 的执行、日志与取消界面 |
| 临时文件上下文 | Context Snapshot 的可见、可控输入 |
| 插件市场 | 本地插件与能力启用状态，不绕过云端注册和授权 |
| Owner 审批、授权、审计、回滚 | SRH Runtime 的安全控制面 |
| Owner 记忆维护 | Memory Server 的受权限保护管理界面 |

页面需要逐步增加，但不推翻现有导航：

- 当前 Run 状态、执行位置和模型来源；
- 计划步骤、工具/Agent 调用、日志、产物和引用时间线；
- 本地或云端执行选择、交接状态、取消、暂停、重试；
- 本地设备能力、心跳、队列和离线同步状态；
- 高风险本地动作的审批与可回看记录。

`IDE` 和 `Work` 模式继续共享工作区、任务和上下文引用。IDE 负责本地项目操作，Work 负责自然语言任务、研究与交付；两者在统一 Run 模型上会合。

### 8.2 SRH 客户端

SRH 端是 Owner 的 Local IDEA Runtime，不是另一套服务。

它在普通桌面 Runtime 能力上增加：

- 常驻 `IDEA` 主体；
- Owner 私域记忆维护；
- 设备批准与撤销、MCP 凭据、审批、长期授权、审计和文件变更确认；
- 可使用 Owner 才可见的 Agent、能力、项目和记忆命名空间；
- 对云端任务调度、插件发布和 Agent Registry 版本的管理入口。

### 8.3 Android

Android 保持轻量交互与跨设备续接定位：会话、任务、记忆、同步、审批通知和云端执行状态。它不承担本地工作区、终端或桌面自动化 Runtime，除非以后单独定义移动端受控能力协议。

## 9. 渐进实施顺序

不直接按目标目录搬迁。先建立协议和数据模型，再逐步替换内部实现边界。

1. **统一对象与事件协议**
   - 引入 `AgentRun`、`RunStep`、`RunEvent`、`Artifact`、`Citation`、`Approval`；
   - 将现有聊天结果、任务、工具日志和审计逐步关联到 Run；
   - 将 `run_stream()` 接到服务 API 与客户端时间线。

2. **建立 Agent Registry 与 Runtime Capability Registry**
   - 整合 `Agents/`、`.trae/agents/`、Python Agent 实现、提示词和工具策略；
   - 定义版本、发布、回滚、可见范围与客户端缓存；
   - 让普通端和 SRH 端按主体获取基础 Agent。

3. **将桌面端注册为 Local Device Runtime**
   - 设备注册、心跳、能力声明、目标设备路由；
   - 引入本地 action request、幂等键、取消、日志/产物回传和离线队列；
   - 将已有安全 IPC、工作区与单文件运行器收敛为本地能力适配器。

4. **形成明确的 Memory Server 契约**
   - 收敛双轨记忆入口；
   - 保持现有 revision、审计、空间与 namespace 权限；
   - 让本地和云端 Runtime 通过同一服务访问记忆。

5. **拆出 Agent Server 与 brain 边界**
   - 先从 `main.py` 提取任务与 Run 组装，再抽出 Worker、能力通道与插件宿主；
   - 将 `agent_runtime`、`tool_runtime`、Agent 定义迁移到清晰职责下；
   - 保持旧 API 和客户端可用，直到新协议经过端到端验证。

6. **接入扩展通道**
   - 以能力注册形式接入 RAG、MCP、Browser Use、Computer Use、用户插件和将来的专用科研工具；
   - 每个通道使用统一权限、审批、Run、Event、Artifact 和审计模型。

## 10. 不可违反的约束

- 身份、长期记忆、任务状态、授权和审计只存在一套云端事实来源；
- 客户端不保存 RAG 密钥，也不直接访问记忆数据库或 RAG 存储；
- 云端不能绕过 Local Runtime 对本地文件、命令、屏幕、输入设备和 GPU 的二次策略校验；
- 本地和云端不允许同时以同一 Run 执行相互冲突的步骤；
- 每个高风险动作应能追溯至身份、设备、任务、Run、审批和结果；
- 本地离线能力必须显式标注为离线，不能伪造云端同步、记忆写入或 Agent 执行结果；
- 架构迁移不删除既有 IDEA Assistant 页面、SRH 控制面、工作区功能、记忆维护与现有安全边界。

## 11. 结论

N.E.K.O 的价值在于提供了可扩展的系统骨架：Main Server、Memory Server、Agent Server、`brain/`、能力通道和插件宿主。IDEA 将以此作为底层范式，但其运行形态是本地与云端共同构成同一个 IDEA。

现有 IDEA Assistant Harness、桌面工作台、SRH Owner 页面、Android 续接端、跨设备记忆、RAG、MCP、审批与审计都属于这个目标系统的既有资产。后续重构的原则不是减少它们，而是让它们通过统一的 Agent Registry、Runtime Capability、Task Handoff、Run/Event/Artifact/Approval 协议进入同一套可恢复、可观察、可扩展的分布式智能体架构。
