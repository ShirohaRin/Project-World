# Project World 跨平台与跨设备测试手册

## 目的

本手册用于验证四条彼此独立的链路：

1. Project World 的公开代码与脱敏文档能通过 Git 在不同设备、不同系统间续接开发。
2. IDEA Assistant 的服务端会话、任务与授权共享数据能跨设备读取。
3. IDEA 的 Owner 私有记忆能在获批准设备之间同步，并能阻止未批准设备访问。
4. TRAE 能否通过受控 MCP 读取当前授权范围内的记忆。

不要把四条链路混为一谈。Git 不同步私人记忆；MCP 不提供文件同步；Owner 设备批准不等于正式生产认证。

## 测试结论记录

每完成一项，在本节填写日期、设备、结果和异常。不要记录 Access Token、Refresh Token、部署 Token、验证码、设备真实序列号或记忆正文。

| 项目 | 日期 | 设备 A | 设备 B | 结果 | 异常/备注 |
|---|---|---|---|---|---|
| 服务健康检查 |  |  |  | 通过/失败 |  |
| 服务端自动化回归 |  |  |  | 通过/失败 |  |
| Git Windows → Linux |  |  |  | 通过/失败 |  |
| Git Linux → Windows |  |  |  | 通过/失败 |  |
| Owner 设备批准 |  |  |  | 通过/失败 |  |
| Owner 记忆读取 |  |  |  | 通过/失败 |  |
| Owner 记忆冲突 |  |  |  | 通过/失败 |  |
| Owner 设备撤销 |  |  |  | 通过/失败 |  |
| TRAE MCP 连接 |  |  |  | 通过/阻塞/失败 |  |
| TRAE MCP 工具调用 |  |  |  | 通过/阻塞/失败 |  |

## 一、测试前准备

### 1. 设备要求

准备两套相互独立的环境：

- 设备 A：当前 Windows 开发机。
- 设备 B：另一台 Windows、Linux 或 macOS 设备。Linux 可使用 `/opt/project-world` 工作树。

设备 B 不应复制设备 A 的以下内容：

- Electron 的 `userData` 或应用配置目录；
- 浏览器 Local Storage；
- Access Token 或 Refresh Token；
- IDEA 服务端数据库；
- `PROJECT_MEMORY.md`、`IDEA/` 或其他私人资料。

原因是每台设备必须生成自己的设备标识和登录会话，才能验证批准与撤销是否真实生效。

### 2. 服务健康检查

在任一能访问服务的设备打开：

```text
https://shiroha-rin.world/health
```

预期响应至少包含：

```json
{
  "status": "healthy"
}
```

若不是 `healthy`，停止后续在线验收，先恢复服务。

### 3. 本地自动化基线

在 Windows 工作区执行：

```powershell
cd "D:\Project World\Project-IDEA\server"
python -m unittest -v test_platform_auth.py
```

预期结果：

```text
Ran 12 tests
OK
```

这套测试使用临时 SQLite 数据库，不会修改线上记忆。它已覆盖 Owner 批准、双设备记忆同步、revision 冲突、设备撤销和 MCP JSON-RPC 协议。

## 二、Project World 代码跨平台验证

本节只验证公开代码和脱敏文档。它不验证私人记忆，也不允许将整个工作目录做双向覆盖同步。

### 1. Windows → Linux

在设备 A 创建一个无敏感信息的验收文件，例如：

```powershell
cd "D:\Project World"
Set-Content -Path "Project-IDEA\docs\sync-check-A-to-B.md" -Value "Cross-platform Git check created on device A."
git status --short
```

确认暂存前文件列表不含敏感内容：

```powershell
git add "Project-IDEA/docs/sync-check-A-to-B.md"
git diff --cached --name-only
```

列表中只能出现刚创建的验收文件。随后提交并推送：

```powershell
git commit -m "test: verify Windows to Linux sync"
git push
```

在设备 B 拉取：

```bash
cd /opt/project-world
git pull --ff-only
test -f "Project-IDEA/docs/sync-check-A-to-B.md"
git status --short
```

通过标准：

- `git pull --ff-only` 成功；
- 验收文件存在；
- 未出现合并冲突；
- 工作树没有意外的私人文件或数据库。

### 2. Linux → Windows

在设备 B 编辑同一验收文件，增加一行：

```bash
printf '\nVerified on device B.\n' >> "Project-IDEA/docs/sync-check-A-to-B.md"
git add "Project-IDEA/docs/sync-check-A-to-B.md"
git commit -m "test: verify Linux to Windows sync"
git push
```

