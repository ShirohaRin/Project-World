# TRAE 跨设备记忆接入

## 目标

让笔记本与主力台式机上的 TRAE 读取同一份由服务端保存的 Owner 长期记忆。此接入不安装 IDEA 客户端，不同步本地文件，不授予命令或文件权限。

## 访问边界

- MCP 地址固定为 `https://shiroha-rin.world/mcp/memory/mcp`。
- 每台 TRAE 使用独立 MCP 凭据。
- 一枚凭据只绑定一个服务端空间，`X-Space-ID` 不能改变其范围。
- 只开放 `memory_search`、`memory_get`。
- MCP 凭据不能访问 `/api/*`，不能访问通用 `/mcp`，不能读写文件、执行命令、调度 Agent 或写入记忆。
- 凭据仅在创建响应中返回一次；服务器仅保存其哈希。

## 创建两枚凭据

这一步暂时通过 Owner 控制 API 完成，需要使用部署维护用的 Owner Bearer Token；不要把该 Token 填入 TRAE。

在笔记本 PowerShell 中分别执行。将环境变量只保留在当前终端，不要保存到脚本或仓库。

```powershell
$ownerToken = Read-Host '输入部署 Owner Token'
$headers = @{ Authorization = "Bearer $ownerToken" }

$laptop = Invoke-RestMethod `
  -Method Post `
  -Uri 'https://shiroha-rin.world/api/platform/owner/mcp-credentials' `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"device_label":"TRAE-笔记本"}'

$desktop = Invoke-RestMethod `
  -Method Post `
  -Uri 'https://shiroha-rin.world/api/platform/owner/mcp-credentials' `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"device_label":"TRAE-主力台式机"}'
```

将 `$laptop.token` 仅填入笔记本 TRAE，将 `$desktop.token` 仅填入主力台式机 TRAE。不要互换、截图、写入 Markdown、Git 或聊天记录。

## 在两台 TRAE 添加 MCP

在每台 TRAE 的 MCP 设置中添加 Streamable HTTP MCP：

```json
{
  "url": "https://shiroha-rin.world/mcp/memory/mcp",
  "headers": {
    "Authorization": "Bearer 此设备专属MCP凭据"
  }
}
```

如果 TRAE 使用完整配置文件，参照 `server/trae-mcp-config.json`，每台机器填各自的凭据。

连接成功后，工具列表必须严格只有：

```text
memory_search
memory_get
```

若出现 `read_file`、`write_file`、`run_command` 或任何其他工具，立刻移除该 MCP 配置；这说明接入到了错误地址。

## 双设备验收

1. 在服务端已有的 Owner 记忆中创建一条不含私人内容的标记，例如“TRAE 双设备验收：笔记本可读取”。
2. 在笔记本 TRAE 要求调用 `memory_search` 搜索“TRAE 双设备验收”。
3. 记录返回的记忆 ID，并调用 `memory_get`。
4. 在主力台式机重复第 2、3 步；两边应读到相同 ID 和内容。
5. 在任一设备撤销其专属 MCP 凭据后，该设备下一次 MCP 请求必须失败；另一台设备仍可读取。

MCP 是只读接入。需要保存或更新记忆时，仍通过服务端受控写入流程完成；不要要求 TRAE 自动把聊天写入长期记忆。

## 撤销

列出凭据：

```powershell
Invoke-RestMethod `
  -Uri 'https://shiroha-rin.world/api/platform/owner/mcp-credentials' `
  -Headers @{ Authorization = "Bearer $ownerToken" }
```

撤销单枚凭据：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri 'https://shiroha-rin.world/api/platform/owner/mcp-credentials/凭据ID/revoke' `
  -Headers @{ Authorization = "Bearer $ownerToken" }
```

随后在对应 TRAE 删除旧配置，重新创建一枚新凭据再接入。
