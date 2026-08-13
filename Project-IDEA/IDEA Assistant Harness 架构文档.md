# IDEA Assistant Harness 架构文档

> 文档状态：讨论稿
>
> 依据：当前 `D:\Project World\Project-IDEA` 源码盘点
>
> 目标：完整说明 IDEA Assistant Harness 的运行架构、代码归属、数据流、权限边界和待完善部分。本稿只描述现状与建议边界，不代表所有规划能力已经实现。

---

## 1. Harness 的定义与目标

IDEA Assistant Harness 是 IDEA 的执行骨架。它不等同于某一个 Agent，也不等同于 LLM 客户端，而是负责把用户请求转化为可观察、可控制、可持久化的工作过程：

```text
用户请求
  → 身份与空间解析
  → 上下文装配
  → Agent / 模型选择
  → 模型推理
  → 工具调用或下级 Agent 调度
  → 工具结果回灌
  → 继续推理或结束
  → 持久化会话、任务、记忆和审计
  → 客户端展示结果
```

Harness 的核心目标有五个：

1. **执行**：让模型能够调用文件、命令、网络和其他 Agent。
2. **控制**：限制工具范围、工作区、调用轮次和身份权限。
3. **观察**：记录模型、工具、任务和同步事件，让用户知道系统正在做什么。
4. **恢复**：工具失败、模型异常或任务中断后，能够重试、停止或继续。
5. **交付**：把最终答案、文件变更、任务状态和验证结果交给用户。

当前项目已经具备第 1 项的大部分基础、第 2 项的部分基础、第 3 项的部分基础；第 4、5 项仍需要系统化完善。

---

## 2. 当前总体架构

当前代码库包含服务端 Agent 执行能力，以及一套客户端本地执行机制。PWA、Researcher、AgentProducer 等子智能体作为可被 IDEA 本体或 IDEA-Assistant 调用的能力单元保留，但不构成固定的上下级工作流；是否调用、调用哪个、调用几次以及如何组合，应由当前 Agent 根据任务上下文自主决定。

```text
                         ┌────────────────────────────┐
                         │ IDEA Assistant Electron     │
                         │ React UI + Electron IPC     │
                         └──────────────┬─────────────┘
                                        │ HTTP / IPC
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ FastAPI IDEA 服务                                                │
│                                                                 │
│ 认证 / 空间 / Owner 路由                                         │
│        │                                                        │
│        ▼                                                        │
│ /api/assistant/chat                                             │
│        │                                                        │
│        ▼                                                        │
│ AgentRunner 主执行内核                                           │
│   ┌──────────────┬──────────────┬──────────────────────────┐    │
│   │ LLMClient    │ ToolRegistry │ Agent 能力注册与调用       │    │
│   │ 模型调用      │ 工具执行      │ IDEA / Assistant 自主选择 │    │
│   └──────────────┴──────────────┴──────────────────────────┘    │
│        │                                                        │
│        ▼                                                        │
│ PlatformStore：会话 / 任务 / 记忆 / 同步 / 审计 / 凭据            │
└─────────────────────────────────────────────────────────────────┘

子智能体能力：
IDEA / IDEA-Assistant → 按任务需要自主调用 → PWA / Researcher / AgentProducer

客户端本地链：
插件注册表 → Electron 执行进程 → stdout/stderr → UI
```

### 2.2 子智能体的定位

PWA、Researcher、AgentProducer 等子智能体继续保留。它们是可被 IDEA 本体或 IDEA-Assistant 智能体调用的专业能力单元，而不是一条预先写死的调度流水线。

```text
IDEA / IDEA-Assistant
  → 根据当前请求、上下文和能力描述自主判断
  → 零次、一次或多次调用某个子智能体
  → 将结果纳入当前任务上下文
  → 继续自行处理、继续调用其他能力，或直接交付
```

这里的箭头表示**能力调用关系**，不表示固定执行顺序、必经步骤或永久的父子工作流。子智能体可以被保留、扩展和替换；其存在是为了扩大 IDEA 与 IDEA-Assistant 的可用能力范围。

### 2.3 旧式 Orchestrator 的处理原则