回到设备 A：

```powershell
cd "D:\Project World"
git pull --ff-only
Get-Content "Project-IDEA\docs\sync-check-A-to-B.md"
git status --short
```

通过标准：设备 A 能看到 `Verified on device B.`，并且工作树没有冲突。

### 3. 清理验收文件

验收完成后，可以保留此文件作为记录，或在任一设备删除、提交并推送：

```powershell
Remove-Item "Project-IDEA\docs\sync-check-A-to-B.md"
git add "Project-IDEA\docs\sync-check-A-to-B.md"
git commit -m "test: remove cross-platform sync marker"
git push
```

## 三、IDEA Assistant 跨设备服务验证

本节验证普通服务能力。普通 IDEA Assistant 不应出现 Owner、私有记忆、私有设备批准或申请入口。

### 1. 两个普通账号与共享空间

1. 在设备 A 使用账号 A 登录访客版 IDEA Assistant。
2. 在设备 B 使用账号 B 登录访客版 IDEA Assistant。
3. 将两个账号加入同一个已授权共享空间。
4. 在设备 A 创建测试任务或测试对话。
5. 在设备 B 刷新同步事件或重新加载任务列表。

通过标准：

- 授权共享空间中的任务、对话和共享记忆按空间权限显示；
- `viewer` 可读取共享记忆，但不能修改；
- `owner` 与 `editor` 可修改共享记忆；
- 访客版全程不显示私有记忆或 Owner 设备控制。

服务端已有自动化覆盖共享记忆的角色权限和 revision 冲突。若没有可用的访客客户端安装包，可先只运行本手册的“本地自动化基线”，不要把服务端测试结果写成真实客户端验收。

## 四、IDEA Owner 私有记忆跨设备验证

本节需要两台设备均能运行私有版 IDEA，或者至少能够通过私有客户端安全地登录不同设备会话。

### 1. 设备 A 建立私有会话

1. 在设备 A 登录你的 Owner 账号。
2. 确认界面是私有 IDEA，而不是访客 IDEA Assistant。
3. 记录设备 A 显示名或匿名设备标识，不记录真实 Token。
4. 确认能看到私有记忆和私有设备列表。

预期：设备 A 已是批准设备，或可由引导身份完成首次批准。

### 2. 设备 B 首次登录必须受限

1. 在设备 B 使用相同 Owner 账号登录。
2. 不导入 A 的应用数据，不复制任何 Token。
3. 登录后查看可用路由和功能。
4. 回到设备 A 的私有设备列表查看 B。

通过标准：

- B 首次是 `idea_assistant`，而不是 `owner_idea`；
- B 看不到 Owner 私有记忆；
- A 能看到 B 状态为 `pending`；
- B 不能通过指定 `agent_id=idea` 强行升级到 IDEA。

### 3. 批准设备 B

1. 在设备 A 的私有设备列表中找到 B。
2. 点击批准。
3. 在 B 登出。
4. 在 B 重新登录。

通过标准：

- B 重新登录后进入 `owner_idea`；
- B 才能看到私有记忆功能；
- 未重新登录的旧会话不会自动升级。

### 4. 创建和读取一条验收记忆

在 A 显式创建一条 Owner 记忆：

```text
跨设备记忆验收标记：由设备 A 创建；只用于验证，不包含私人内容。
```

保存时必须执行客户端要求的明确确认。随后在 B：

1. 刷新同步或重新打开记忆面板。
2. 搜索 `跨设备记忆验收标记`。
3. 打开搜索结果，核对创建设备与内容。

通过标准：

- B 能读取同一条 Owner 记忆；
- 访客版和未批准设备不能读取；
- 该记忆不会出现在 Git 工作树、GitHub 或共享空间；
- 只在 Owner 已批准设备之间可见。

### 5. 验证 revision 冲突

1. 在 A 打开验收记忆，保持页面不刷新。
2. 在 B 修改记忆内容并保存。
3. 确认 B 保存成功。
4. 不刷新 A，直接修改并保存 A 的旧版本。

通过标准：

- B 修改后 revision 递增；
- A 收到冲突提示；
- 服务端返回 HTTP `409 Conflict`；
- 响应头包含当前 `X-Memory-Revision`；
- B 的更新没有被 A 静默覆盖。

### 6. 验证撤销即时失效

1. 在 A 的私有设备列表中撤销 B。
2. 不关闭 B，立即在 B 刷新私有记忆或请求个人资料。
3. 允许 B 尝试自动刷新登录会话。
4. 在 B 再次登录一次。

