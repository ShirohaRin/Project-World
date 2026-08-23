# 主力机接入 Owner IDEA MCP

## 前置条件

- 主力机已克隆 Project World 私有仓库，并已在 TRAE 中重新加载过 MCP 服务。
- 本机和主力机分别使用独立的 Owner IDEA MCP 凭据；不要复用只读记忆 MCP 凭据。
- 两类 MCP 可以同时保留：`IDEA-Controlled-Memory` 用于检索显式长期记忆，`IDEA-Owner-Agent` 用于向云端 IDEA 发起对话和续接会话。

## 主力机配置

将为主力机签发的凭据中的 `token` 填入以下配置的 `Authorization` 字段，并在 TRAE 中添加为单独的 MCP 服务：

```json
{
  "mcpServers": {
    "IDEA-Owner-Agent": {
      "url": "https://shiroha-rin.world/mcp/idea/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_DESKTOP_IDEA_AGENT_TOKEN",
        "RUN_MCP_TIMEOUT_MS": "180000"
      }
    }
  }
}
```

重新加载 MCP 后，应只看到以下工具：

```text
idea_chat
idea_session_get
idea_task_status
```

## 验收方式

1. 在笔记本调用 `idea_chat`，记录返回的 `conversation_id`。
2. 在主力机调用 `idea_session_get` 并传入该 `conversation_id`，确认能看到笔记本侧消息。
3. 在主力机调用 `idea_chat`，将相同 `conversation_id` 传回，确认会话继续而非创建新的 IDEA 身份。
4. 如需撤销某台设备的 Agent 接入，撤销对应 MCP credential；不会影响另一台设备，也不会影响只读记忆凭据。

## 当前边界

- `idea_chat` 运行云端 IDEA，并持久化 Owner 会话和已获授权的长期记忆上下文。
- 当前 MCP 不会直接向 TRAE 暴露文件读写、命令执行、服务器控制、主力机 GPU、屏幕、键鼠或 Device Agent 工具。
- 主力机本地构建、文件和 GPU 调度属于后续 Device Agent 阶段；当前完成的是主力机识别并续接同一个云端 IDEA 的入口。