`server/agents/orchestrator.py` 代表早期的显式意图分类和固定调度思路。根据当前设计初衷，它不应继续作为一条独立的 `Orchestrator → PWA / Researcher / AgentProducer` 工作流保留，也不应继续发展自己的调度、权限和任务状态体系。

后续处理原则是：

- 保留 PWA、Researcher、AgentProducer 等子智能体能力；
- 删除旧式固定链条的架构定位；
- 不再把关键词分类和预定义流程作为子智能体调用的前提；
- 子智能体调用统一回到 IDEA / IDEA-Assistant 的 Harness 判断；
- `Orchestrator` 若仍有可复用代码，只能迁移为通用能力描述、Agent 注册或兼容适配，不保留为独立调度中心；
- 在完成引用确认和能力迁移后，删除其独立运行入口。

当前主聊天流程仍然是：

```text
App.tsx
  → preload.ts
  → electron/main.ts
  → POST /api/assistant/chat
  → main.py 认证、路由、上下文装配
  → AgentRunner.run()
  → LLMClient.chat()
  → 工具或可用 Agent 能力
  → PlatformStore 持久化
  → JSON 返回客户端
```

---

## 3. 架构分层与负责文件

## 3.1 客户端呈现层

### 文件

- `IDEA Assistant Code/src/App.tsx`
- `IDEA Assistant Code/src/styles.css`
- `IDEA Assistant Code/src/CodeEditor.tsx`
- `IDEA Assistant Code/src/plugins/registry.ts`

### 作用

负责把 Harness 的运行状态呈现给用户，包括：

- 登录状态和服务状态；
- 会话、任务和工作区；
- 聊天消息；
- 文件编辑器；
- 本地运行和调试输出；
- Owner 记忆、设备和凭据管理；
- 插件市场和插件启用状态。

### 当前实现

`App.tsx` 已经能发送在线聊天、读取会话、读取任务、删除会话和任务、读取 Owner 记忆，并接收本地执行输出。但云端 Agent 的工具调用过程尚未以完整的实时事件面板展示给用户，当前聊天主要等待完整 JSON 返回。

### Harness 需要它承担的职责

客户端不应重新实现 Agent 推理或权限判断，而应负责：

1. 展示当前 Run 状态；
2. 展示计划、工具调用、工具结果和最终交付物；
3. 提供暂停、取消、确认和重试操作；
4. 展示错误和恢复建议；
5. 维护用户可理解的任务时间线。

---

## 3.2 Electron 安全桥与服务代理层

### 文件

- `IDEA Assistant Code/electron/main.ts`
- `IDEA Assistant Code/electron/preload.ts`

### 作用

Electron 主进程是客户端与云端服务之间的受控代理，也是本地文件和进程执行的边界。

主要职责：

- 保存和恢复服务配置；
- 使用 Electron `safeStorage` 保存 Access Token 和 Refresh Token；
- 登录、刷新 Token、注销；
- 为请求添加 Authorization、设备 ID、空间 ID；
- 处理 401 后的 Token 刷新；
- 代理会话、任务、记忆和聊天请求；
- 访问本地工作区；
- 启动、停止本地执行进程；
- 转发 stdout、stderr 和终止事件；
- 处理客户端自动更新。

### 当前实现

`preload.ts` 使用 `contextBridge.exposeInMainWorld('ideaDesktop', ...)` 暴露受限 API。React UI 不直接访问 Node.js、文件系统或网络凭据。

### Harness 边界

- 云端 Agent 工具应由服务端 Harness 执行；
- 本地代码运行是客户端 Harness 的独立执行子系统；
- 客户端不应绕过 Electron IPC 直接读写云端记忆或直接保存明文 Token；
- 云端任务取消、审批和事件订阅需要通过明确的 IPC API 暴露。

---

## 3.3 API 入口与请求上下文层

### 文件

- `server/main.py`
- `server/platform_auth.py`

### 作用

这一层负责把一次网络请求转换成可信的执行上下文：

```text
HTTP 请求
  → Bearer Token / Access Token 解析
  → 账户、主体、设备解析
  → 空间解析
  → Owner / 普通用户路由
  → 权限上下文
  → Harness 执行
```