通过标准：

- B 的当前请求被拒绝，旧 Access Token 返回 `401`；
- B 不能用旧 Refresh Token 换取新 Token，返回 `401`；
- B 回到登录状态；
- B 再登录后重新成为 `pending`，不会自动恢复私域访问。

## 五、TRAE MCP 记忆接入验证

### 1. 已知服务端协议能力

MCP 地址：

```text
https://shiroha-rin.world/mcp/memory/mcp
```

当前只开放两个只读工具：

```text
memory_search
memory_get
```

服务端标准 JSON-RPC 自动化测试已经通过：

```text
initialize → tools/list → memory_search → memory_get
```

它不开放文件读写、本地命令、Agent 调度、设备控制或自动写入记忆。

### 2. 配置前检查

TRAE MCP 模板在：

```text
Project-IDEA/server/trae-mcp-config.json
```

模板只是一份占位配置，不能提交真实 Token。当前模板的 Header 包含 `Authorization` 与可选 `X-Space-ID`；而设备绑定会话还要求请求携带与当前登录设备一致的 `X-Device-ID`。

因此先在 TRAE 的 MCP 添加页面确认是否支持自定义 Header。如果无法添加 `X-Device-ID`，本轮真实 TRAE 接入应记录为“受客户端 Header 能力阻塞”，不要移除服务端设备绑定来绕过。

可用于受控测试的 Header 结构如下，值必须来自当前设备的短期安全会话：

```json
{
  "Authorization": "Bearer <当前设备的短期 Access Token>",
  "X-Device-ID": "<当前设备对应的 Device ID>",
  "X-Space-ID": "<可选且已授权的 Space ID>"
}
```

不要执行以下操作：

- 不在仓库、Markdown、截图或聊天记录中粘贴真实 Token；
- 不把部署引导 Token 填进 MCP；
- 不将 A 的会话 Token 复制给 B；
- 不为了使 MCP 连接成功而关闭设备绑定。

### 3. TRAE 内操作

1. 在已经批准的设备上打开 TRAE 设置中的 MCP 页面。
2. 新增 Streamable HTTP MCP 服务。
3. 填写 MCP 地址。
4. 在仅本机保存的安全配置中填写当前设备对应的 Header。
5. 保存后观察连接状态。
6. 查看工具列表。
7. 调用 `memory_search`，查询第四章创建的验收记忆。
8. 从结果中复制记忆 ID，在 `memory_get` 中读取该 ID。

通过标准：

- MCP 连接成功；
- 工具列表严格只有 `memory_search` 与 `memory_get`；
- `memory_search` 仅返回当前获授权范围内的记忆；
- `memory_get` 能读取已搜索到的记忆；
- 工具列表中没有文件、命令、浏览器、设备控制或写记忆能力。

### 4. 拒绝访问检查

使用以下一种受控方式验证拒绝：

1. 移除 `Authorization`；或
2. 使用错误的 `X-Device-ID`；或
3. 在未批准的 Owner 设备上尝试访问私域记忆。

通过标准：请求被拒绝为 `401` 或权限错误，且不会返回 Owner 私有记忆。

测试完成后立即删除 TRAE 中临时保存的测试 Token；不要依赖它长期有效。

## 六、当前不应测试或宣称完成的项目

- Linux 访客版、私有版的最终 Electron 安装包尚未产出；
- TRAE 图形客户端是否完整支持所需 Header 仍需实测；
- 工作区文件自动双向同步、离线队列和冲突合并尚未实现；
- 远程桌面控制、OCR、屏幕操作、键鼠执行和 DeviceAction 尚未实现；
- 正式手机号认证、生产邮件投递和账号恢复尚未完成。

开发/过渡认证的限制见 [认证边界](authentication-boundary.md)，服务端双设备自动化证据见 [跨设备验收记录](cross-device-acceptance.md)。

## 七、出现失败时的记录格式

每次失败请记录以下最小信息，避免泄漏凭据：

```text
日期时间：
测试章节与步骤：
设备系统与客户端版本：
服务健康状态：
HTTP 状态码或界面错误文字：
是否携带正确的设备标识：是/否/不确定
是否为已批准 Owner 设备：是/否/不适用
是否已重新登录：是/否
复现步骤：
```

不要记录真实邮箱、验证码、Token、完整设备 ID、私有记忆正文、数据库内容或日志原文中的敏感字段。
