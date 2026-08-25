# 混合架构第一阶段：Owner 云端 IDEA 接入与主力机执行节点

## 目标

让笔记本与主力台式机以独立凭据接入同一个 Owner 专属云端 IDEA 身份，并为后续由主力机执行本地任务建立受控协议边界。

本阶段不把所有工作移至云端。云端承担身份、会话、记忆、任务和调度；主力机保留本地工作区、构建、测试、GPU 与重计算能力。

## 本阶段范围

1. 新建标准 Streamable HTTP MCP 端点：`/mcp/idea/mcp`。
2. 建立 Owner 专属 Agent MCP 凭据类型与 capability scope，至少区分 `memory_readonly` 与 `idea_agent_owner`。
3. 首版 MCP 仅暴露高层能力：`idea_chat`、`idea_session_get`、`idea_task_status`。
4. `idea_chat` 必须经服务端 Owner 路由，Owner Agent 凭据只能获得 `agent_id=idea`。
5. 两台 TRAE 使用不同 Agent MCP 凭据，通过同一 `conversation_id` 续接同一云端 IDEA 会话。
6. 设计 Device Agent 的注册、心跳、能力声明、任务领取、结果回传、取消与审计协议。

## 明确不在本阶段实现

- 不向 TRAE 直接暴露 `run_command`、文件读写、删除、底层 ToolRegistry 或原始 Agent 调度工具。
- 不让现有 Memory MCP 凭据访问 Agent MCP。
- 不让 Agent MCP 凭据获得 Device Agent 的任务执行权限。
- 不实现远程控制主力机文件、命令、GPU、屏幕、键鼠或客户端；这些能力须在 Device Agent 后续实现和验收后才可启用。
- 不把 IDEA Assistant 普通版变为 Owner 私域入口。

## 验收标准

1. `memory_readonly` 凭据请求 `/mcp/idea/mcp` 返回 `401`。
2. 已批准 Owner Device 才能签发 `idea_agent_owner` 凭据。
3. 两台 TRAE 的不同 Agent MCP 凭据均可调用 `idea_chat`。
4. `idea_chat` 的响应明确返回 `agent_id=idea` 与可续接的 `conversation_id`。
5. 在笔记本创建的会话可在主力机以相同 `conversation_id` 续接。
6. 撤销任一 Agent MCP 凭据后，该凭据立即无法访问端点。
7. `tools/list` 不包含底层文件、命令或设备控制工具。
8. 自动化测试覆盖凭据隔离、Owner 路由、跨设备会话续接和撤销失效。
9. Device Agent 协议文档明确每个动作的设备目标、能力要求、请求 ID、审批状态、日志和取消语义。