关键职责：

- 认证和会话恢复；
- 账户、主体、设备和空间识别；
- Owner 路由与普通用户路由区分；
- 读取项目成员权限；
- 处理会话、任务、记忆、同步和审计 API；
- 挂载 MCP 记忆和 Owner Agent 服务。

### 关键入口

- `POST /api/assistant/chat`：主聊天入口；
- `GET /api/conversations`、`GET /api/conversations/{id}`：会话读取；
- `DELETE /api/conversations/{id}`：会话软删除；
- `GET /api/tasks`、`POST /api/tasks`、`DELETE /api/tasks/{id}`：任务管理；
- `GET/POST/PUT/DELETE /api/memories...`：长期记忆；
- `GET /api/sync/events`：跨设备同步；
- `GET /api/agents`：Agent 和工具发现；
- `GET /api/workspaces`：允许工作区查询。

### 当前缺口

请求上下文已经能控制路由和平台资源，但 `AgentRunner` 执行工具时没有统一接收完整的 `RequestContext`。因此，身份权限和工具权限之间还没有完全闭合。

---

### 3.4 Agent 能力与角色层

### 文件

- `server/main.py`
- `server/agent_runner.py`
- `server/agents/pwa.py`
- `server/agents/researcher.py`
- `server/agents/agent_producer.py`
- `Agents/` 下的角色定义文件
- `.trae/agents/` 下的 TRAE 适配定义

### 作用

负责描述和提供可被 IDEA 或 IDEA-Assistant 调用的 Agent 能力。该层不是固定流程编排器，不预设 PWA、Researcher、AgentProducer 的调用顺序，也不要求每个请求都经过某个子智能体。

当前 Agent 能力包括：

| Agent ID | 能力定位 | 可被谁调用 |
|---|---|---|
| `idea` | Owner IDEA 主体能力 | Owner 客户端和授权入口 |
| `idea_assistant` | 普通用户 Assistant 主体能力 | 普通客户端 |
| `pwa` | PWA 相关专业能力 | IDEA / IDEA-Assistant 按需调用 |
| `researcher` | 研究与资料分析能力 | IDEA / IDEA-Assistant 按需调用 |
| `agent_producer` | Agent 定义生产能力 | IDEA / IDEA-Assistant 按需调用 |

### 调用原则

- 子智能体以能力描述、工具集合和角色提示词被注册；
- IDEA 或 IDEA-Assistant 根据任务上下文自行选择是否调用；
- 子智能体可以不被调用，也可以被多次调用；
- 子智能体返回结果后，由调用方决定采纳、追问、组合或忽略；
- 不把子智能体建模成固定的 `Orchestrator → PWA → Researcher` 链条；
- 不为子智能体额外建立一套与 Harness 分离的权限和任务体系。

### 当前主路由

`main.py` 中的 `_routed_agent()` 根据账户和 Owner 状态，将请求路由到 `idea` 或 `idea_assistant`。子智能体不再作为固定链路的后续节点，而应作为主 Agent 可发现、可调用的能力集合。

### 当前问题

Agent 定义目录、Python Agent 实现和 TRAE Agent 配置是三套独立内容，没有统一加载、版本、发布、回滚或同步机制。后续应优先建立 Agent 能力注册与发现机制，而不是恢复旧式固定调度器。

---

## 3.5 Harness 核心：AgentRunner

### 文件

- `server/agent_runner.py`

### 作用

`AgentRunner` 是当前 Harness 的核心执行器，负责实现：

```text
LLM 推理 → 工具调用 → 工具结果回灌 → 再次推理
```

### 关键符号

- `MAX_TOOL_ROUNDS`：最大工具调用轮次；
- `AgentRunner.run()`：非流式执行；
- `AgentRunner.run_stream()`：内部流式执行；
- `_build_messages()`：上下文消息构建；
- `_execute_tool()`：工具查找、调用和异常包装。

### 非流式执行流程

