# Project IDEA

Project IDEA 是 [Project World](../README.md) 的第一子项目，旨在建立支持 Project World 开发工作与对应生物科研的平台。它侧重**工作能力、执行与复杂问题处理**，IDEA Assistant 的发展重点是生物科研工作。

历史资料中也使用 `Program IDEA`。本目录是软件平台；仓库中的 [`IDEA/`](../IDEA/README.md) 则主要保存主项目的世界观、正文与设计资料，两者不要混淆。

## 与 IDEA 角色、K.U.A.T 的分工

IDEA（伊迪亚）是服务白羽奈绪的角色与生活助手；IDEA Assistant 是工作平台中的助手产品。主项目侧保留 IDEA 的角色映射与生活助手需求，平台继续发展科研与开发能力。

根据 2026-10-03 的项目划分，世界构建中的项目管理、关系可视化、设定卡片、逻辑连接、思维导图和多 Agent 世界模拟，交由第四子项目 [Project K.U.A.T](../Project-KUAT/README.md) 承接。拆分是为了让工作执行与管理构思分别得到充分发展。

这是产品职责的调整。白羽奈绪已明确 K.U.A.T 没有可供迁移的代码，将从零开发；`Agents/` 中的历史项目管理 Agent 定义不能视为它的既有实现。科研任务中的 Agent 协作也不等同于世界模拟。

## 当前代码与文档入口

| 目录或文件 | 用途 |
| --- | --- |
| [IDEA Assistant Code/](IDEA%20Assistant%20Code/) | 桌面客户端源码，React、TypeScript 与 Electron；包含 Assistant / Owner 构建脚本 |
| `IDEA Assistant/`、`IDEA Assistant SRH/`、`IDEA Assistant Android/` | 仓库内其他客户端相关目录，具体用途以各目录内容为准 |
| [server/](server/) | Python 平台服务、账户与权限、Agent 执行等服务端实现 |
| [modules/](modules/) | 生物分析、工作流、浏览器、自动化和实时语音等能力模块 |
| [Agents/README.md](Agents/README.md) | Agent 定义层的结构与约定；定义文件不代表所有角色已经接入运行时 |
| [memory/USAGE.md](memory/USAGE.md) | 长期记忆服务使用说明 |
| [tests/README.md](tests/README.md) | 测试组织与说明 |
| [CLIENT_SERVICE_BOUNDARIES.md](CLIENT_SERVICE_BOUNDARIES.md) | 客户端、IDEA 服务与 RAG 的访问边界 |
| [PROJECT_RULES.md](PROJECT_RULES.md) | 唯一项目级开发规则入口，修改前必读 |

上述目录反映仓库已有内容，不是整个平台通过端到端验收的声明。具体功能状态、验证结果和限制，应查对应模块的设计与进度文档。

## 科研能力入口

- [生物方法模块算法清单](modules/bio_analysis_function/生物方法模块算法清单.md)：算法规划与开发进度入口。
- [fastp 预处理工作流](modules/workflow/workflow.md)：调用已有算法完成处理流程。
- [breseq 工作流](modules/workflow/breseq/breseq_workflow.md)：参考比对与变异检测流程。
- [实时语音模块设计](modules/realtime_voice/实时语音对话模块设计.md)：协议、模块边界与进度。

工作流是独立能力层，不在算法内部重复实现算法。各模块的验收条件遵循项目规则与专项文档。

## 本地开发入口

桌面客户端的以下命令来自 `IDEA Assistant Code/package.json`，在该目录下执行：

```powershell
npm install
npm run dev:electron
npm run build:assistant
npm run build:owner
```

这些分别用于安装依赖、启动开发环境以及执行两种客户端的构建脚本，并非要求依次全部运行。构建输出与打包行为以实际脚本为准；服务端配置与启动应另行核对 `server/` 及部署文档。此次总览整理没有运行应用或验证发布包。

早期 README 中“只提供离线功能、不连接任何服务”的描述不再适合作为整个平台的当前说明。客户端访问服务应遵守已有账户和项目权限边界，不能从总览推定某个目录已获得访问授权。

## Agent 定义与文档维护

`Agents/` 保留 IDEA、IDEA-ProgramWorldAdminister、IDEA-Reasearcher、IDEA-AgentProducer 等角色定义。基础文件为 `system.md` 与 `character.md`；原有规划中的配置、工具清单、知识和行为测试属于后续扩展方向，不应仅凭定义目录认定已经支持运行时加载。

项目总览、设计、Agent 定义、开发记录与操作指南使用 Markdown。详细开发约定统一维护在 `PROJECT_RULES.md`，模块进度留在对应模块文档；本页维护项目定位和阅读入口，避免复制易过时的权限白名单、测试数量或发布状态。
