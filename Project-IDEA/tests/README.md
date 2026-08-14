# IDEA Assistant 测试集

Project IDEA / IDEA Assistant 的系统性测试集，按层级组织，覆盖服务端工具链、数据层、平台 API 与真实进程冒烟。

## 目录结构

```text
tests/
├── pytest.ini                # pytest 配置（testpaths / pythonpath / 标记）
├── conftest.py               # 公共 fixtures：owner_context / member_context / platform_store
├── unit/                     # 单元测试：单层内部逻辑，不依赖网络与进程
│   ├── test_tool_policy.py   # 工具策略决策链、沙箱边界、审批闭环、回滚、命令白名单、SSH、CapabilityGrant
│   └── test_platform_store.py# PlatformStore 数据层：审批/授权/文件变更审查/审计/记忆版本守卫
├── integration/              # 集成测试：TestClient 起完整 app，跨层 API 流程
│   └── test_platform_auth.py # 鉴权、MCP、空间隔离、记忆 API、审批 API、能力授权 API、文件变更 API
└── e2e/                      # 端到端：真实启动 server 进程（默认跳过）
    └── test_e2e_smoke.py     # 服务可启动 + 鉴权闸门 401/200
```

## 快速开始

```powershell
pip install pytest          # 测试依赖（生产依赖见 server/requirements.txt）
python -m pytest tests      # 运行全部测试
```

pytest 通过 `pytest.ini` 的 `pythonpath = ../server` 解析 server 包，测试内可直接 `from platform_auth import ...`，无需修改 `sys.path`。

## 常用运行方式

```powershell
python -m pytest tests                        # 全量（默认跳过 e2e）
python -m pytest tests/unit -v                # 仅单元测试，显示用例名
python -m pytest tests/integration -v         # 仅集成测试
python -m pytest tests/unit/test_platform_store.py::TestToolApprovalStore   # 指定测试类
python -m pytest tests/unit/test_platform_store.py::TestToolApprovalStore::test_expired_approval_is_not_found  # 指定用例
python -m pytest tests -k "ssh or grant"      # 按关键字筛选
```

### 启用端到端冒烟

e2e 会真实启动 server 进程（隔离端口与临时数据库），默认跳过以免干扰日常运行：

```powershell
$env:IDEA_RUN_E2E = "1"; python -m pytest tests/e2e -v
```

可用 `IDEA_E2E_PORT` 指定端口（默认 8900）。

## 覆盖矩阵

| 功能面 | 测试文件 | 层级 |
|---|---|---|
| 工具读取/写/删除策略、未知工具拒绝 | unit/test_tool_policy.py | 单元 |
| 沙箱边界：路径逃逸、敏感路径、审批门禁 | unit/test_tool_policy.py | 单元 |
| 审批闭环：指纹复用、批准/拒绝、过期、审计 | unit/test_tool_policy.py + unit/test_platform_store.py | 单元 |
| 命令黑名单、工作目录锁定 | unit/test_tool_policy.py | 单元 |
| SSH 白名单/认证/文件传输边界 | unit/test_tool_policy.py | 单元 |
| CapabilityGrant：空间隔离、过期、撤销 | unit/test_tool_policy.py + unit/test_platform_store.py | 单元 |
| 回收站备份与 restore 回滚 | unit/test_tool_policy.py | 单元 |
| PlatformStore 数据层：审批/授权/文件变更/审计/记忆版本守卫 | unit/test_platform_store.py | 单元 |
| 平台 API：鉴权、MCP 凭据、空间隔离、记忆 API | integration/test_platform_auth.py | 集成 |
| 审批/能力授权/文件变更审查 API 闭环 | integration/test_platform_auth.py | 集成 |
| 服务可启动、鉴权闸门 | e2e/test_e2e_smoke.py | 端到端 |

## 新增测试指南

1. **确定层级**：单层逻辑 → `unit/`；跨层 API 流程 → `integration/`（用 `TestClient(main.app)`）；需要真实进程 → `e2e/`（加 `@pytest.mark.e2e` 并默认 skip）。
2. **复用 fixtures**：`conftest.py` 提供 `owner_context` / `member_context`（工具层）、`platform_store`（临时 sqlite 数据层）。
3. **命名**：文件 `test_*.py`，类 `TestXxx`，用例 `test_*`，断言用原生 `assert`（pytest 风格）。
4. **隔离性**：每个用例尽量使用 `tmp_path` / 独立数据库；不要共享可变全局状态。
5. **跑通再提交**：`python -m pytest tests` 全绿后再提交。

## 迁移说明

原 `server/test_tool_policy.py`、`server/test_platform_auth.py` 已分别迁入 `tests/unit/` 与 `tests/integration/`（git 保留 rename 历史），导入路径由 `pytest.ini` 的 `pythonpath` 解析，无需改动测试代码。