```text
1. 接收 user_message 和 history
2. 加载系统提示词和工具 Schema
3. 调用 LLMClient.chat()
4. 如果返回文本，结束
5. 如果返回 tool_calls，逐个执行工具
6. 把 assistant tool_call 与 tool result 写回消息列表
7. 再次调用 LLM
8. 达到完成条件或最大轮次后返回
```

返回内容包括：

- `reply`；
- `tool_calls_log`；
- `iterations`；
- `usage`。

### 流式执行流程

`run_stream()` 已定义以下事件：

- `text`：文本增量；
- `tool_call`：模型请求调用工具；
- `tool_result`：工具结果；
- `done`：执行完成。

但当前普通 HTTP 聊天入口和 Electron 客户端尚未完整接通这条流式链路。

### 当前定位

它是“最小可运行 Agent Loop”，还不是完整 Harness。缺少：

- 独立 Run 对象；
- 计划和步骤状态；
- 工具审批节点；
- 取消和暂停；
- 预算和超时策略；
- 统一事件持久化；
- 产物和引用模型；
- 可恢复执行。

---

## 3.6 工具注册与工具执行层

### 文件

- `server/tools/registry.py`

### 作用

`ToolRegistry` 把 Python 函数包装成 LLM 可以发现和调用的工具。

### 当前工具

#### 文件工具

- `read_file`
- `write_file`
- `edit_file`
- `list_dir`
- `search_content`
- `delete_file`

#### 系统工具

- `run_command`

#### 网络工具

- `web_search`
- `web_fetch`

### 路径控制

`_resolve_path()` 将相对路径解析到工作区，并确认最终路径位于 `allowed_dirs` 之一。服务端启动时通过 `existing_workspace_paths()` 初始化允许目录。

### 当前已有控制

- 工作区目录限制；
- 文件路径解析；
- 命令工作目录限制；
- 命令超时；
- 输出截断；
- 未知工具错误包装。

### 当前缺口

- 工具没有统一的风险等级；
- 没有工具能力声明；
- 没有按用户、空间、设备和任务动态授权；
- `run_command` 仍然是高能力工具；
- 写入、删除和外部网络访问没有统一确认协议；
- 工具注册表不是完整插件协议。

---

## 3.7 LLM 与模型适配层

### 文件

- `server/llm/client.py`

### 作用

负责向不同模型服务商发送标准化请求，并把响应转换为 Harness 能消费的结构。

当前支持或配置了：

- Doubao；
- GLM；
- OpenAI；
- Custom OpenAI-compatible endpoint。

关键方法：

- `LLMClient.chat()`：非流式调用；
- `LLMClient.chat_stream()`：流式调用；
- `get_model_id()`：模型标识；
- `_fallback_response()`：缺少配置或模型异常时的回退。

### Harness 中的作用

LLM 层只负责推理，不负责：

- 文件访问；
- 账户权限；
- 工具执行；
- 会话持久化；
- 用户确认。

这些职责由上层 Harness 和下层工具层承担。

### 当前缺口

Provider 还没有被抽象成统一的 `ModelProvider` 接口，模型能力、上下文窗口、工具调用能力、健康状态和成本预算尚未统一建模。

---

## 3.8 Agent 能力调用层

### 文件

- `server/main.py`
- `server/agent_runner.py`
- `server/agents/pwa.py`
- `server/agents/researcher.py`
- `server/agents/agent_producer.py`

### 作用

让 IDEA 或 IDEA-Assistant 在需要时调用专业子智能体。这里保留的是**可调用能力**，不是一个预先规定好的调度工作流。

```text
IDEA / IDEA-Assistant
  → 根据请求和上下文判断是否需要专业能力
  → 选择一个或多个可用子智能体
  → 接收结果并继续当前任务
```

调用可以是：

- 不调用任何子智能体；
- 调用一个子智能体一次；
- 在不同阶段调用同一个子智能体；
- 按当前任务需要组合多个子智能体；
- 调用后不采纳结果，改用其他路径处理。

因此，`PWA`、`Researcher`、`AgentProducer` 应作为独立能力单元注册和发现，不应被描述成固定的“下级 Agent 链”，也不要求形成永久父子关系。

### 当前实现状态

