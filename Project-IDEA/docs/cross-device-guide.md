# IDEA 智能体跨设备使用指南

---

## 当前状态

本文记录当前跨设备能力、验收方法与后续边界。IDEA 服务端已经部署，并已具备持久化会话、任务、同步事件、受控长期记忆与 Owner 设备批准；桌面客户端仍以本地工作区为主，工作区文件同步、设备代理和移动端任务界面尚未完成。

当前跨设备策略分为两条链路：代码和脱敏文档通过 GitHub 私有仓库在 Windows/Linux 间同步；会话、任务和后续伊迪亚记忆通过受控 IDEA 服务端保存，绝不进入 GitHub。具体边界见 [跨设备同步基线](cross-device-sync-baseline.md)。云端知识库与长期记忆的专项路线图见 [云端知识库与长期记忆路线图](cloud-knowledge-memory-roadmap.md)。

---

## 总览

当前已验证的跨设备能力集中在服务端会话、Owner 设备、长期记忆与同步事件；以下客户端形态是目标接入面，不代表每一项都已完成真实客户端验收：

| 设备 | 客户端 | 连接方式 | MCP 后端 |
|------|--------|----------|----------|
| iOS / Android 手机 | TRAE 移动端 | 任务下发 → 桌面/云端执行 → 结果推送 | Linux MCP Server |
| 公司 Windows 电脑 | TRAE IDE / TRAE Work 桌面版 | 内置 Agent → Subagent（本地） 或 HTTP MCP | 本地 Subagent + 远程 MCP |
| 家中 Mac/Windows | TRAE Work 桌面版 | HTTP MCP（直接连接远程服务器） | Linux MCP Server |
| 任意浏览器 | TRAE Work 网页版 | HTTP MCP → 云端沙箱 | Linux MCP Server |

---

## 当前可验证能力

服务端自动化验收使用两个独立设备标识，覆盖：

- Owner 设备 `pending`、`approved`、`revoked` 状态；
- 设备 B 批准前只能进入 `idea_assistant`；
- Owner 记忆在两个已批准设备之间查询与同步；
- 记忆 revision 冲突返回 `409`；
- 撤销设备后 Access Token 与 Refresh Token 立即失效。

自动化测试与人工步骤见 [跨设备验收记录](cross-device-acceptance.md)。

## 当前接入步骤

### Step 1：本地先跑通 Subagent

当前项目已经创建好 Subagent 文件在 `.trae/agents/`：

```
.trae/agents/
├── idea-dispatcher.md     ← IDEA 总调度
├── idea-pwa.md            ← 项目管理
├── idea-researcher.md     ← 科研
└── idea-agent-producer.md ← 智能体生产
```

**测试方法**：在 TRAE IDE 打开 `d:\Program-IDEA` 项目，在 AI 对话中 @Agent，说：

> "帮我制定一个项目计划，项目名称叫 IDEA-Demo"

如果 Agent 自动调用了 `idea-pwa` subagent，说明 Subagent 已生效。

---

### Step 2：检查已部署的 IDEA 服务

```bash
# 服务地址
https://shiroha-rin.world

# 健康检查
https://shiroha-rin.world/health
```

当前服务端已持久化会话、任务、同步事件与长期记忆；生产认证、工作区文件同步、离线队列和真实 TRAE 客户端接入仍需单独验收。

---

### Step 3：在受控客户端中添加 MCP Server

#### TRAE IDE / TRAE Work 桌面版：

1. 打开 **设置** → **MCP**。
2. 点击 **添加** → **手动添加**。
3. 使用 `server/trae-mcp-config.json` 中的地址：`https://shiroha-rin.world/mcp/memory/mcp`。
4. 使用安全设备会话产生的用户 Access Token，不使用部署引导 Token，不把真实 Token 写入仓库。
5. 确认客户端能够发送与会话匹配的 `X-Device-ID`；当前模板仍需结合 TRAE 的自定义 Header 能力完成真实接入验收。

#### 跨设备：

不同设备不能简单复制同一份 Token 或 Electron 用户数据。每台设备应使用自己的设备标识和可撤销会话，Owner 私域还必须经过设备批准。

---

## 跨设备工作流示例

```
手机（地铁上）
  │
  │ "帮我做一份 Transformer 架构的文献综述"
  │ 语音输入 → TRAE 移动端
  │
  ▼
TRAE Work 桌面版（公司电脑）
  │
  │ 接收任务 → 调用 Linux MCP Server 的 researcher_literature_review 工具
  │
  ▼
Linux 服务器
  │ Researcher 智能体执行：
  │ 1. 检索 arXiv / Google Scholar
  │ 2. 按 IMRaD 结构组织综述
  │ 3. 标注来源可信度 Tier A/B/C/D/E
  │
  ▼
电脑上输出完成 → 手机收到推送通知 → 查看结果
```

---

## 尚未完成

- Linux 访客版与私有版最终安装包产物；
- TRAE GUI 的真实 MCP 接入；
- 工作区文件同步、离线队列与冲突应用；
- 设备代理、OCR、屏幕操作与执行回执；
- 正式手机号认证与生产级多用户注册。

## 后续：接入飞书（延期）

完成 TRAE 接入后，下一步是在 Linux MCP Server 上集成飞书 SDK，添加飞书相关的 MCP 工具：

- `lark_send_message` — 发送飞书消息
- `lark_create_task` — 创建飞书任务
- `lark_create_doc` — 创建飞书文档
- `lark_search_calendar` — 查询飞书日历

最终链路：**TRAE（用户交互）→ Linux MCP Server（智能体调度）→ 飞书 API（协作执行）**

---

## 注意事项

1. **网络安全**：生产环境使用 Nginx 反向代理 + HTTPS + 域名
2. **Token 安全**：不要将 Token 提交到 Git 仓库
3. **服务器成本**：基础配置的云服务器约 50-100 元/月
4. **LLM 调用**：Agent 实际执行依赖服务端模型配置，API Key 只保存在受控运行环境，不进入 GitHub。
