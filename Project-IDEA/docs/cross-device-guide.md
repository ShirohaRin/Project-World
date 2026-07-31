# IDEA 智能体跨设备使用指南

---

## 当前状态

本文保留跨设备目标架构与后续操作参考。IDEA 服务端已经部署，并已具备持久化会话、任务与同步事件的第一阶段基础；桌面客户端仍以本地工作区为主，尚未实现工作区文件同步、设备代理、长期记忆服务或移动端任务界面。

当前跨设备策略分为两条链路：代码和脱敏文档通过 GitHub 私有仓库在 Windows/Linux 间同步；会话、任务和后续伊迪亚记忆通过受控 IDEA 服务端保存，绝不进入 GitHub。具体边界见 [跨设备同步基线](cross-device-sync-baseline.md)。云端知识库与长期记忆的专项路线图见 [云端知识库与长期记忆路线图](cloud-knowledge-memory-roadmap.md)。

---

## 总览

完成接入后，你可以在以下**所有设备**上使用 IDEA 智能体：

| 设备 | 客户端 | 连接方式 | MCP 后端 |
|------|--------|----------|----------|
| iOS / Android 手机 | TRAE 移动端 | 任务下发 → 桌面/云端执行 → 结果推送 | Linux MCP Server |
| 公司 Windows 电脑 | TRAE IDE / TRAE Work 桌面版 | 内置 Agent → Subagent（本地） 或 HTTP MCP | 本地 Subagent + 远程 MCP |
| 家中 Mac/Windows | TRAE Work 桌面版 | HTTP MCP（直接连接远程服务器） | Linux MCP Server |
| 任意浏览器 | TRAE Work 网页版 | HTTP MCP → 云端沙箱 | Linux MCP Server |

---

## 未来实施参考（三步走）

### Step 1：本地先跑通 Subagent（5 分钟）

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

### Step 2：接入已部署的 IDEA 服务

```bash
# 服务地址
https://shiroha-rin.world

# 健康检查
https://shiroha-rin.world/health
```

当前服务端已持久化会话、任务与同步事件；跨设备工作区文件同步和正式多设备登录仍待后续阶段完成。

---

### Step 3：在 TRAE 中添加 MCP Server（后续）

#### TRAE IDE / TRAE Work 桌面版：

1. 打开 **设置** → **MCP**
2. 点击 **添加** → **手动添加**
3. 编辑 `server/trae-mcp-config.json`，把 `YOUR_LINUX_SERVER_IP` 换成真实 IP，`YOUR_SECRET_TOKEN_HERE` 换成部署脚本输出的 Token
4. 粘贴 JSON 并点击 **确认**

#### 跨设备：

所有设备的 TRAE 客户端都可以重复 Step 3 添加同一个 MCP Server —— 因为 HTTP MCP 是基于网络的，任何能访问服务器 IP 的设备都能连接。

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
4. **LLM 调用**：当前模板中 Agent 的实际执行需要接入大模型 API（如豆包、GLM 等），需额外配置 API Key