当前代码中仍存在 `dispatch_to_agent` 和部分 Agent 调用逻辑，但它们只能被理解为当前实现中的一种调用适配方式，不能固化为 Harness 的架构约束。后续应将其收敛为通用 Agent 能力调用接口，由 IDEA / IDEA-Assistant 自主决定是否调用。

### 当前缺口

- 没有统一的 Agent 能力描述和发现协议；
- 没有统一的调用输入、输出和错误结构；
- 没有调用上下文与当前 Run 的统一关联；
- 没有对多个子智能体结果的通用合并机制；
- AgentProducer 生成的定义不会自动注册为运行时能力。

---

## 3.9 上下文、会话、任务与记忆层

### 文件

- `server/platform_auth.py`
- `server/memory/store.py`
- `server/main.py`
- `server/owner_agent_mcp.py`
- `server/memory_mcp.py`

### 会话

`PlatformStore` 管理：

- `conversations`；
- `conversation_messages`；
- 会话列表和详情；
- 会话删除；
- 同步事件；
- 审计记录。

### 任务

`tasks` 表和 API 已实现，可以创建、查询、删除和同步。但当前没有独立后台 Worker、任务恢复、取消和长任务执行生命周期。

### 长期记忆

当前存在两套记忆：

1. `server/memory/store.py` 的基础 `MemoryStore`；
2. `platform_auth.py` 中按账户、空间、namespace 和 scope 管理的 `long_term_memories`。

长期记忆支持个人、共享、项目和 Owner 范围，并具有 revision 并发控制、审计和同步事件。

### MCP 记忆层

- `server/memory_mcp.py`：提供 `memory_search`、`memory_get`；
- `server/owner_agent_mcp.py`：提供 `idea_chat`、`idea_memory_save`、`idea_session_get`、`idea_task_status`。

Harness 应该通过稳定的服务接口使用记忆，不应把记忆数据库直接暴露给客户端。

---

## 3.10 持久化、同步与审计层

### 文件

- `server/platform_auth.py`
- `server/main.py`

### 数据表

- `accounts`：账户；
- `principals`：用户主体和角色；
- `access_tokens`、`account_sessions`：认证会话；
- `spaces`、`space_members`：空间和成员；
- `conversations`、`conversation_messages`：会话；
- `tasks`：任务；
- `sync_events`：跨设备同步；
- `long_term_memories`：长期记忆；
- `audit_events`：审计；
- `owner_devices`：Owner 设备；
- `mcp_device_credentials`：自动化凭据；
- `projects`、`project_members`：项目权限。

### 当前作用

这一层保证客户端更换设备、重启或重新登录后，仍能恢复会话、任务、记忆和权限范围。

### Harness 需要补充的数据模型

当前缺少独立的：

- `agent_runs`：一次完整执行；
- `run_steps`：计划步骤；
- `tool_calls`：工具调用；
- `approvals`：风险操作审批；
- `artifacts`：生成文件、报告和补丁；
- `citations`：RAG、网页和文件引用；
- `run_events`：统一实时事件。

---

## 3.11 MCP 接入层

### 文件

- `server/main.py`
- `server/memory_mcp.py`
- `server/owner_agent_mcp.py`
- `shared_rag/trae/rag_mcp_owner_admin.py`

### 作用

MCP 层为外部 TRAE、自动化客户端和其他 Agent 提供稳定能力入口。

当前包括：

- 云端记忆搜索和读取；
- Owner IDEA 对话；
- Owner 记忆保存；
- Owner 会话和任务状态；
- 外部 RAG 管理工具。

MCP 的职责是能力适配和认证，不应把它变成另一套独立的 Harness。外部 MCP 请求最终仍应进入统一的账户、空间、项目和 Owner 权限链。

---

## 3.12 客户端本地执行 Harness

### 文件

- `IDEA Assistant Code/src/plugins/registry.ts`
- `IDEA Assistant Code/electron/main.ts`
- `IDEA Assistant Code/electron/preload.ts`
- `IDEA Assistant Code/src/App.tsx`

### 作用

这是与云端 Agent Harness 并行的本地执行子系统，负责运行和调试用户工作区中的代码。

