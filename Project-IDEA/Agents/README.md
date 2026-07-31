# Agent 定义层

此目录集中存放 Program IDEA 的 Agent 定义，而不是 Agent 的运行时代码。

本目录中的说明、角色卡、系统提示词、测试说明与后续配置文档均使用 Markdown（`.md`）格式维护。结构化运行参数例外使用 YAML（`.yaml`）。

## 当前成员

| 目录 | 层级 | 定义文件 |
|---|---|---|
| `IDEA/` | L0 | `system.md`、`character.md` |
| `IDEA-ProgramWorldAdminister/` | L1 | `system.md`、`character.md` |
| `IDEA-Reasearcher/` | L1 | `system.md`、`character.md` |
| `IDEA-AgentProducer/` | L1 | `system.md`、`character.md` |

## 与平台的关系

- `Agents/`：声明 Agent 是谁、能做什么、不能做什么。
- `server/agents/`：当前 Python 平台中用于执行 Agent 能力的实现。
- `.trae/agents/`：面向 TRAE 的项目级 Subagent 适配文件。

三者目前独立维护，避免平台开发受 Agent 实现细节阻塞。后续将由平台增加配置装载、版本控制、测试执行与发布流程，再将本目录中的定义正式绑定到运行时。

## 预留标准

新增 Agent 必须创建独立目录，并至少包含 `system.md` 与 `character.md`。进入平台运行时前，应补充 `config.yaml`、`tools.yaml`、`tests/` 和必要的 `knowledge/`，由 IDEA Assistant 的审核与发布流程接管。