支持：

- JavaScript / Node；
- Python；
- Go；
- TypeScript；
- C / C++；
- Rust；
- Java。

运行输出通过 `execution:output` 事件回传客户端，包含：

- `sessionId`；
- `stdout`；
- `stderr`；
- `system`；
- `terminal`。

### 与云端 Harness 的关系

```text
云端 Harness：模型推理、工具调用、任务和记忆
本地 Harness：代码运行、调试、进程控制和本地输出
```

两者当前没有统一的 Run ID、权限模型、产物模型或事件协议。后续如果要让 IDEA 自动“修改文件后运行测试”，需要建立两者之间的受控桥接。

---

## 4. 一次完整请求的当前数据流

以用户要求“读取项目文件并总结”为例：

```text
1. 用户在 App.tsx 输入请求
2. App.tsx 调用 window.ideaDesktop.sendChat()
3. preload.ts 转发 service:chat
4. electron/main.ts 发送 POST /api/assistant/chat
5. main.py 解析 Token、设备和空间
6. main.py 选择 idea 或 idea_assistant
7. main.py 读取会话历史和允许范围内的上下文
8. AgentRunner 构造消息和工具 Schema
9. LLMClient.chat() 返回 read_file tool_call
10. AgentRunner 调用 ToolRegistry.read_file()
11. 工具结果回灌 AgentRunner
12. LLM 再次推理并生成总结
13. main.py 写入用户消息和 assistant 消息
14. PlatformStore 写入审计与同步事件
15. 服务端返回 reply、conversation_id、tool_calls_log
16. App.tsx 更新聊天界面和会话列表
```

当前用户看不到第 9 至 12 步的实时过程，只能在最终响应中看到部分工具调用日志。这是 Harness 体验需要优先改善的地方。

---

## 5. 当前架构的主要问题

### 5.1 执行状态没有独立实体

一次请求、一次 Agent 循环、一个任务和一个会话目前没有清晰的生命周期模型。结果主要以聊天响应和任务表承载，无法可靠恢复长任务。

### 5.2 流式能力没有贯通

`LLMClient.chat_stream()` 和 `AgentRunner.run_stream()` 已存在，但普通聊天 HTTP 接口和客户端尚未展示统一的文本、工具调用和工具结果事件。

### 5.3 权限没有贯穿工具执行

请求入口有账户和空间权限，但工具层主要依靠静态工作区白名单；高风险动作没有统一的确认和审批层。

### 5.4 Agent 能力调用边界

当前代码中仍保留旧式 `Orchestrator` 和 `dispatch_to_agent` 的实现痕迹，但它们不应被当作两套并行的 Harness，也不应形成固定的子智能体调度链。真正需要保留的是 PWA、Researcher、AgentProducer 等专业能力，以及 IDEA / IDEA-Assistant 按任务需要调用这些能力的可能性。

后续应统一 Agent 能力描述、发现和调用接口，逐步移除固定流程与旧调度中心的架构定位。

### 5.5 云端和本地执行链没有统一协议

代码修改、运行测试、生成报告这类复合任务，需要云端 Agent 调用本地执行能力；当前二者之间没有统一的 Run、Event、Artifact 和 Approval 协议。

### 5.6 记忆实现存在双轨

`MemoryStore` 和 `PlatformStore.long_term_memories` 并存，后续需要明确哪一个是正式长期记忆入口，避免写入一处、检索另一处。

---

## 6. 建议的目标 Harness 架构

后续完善可以把 Harness 明确拆成九个稳定层：

```text
1. Request Gateway
   请求、认证、空间与设备上下文

2. Intent / Route Layer
   Agent、模型档位、任务类型和工作区选择

3. Run Orchestrator
   创建 Run、计划步骤、循环、暂停、取消和恢复

4. Context Builder
   会话历史、长期记忆、RAG、文件引用、任务状态

5. Model Gateway
   Provider、模型能力、Token 预算、超时和重试

6. Tool / Agent Runtime
   工具调用、下级 Agent、超时、隔离和结果校验

7. Approval Layer
   写入、删除、命令、网络和外部发布等风险确认

8. State / Event / Artifact Layer
   Run、Step、ToolCall、Event、Artifact、Citation、Audit

9. Client Presentation Layer
   计划卡片、执行时间线、确认面板、结果和错误恢复
```

### 目标执行模型

```text
Request
  → Run 创建
  → Plan 生成
  → Step 逐步执行
  → Tool / Agent 调用
  → Event 持久化与推送
  → Approval（必要时暂停）
  → Result / Artifact / Citation
  → Run 完成、失败、取消或等待
```

### 建议的状态枚举

```text
Run:
created / planning / running / waiting_approval / paused /
cancelling / completed / failed / cancelled

Step:
pending / running / waiting_approval / completed / failed / skipped

ToolCall:
proposed / approved / running / succeeded / failed / rejected / timed_out
```

---

## 7. 当前文件与目标层的对应关系

| 目标 Harness 层 | 当前主要文件 | 当前成熟度 |
|---|---|---|
| Request Gateway | `server/main.py`, `server/platform_auth.py` | 较完整 |
| Intent / Route | `server/main.py`, Agent 能力注册与发现 | 部分实现，调用关系不固定 |
| Run Orchestrator | `server/agent_runner.py` | 原型 |
| Context Builder | `server/main.py`, `platform_auth.py`, `owner_agent_mcp.py`, `memory/store.py` | 部分实现 |
| Model Gateway | `server/llm/client.py` | 可用但抽象不足 |
| Tool Runtime | `server/tools/registry.py` | 可用但审批不足 |
| Agent Runtime | `server/agent_runner.py`, `server/agents/*.py` | 可用但缺生命周期 |
| Approval Layer | 当前无统一实现 | 缺失 |
| State / Event | `platform_auth.py` 的会话、任务、同步、审计表 | 部分实现 |
| Artifact / Citation | 当前无统一模型 | 缺失 |
| Client Presentation | `App.tsx`, `main.ts`, `preload.ts` | 基础可用 |
| Local Execution | `main.ts`, `preload.ts`, `plugins/registry.ts` | 可用但独立 |
| MCP Adapter | `main.py`, `memory_mcp.py`, `owner_agent_mcp.py`, `rag_mcp_owner_admin.py` | 可用 |

---

## 8. 讨论时需要先确定的架构决策

1. `AgentRunner` 如何承载 IDEA / IDEA-Assistant 对 Agent 能力的自主调用，以及 Agent 能力注册与发现接口如何设计？
2. 长任务是否与普通聊天分离，是否引入独立 Run 和后台 Worker？
3. 哪些工具必须确认：写文件、删除文件、运行命令、网页访问、RAG 写入、外部发布？
4. 工具调用日志是否保存参数原文，哪些参数必须脱敏？
5. 云端 Harness 是否允许调用客户端本地执行器？如果允许，如何配对设备、授权和撤销？
6. `MemoryStore` 与 `PlatformStore` 哪一个是正式长期记忆系统？
7. RAG、记忆和网页结果是否统一为 Citation / Evidence？
8. 用户需要看到多详细的计划和工具过程？是否允许折叠、暂停和重试？
9. 任务删除、会话删除和审计记录之间采用什么保留政策？
10. AgentProducer 生成的 Agent 是否允许直接发布，还是必须经过 Owner 审核？

---

## 9. 结论

当前 IDEA Assistant 已经有一个可工作的 Harness 雏形：

```text
FastAPI + PlatformStore
  + AgentRunner
  + LLMClient
  + ToolRegistry
  + 下级 Agent 调度
  + Electron 客户端
```

但它目前更接近“带工具调用的聊天服务”，还不是完整的任务执行框架。下一阶段的重点不应继续堆叠更多工具，而应先建立：

1. Run / Step 生命周期；
2. 统一事件流；
3. 高风险操作审批；
4. 工具与 Agent 权限；
5. 失败恢复和取消；
6. Artifact / Citation 交付模型；
7. 云端 Agent 与本地执行器之间的受控桥接。

这些基础确定后，再增加更多 Agent、RAG 能力和客户端功能，系统会更稳定，也更容易解释和维护。
