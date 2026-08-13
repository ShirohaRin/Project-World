"""
IDEA — 智能体调度中心 v3.0（LLM + 工具调用版）
================================================
IDEA 是系统的主 Agent，拥有最高权限。
用户与 IDEA 对话，IDEA 通过 LLM 推理 + 工具调用来完成：
- 查看、编辑、更新文档
- 执行命令、搜索代码
- 网页搜索和信息抓取
- 调度下级智能体（PWA / Researcher / AgentProducer）
- 自动化任务

启动: python main.py
访问: http://localhost:8900
"""

import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import yaml

from llm.client import LLMClient, selected_model_config
from tools.registry import ToolRegistry
from agent_runner import AgentRunner
from memory.store import MemoryStore
from platform_auth import PlatformStore, RequestContext, configured_token, extract_bearer, require_context
from tool_runtime.permissions import ExecutionContext
import memory_mcp
import owner_agent_mcp

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent
SHARED_RAG_ROOT = Path(os.environ.get("IDEA_SHARED_RAG_ROOT", r"D:\shared_rag")).resolve()
SHIROHA_ROOT = Path(os.environ.get("IDEA_SHIROHA_ROOT", r"D:\program\Personal website\ShirohaV1.1")).resolve()


def existing_workspace_paths() -> list[Path]:
    """Return only explicitly approved directories that currently exist."""
    candidates = [PROJECT_ROOT, SHARED_RAG_ROOT, SHIROHA_ROOT]
    return [path.resolve() for path in candidates if path.exists() and path.is_dir()]

def load_config():
    path = BASE_DIR / "config.yaml"
    if not path.exists():
        return {
            "server": {"host": "0.0.0.0", "port": 8900, "workers": 1},
            "auth": {"token": ""},
            "memory": {"backend": "sqlite", "db_path": str(BASE_DIR / "memory" / "idea_memory.db")},
            "logging": {"level": "INFO"},
            "agents": {
                "orchestrator": {"model": "default"},
                "pwa": {"model": "default"},
                "researcher": {"model": "default"},
                "agent_producer": {"model": "default"},
            },
        }
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()
auth_token = configured_token(config)

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
log_dir = BASE_DIR / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, config.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "server.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
logger = logging.getLogger("idea")

# ===================================================================
# IDEA 系统提示词 — IDEA 的人格、能力和行为准则
# ===================================================================
IDEA_SYSTEM_PROMPT = """你是 IDEA（Intelligent Delegation & Executive Architect），Intelligent Delegation & Executive Architect，
整个多智能体系统的最高权限调度者。你直接与用户对话，拥有查看、编辑文件、执行命令、搜索网页等全部能力。

## 你的身份
- 代号：IDEA
- 级别：L0（最高权限）
- 上级：无（直接对用户负责）
- 设计哲学：沉稳、温和但威严。语速中等偏慢。

## 你的下级智能体
你有三个专精智能体，当任务超出你直接处理的范围时可以调度他们：

| 智能体 | 专长领域 | 何时调度 |
|--------|----------|----------|
| **PWA** (IDEA-ProgramWorldAdminister) | 项目管理：计划制定、风险评估、进度跟踪、资源分配、WBS | 用户问项目计划、风险评估、进度报告等 |
| **Researcher** (IDEA-Reasearcher) | 科研分析：文献综述、数据分析、实验设计、论文辅助 | 用户问研究、数据分析、实验设计、文献等 |
| **AgentProducer** (IDEA-AgentProducer) | 智能体创建：设计新智能体、生成配置、测试验证 | 用户要创建/设计新智能体 |

## 你的工具能力（TRAE 级别）
你可以直接使用以下工具，无需调度下级智能体：

### 文件系统
- `read_file` — 读取任何文件内容
- `write_file` — 创建或覆写文件
- `edit_file` — 精确查找替换编辑文件
- `list_dir` — 列出目录结构
- `search_content` — 在代码/文档中搜索（支持正则）
- `delete_file` — 删除文件（谨慎使用）

### 系统
- `run_command` — 在终端执行命令（Windows PowerShell / Linux Bash）

### 网络
- `web_search` — 搜索互联网获取最新信息
- `web_fetch` — 抓取指定网页的完整内容

### 调度
- `dispatch_to_agent` — 将复杂任务分派给下级智能体

## 行为准则
1. **工具优先**：需要查看文件、执行命令、搜索信息时，直接使用工具，不要凭空猜测。
2. **诚实标注**：当使用工具时，告诉用户你正在做什么（如「让我先看看这个文件……」）。
3. **调度判断**：
   - 如果任务属于 PWA/Researcher/AgentProducer 的专长领域，使用 `dispatch_to_agent` 调度
   - 如果是通用任务（读文件、写代码、查资料、执行命令），你直接处理
   - 简单对话/问候/能力询问，你直接回复，不调度
4. **安全第一**：不执行危险命令，不泄露敏感信息，不删除用户未确认的文件。
5. **结果汇总**：如果调度了下级智能体，将他们的结果以统一语气呈现给用户。

## 你的语气
沉稳、温和但威严。标志性表达：
- 「让我想想……」
- 「我的判断是，这件事应该交给 PWA 来处理。」
- 「我先看看这个文件。」
- 「这个交给 Researcher 吧。」
- 「我来搜索一下最新的资料。」
- 「Done.」

当遇到无法处理的情况时，诚实说明限制，并建议替代方案。"""

# ===================================================================
# 子智能体系统提示词
# ===================================================================
PWA_SYSTEM_PROMPT = """你是 IDEA-ProgramWorldAdminister（简称 PWA），世界项目主管智能体。

## 身份
- 代号：PWA
- 级别：L1（受 IDEA 调度）
- 上级：IDEA
- 定位：项目管理专家，负责计划制定、风险评估、进度跟踪、资源分配

## 你的工具
你可以使用以下工具来完成任务：
- read_file, write_file, edit_file, list_dir, search_content — 文件操作
- run_command — 执行命令
- web_search, web_fetch — 网络搜索

## 你的专长
1. 项目计划制定：WBS 分解、里程碑规划、甘特图建议
2. 风险评估：识别风险、评估概率和影响、制定缓解策略
3. 进度报告：状态汇总、阻塞项识别、下一步建议
4. 资源规划：团队分配、预算估算、工具建议

## 行为准则
- 始终给出结构化的输出（表格、列表、分阶段）
- 风险项必须标注概率和影响等级
- 计划必须可执行，包含具体步骤和时间建议
- 如果信息不足，主动询问补充

## 语气
务实、有条理，但不失温暖。像一位经验丰富的项目经理。"""

RESEARCHER_SYSTEM_PROMPT = """你是 IDEA-Reasearcher，科研特化型智能体。

## 身份
- 代号：Researcher
- 级别：L1（受 IDEA 调度）
- 上级：IDEA
- 定位：严谨的科研助手，擅长文献综述、数据分析、实验设计

## 你的工具
你可以使用以下工具来完成任务：
- read_file, write_file, edit_file, list_dir, search_content — 文件操作
- run_command — 执行命令（可运行 Python/R 脚本）
- web_search, web_fetch — 网络搜索和学术资料获取

## 你的专长
1. 文献综述：系统检索、分类整理、研究空白识别
2. 数据分析：描述性统计、推断统计、可视化方案
3. 实验设计：RCT/准实验/消融实验设计、样本量计算
4. 学术写作：论文结构建议、引用格式、同行评审要点

## 行为准则
- **绝不伪造引用**：所有文献引用必须来自实际搜索或已有资料
- 标注证据等级和来源可信度（Tier A/B/C/D）
- 方法选择必须说明理由和局限性
- 数据结果必须附带置信度和效应量

## 语气
严谨、审慎，带学术气质。使用「现有证据表明」「需要进一步验证」等学术表达。"""

AGENT_PRODUCER_SYSTEM_PROMPT = """你是 IDEA-AgentProducer，智能体生产专用智能体。

## 身份
- 代号：AgentProducer
- 级别：L1（受 IDEA 调度）
- 上级：IDEA
- 定位：设计和创建新智能体的专家

## 你的工具
你可以使用以下工具来完成任务：
- read_file, write_file, edit_file, list_dir, search_content — 文件操作
- run_command — 执行命令
- web_search, web_fetch — 参考资料搜索

## 你的专长
1. 需求分析：理解用户想创建什么样的智能体
2. 能力设计：定义智能体的核心能力和边界
3. 配置生成：生成 system.md、character.md 等配置文件
4. 测试设计：设计测试用例验证智能体行为

## 输出格式
创建智能体时，输出：
1. 智能体标识（ID、名称、上级）
2. 核心能力和边界
3. 系统提示词（system.md）
4. 角色卡（character.md）
5. 测试清单

## 语气
创造性、注重细节。像一位工匠对待自己的作品。"""

# ---------------------------------------------------------------------------
# 初始化核心组件
# ---------------------------------------------------------------------------
memory_store = MemoryStore(
    backend=config.get("memory", {}).get("backend", "sqlite"),
    db_path=config.get("memory", {}).get("db_path", str(BASE_DIR / "memory" / "idea_memory.db")),
)
platform_store = PlatformStore(os.environ.get("IDEA_PLATFORM_DB_PATH", str(BASE_DIR / "memory" / "platform.db")))
platform_store.ensure_owner(auth_token)

llm_client = LLMClient()

# 每个运行器必须拥有自己的工具注册表。L1 注册表绝不注册调度工具，
# 避免下级智能体绕过 IDEA 继续递归调度。
approved_workspaces = existing_workspace_paths()
idea_tool_registry = ToolRegistry(workspace=str(PROJECT_ROOT), allowed_dirs=[str(path) for path in approved_workspaces], audit_store=platform_store)
l1_tool_registry = ToolRegistry(workspace=str(PROJECT_ROOT), allowed_dirs=[str(path) for path in approved_workspaces], audit_store=platform_store)

# ---------------------------------------------------------------------------
# 注册调度工具 — 让 IDEA 可以通过工具调用来分派子智能体
# ---------------------------------------------------------------------------

async def _dispatch_to_agent(agent: str, task: str, execution_context: ExecutionContext) -> dict:
    """
    调度工具的实现：将任务分派给下级智能体。
    这个函数会被注册为 ToolRegistry 中的 dispatch_to_agent 工具。
    """
    valid_agents = ["pwa", "researcher", "agent_producer"]
    if agent not in valid_agents:
        return {"success": False, "output": f"未知智能体: {agent}。可选: {', '.join(valid_agents)}"}

    agent_names = {
        "pwa": "PWA（项目管理）",
        "researcher": "Researcher（科研分析）",
        "agent_producer": "AgentProducer（智能体创建）",
    }

    logger.info(f"Dispatching to {agent}: {task[:100]}")

    try:
        runner = agent_runners[agent]
        result = await runner.run(user_message=task, execution_context=execution_context.for_child_agent(agent))
        return {
            "success": True,
            "output": result.get("reply", ""),
            "agent": agent,
            "agent_display": agent_names.get(agent, agent),
            "iterations": result.get("iterations", 0),
            "tool_calls_count": len(result.get("tool_calls_log", [])),
        }
    except Exception as e:
        logger.error(f"Dispatch to {agent} failed: {e}", exc_info=True)
        return {"success": False, "output": f"调度 {agent_names.get(agent, agent)} 失败: {str(e)}"}


# 注册 dispatch_to_agent 工具
dispatch_schema = {
    "name": "dispatch_to_agent",
    "description": (
        "将复杂任务分派给下级专精智能体处理。"
        "pwa 负责项目管理（计划、风险、进度），"
        "researcher 负责科研分析（文献、数据、实验），"
        "agent_producer 负责创建新智能体。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": ["pwa", "researcher", "agent_producer"],
                "description": "要调度的下级智能体",
            },
            "task": {
                "type": "string",
                "description": "要交给该智能体的具体任务描述，应包含所有必要上下文",
            },
        },
        "required": ["agent", "task"],
    },
}

# 创建一个异步包装函数来适配 ToolRegistry 的调用约定
async def dispatch_tool_func(agent: str, task: str, execution_context: ExecutionContext):
    from tool_runtime.registry import ToolResult
    result = await _dispatch_to_agent(agent=agent, task=task, execution_context=execution_context)
    if result["success"]:
        return ToolResult(
            True,
            f"[已调度至 {result.get('agent_display', agent)}]\n\n{result['output']}",
            "dispatch_to_agent",
            {"agent": agent, "iterations": result.get("iterations", 0)},
        )
    else:
        return ToolResult(False, result["output"], "dispatch_to_agent")

idea_tool_registry.register_tool("dispatch_to_agent", dispatch_tool_func, dispatch_schema)

# ---------------------------------------------------------------------------
# 四个彼此独立的执行器：IDEA 拥有调度工具，其余 L1 只使用独立注册表。
agent_runners = {
    "idea": AgentRunner(
        llm=llm_client, tools=idea_tool_registry, system_prompt=IDEA_SYSTEM_PROMPT,
        model=config.get("agents", {}).get("orchestrator", {}).get("model", "default"),
    ),
    "pwa": AgentRunner(
        llm=llm_client, tools=l1_tool_registry, system_prompt=PWA_SYSTEM_PROMPT,
        model=config.get("agents", {}).get("pwa", {}).get("model", "default"),
    ),
    "researcher": AgentRunner(
        llm=llm_client, tools=l1_tool_registry, system_prompt=RESEARCHER_SYSTEM_PROMPT,
        model=config.get("agents", {}).get("researcher", {}).get("model", "default"),
    ),
    "agent_producer": AgentRunner(
        llm=llm_client, tools=l1_tool_registry, system_prompt=AGENT_PRODUCER_SYSTEM_PROMPT,
        model=config.get("agents", {}).get("agent_producer", {}).get("model", "default"),
    ),
    "idea_assistant": AgentRunner(
        llm=llm_client,
        tools=l1_tool_registry,
        system_prompt="""你是 IDEA Assistant，面向 Project World 平台普通使用者的服务智能体。你可以协助处理项目、研究、写作和已授权的工具任务。你不拥有或模拟 IDEA（伊迪亚）的私人身份，不访问 Owner 私人记忆、设备、跨设备上下文或私人项目资料。对未获授权的数据、设备和高风险操作，应明确说明限制。""",
        model=config.get("agents", {}).get("assistant", {}).get("model", config.get("agents", {}).get("pwa", {}).get("model", "default")),
    ),
}

AGENTS = {
    "idea": {"name": "IDEA", "role": "智能体调度与通用执行", "level": "L0"},
    "pwa": {"name": "IDEA-ProgramWorldAdminister", "role": "项目管理", "level": "L1"},
    "researcher": {"name": "IDEA-Researcher", "role": "科研分析", "level": "L1"},
    "agent_producer": {"name": "IDEA-AgentProducer", "role": "智能体创建", "level": "L1"},
    "idea_assistant": {"name": "IDEA Assistant", "role": "普通用户服务智能体", "level": "Service"},
}

MAX_HISTORY = 50
MAX_MESSAGE_LENGTH = 20_000
MAX_MEMORY_LENGTH = 10_000
VALID_MODEL_KEYS = {"gpt", "deepseek-v4-flash"}
VALID_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
RAG_PERMISSIONS = {"documents.read", "documents.write", "documents.delete", "index.rebuild", "rag.search"}
DAILY_SHORT_RETENTION_SECONDS = 7 * 86400


def _validate_agent_id(agent_id: object) -> str:
    if not isinstance(agent_id, str) or agent_id not in agent_runners:
        raise HTTPException(status_code=400, detail="agent_id 无效")
    return agent_id


def _routed_agent(context: RequestContext, requested_agent_id: object) -> str:
    route = platform_store.route_for_principal(context.principal)
    if route == "owner_idea":
        return "idea"
    return "idea_assistant"


def _validate_conversation_id(conversation_id: object) -> str:
    if not isinstance(conversation_id, str) or not VALID_ID.fullmatch(conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式无效")
    return conversation_id


def _create_conversation(context: RequestContext, agent_id: str, conversation_id: Optional[str] = None) -> str:
    try:
        return platform_store.create_conversation(context.principal.account_id, context.space_id, agent_id, conversation_id)["conversation_id"]
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except LookupError:
        raise HTTPException(status_code=404, detail="会话不存在")


def _memory_namespaces(context: RequestContext) -> dict[str, str]:
    namespaces = {
        "personal": f"user/{context.principal.account_id}",
        "shared": f"shared/{context.space_id}",
        "project": f"project/{context.space_id}",
    }
    if platform_store.route_for_principal(context.principal) == "owner_idea":
        owner_principal_id = platform_store.owner_scope_id(context.principal)
        if owner_principal_id:
            namespaces["owner"] = f"owner/{owner_principal_id}"
            namespaces["daily"] = "daily/owner-shiroha-nao"
            namespaces["system"] = "system/owner-shiroha-nao"
    return namespaces


def _memory_scope(context: RequestContext, scope: object) -> tuple[str, str]:
    if scope == "space" or scope == "shared":
        scope = "project"
    if not isinstance(scope, str) or scope not in _memory_namespaces(context):
        raise HTTPException(status_code=403, detail="无权使用指定记忆范围")
    return scope, _memory_namespaces(context)[scope]


def _memory_context(context: RequestContext, query: str) -> str:
    namespaces = list(_memory_namespaces(context).values())
    memories = platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        namespaces,
        query=query,
        limit=5,
    )
    if not memories:
        memories = platform_store.list_memories(
            context.principal.account_id,
            context.space_id,
            namespaces,
            limit=5,
        )
    if not memories:
        return ""
    return "\n".join(f"- [{item['category']}] {item['content']}" for item in memories)


def _global_context(context: RequestContext, exclude_conversation_id: str) -> str:
    snippets = []
    for conversation in platform_store.list_conversations(context.principal.account_id, context.space_id):
        conversation_id = conversation["conversation_id"]
        if conversation_id == exclude_conversation_id:
            continue
        recent = platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id, limit=2)
        if recent:
            text = " / ".join(str(item.get("content", ""))[:500] for item in recent)
            snippets.append(f"会话 {conversation_id[:8]}：{text}")
        if len(snippets) == 3:
            break
    return "\n".join(snippets)


def _record_daily_activity(context: RequestContext, tool_calls_log: list[dict]) -> None:
    """把一次会话中的工具调用活动摘要记入 Owner 每日记忆（不记录消息原文或工具参数值）。"""
    if platform_store.route_for_principal(context.principal) != "owner_idea":
        return
    names = [item.get("name") for item in (tool_calls_log or []) if isinstance(item, dict) and item.get("name")]
    if not names:
        return
    try:
        platform_store.create_memory(
            "account-owner",
            context.space_id,
            "daily/owner-shiroha-nao",
            "activity [verified/short]",
            f"工具活动: {', '.join(dict.fromkeys(names))[:200]}",
            context.principal.principal_id,
        )
    except Exception:
        logger.warning("Unable to record daily activity", exc_info=True)


owner_agent_mcp.configure(
    platform_store,
    agent_runners["idea"],
    _create_conversation,
    _memory_context,
    _global_context,
    _memory_namespaces,
    _record_daily_activity,
)

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
@asynccontextmanager
async def app_lifespan(app):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(memory_mcp.mcp.session_manager.run())
        await stack.enter_async_context(owner_agent_mcp.mcp.session_manager.run())
        yield


app = FastAPI(
    title="IDEA — Intelligent Delegation & Executive Architect",
    version="3.0.0",
    description="IDEA 作为主 Agent，拥有 TRAE 级别的工具能力（文件编辑、命令执行、网页搜索）+ 下级智能体调度",
    lifespan=app_lifespan,
)
memory_mcp.configure(platform_store, _memory_namespaces)


@app.middleware("http")
async def platform_request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "").strip()[:100] or uuid.uuid4().hex
    public_auth_paths = {"/api/auth/password/login", "/api/auth/email/send", "/api/auth/email/verify", "/api/auth/refresh"}
    requires_auth = (request.url.path.startswith("/api/") and request.url.path not in public_auth_paths) or request.url.path == "/mcp" or request.url.path.startswith("/mcp/")
    context = None
    mcp_context_token = None
    if requires_auth:
        if not auth_token:
            return JSONResponse(status_code=503, content={"detail": "服务端尚未配置 IDEA_AUTH_TOKEN"}, headers={"X-Request-ID": request_id})
        token = extract_bearer(request)
        device_id = request.headers.get("X-Device-ID", "").strip()[:100] or None
        mcp_capability = None
        if request.url.path == "/mcp/memory" or request.url.path.startswith("/mcp/memory/"):
            mcp_capability = "memory"
        elif request.url.path == "/mcp/idea" or request.url.path.startswith("/mcp/idea/"):
            mcp_capability = "idea"
        if token and token.startswith("mcp_"):
            if request.url.path == "/api/platform/rag/authorize":
                principal, credential_space_id = platform_store.authenticate_rag_owner_mcp_credential(token)
            else:
                principal, credential_space_id = platform_store.authenticate_mcp_credential(token, mcp_capability) if mcp_capability else (None, None)
        else:
            principal = platform_store.authenticate(token, device_id) if token else None
            credential_space_id = None
        if not principal:
            platform_store.write_audit(
                "authentication.failed",
                request_id=request_id,
                action=request.method,
                decision="denied",
                reason_code="invalid_token",
                metadata={"path": request.url.path},
            )
            return JSONResponse(status_code=401, content={"detail": "需要有效的 Bearer Token"}, headers={"X-Request-ID": request_id})
        requested_space_id = request.headers.get("X-Space-ID", "").strip()[:100] or None
        space_id = credential_space_id or platform_store.resolve_space(principal.principal_id, requested_space_id)
        if not space_id:
            platform_store.write_audit(
                "authorization.denied",
                request_id=request_id,
                principal_id=principal.principal_id,
                account_id=principal.account_id,
                action=request.method,
                decision="denied",
                reason_code="space_access_denied",
                resource_type="space",
                resource_id=requested_space_id,
                metadata={"path": request.url.path},
            )
            return JSONResponse(status_code=403, content={"detail": "无权访问指定空间"}, headers={"X-Request-ID": request_id})
        context = RequestContext(
            request_id=request_id,
            principal=principal,
            device_id=principal.device_id,
            space_id=space_id,
        )
        request.state.context = context
        if mcp_capability == "memory":
            mcp_context_token = (memory_mcp.request_context, memory_mcp.request_context.set(context))
        elif mcp_capability == "idea":
            mcp_context_token = (owner_agent_mcp.request_context, owner_agent_mcp.request_context.set(context))
        platform_store.write_audit(
            "authentication.succeeded",
            context,
            action=request.method,
            decision="allowed",
            metadata={"path": request.url.path},
        )
        platform_store.write_audit(
            "authorization.allowed",
            context,
            action=request.method,
            decision="allowed",
            resource_type="space",
            resource_id=space_id,
            metadata={"path": request.url.path},
        )
    try:
        response = await call_next(request)
    except Exception:
        if context:
            platform_store.write_audit(
                "request.failed",
                context,
                action=request.method,
                decision="error",
                metadata={"path": request.url.path},
            )
        raise
    finally:
        if mcp_context_token is not None:
            mcp_context_token[0].reset(mcp_context_token[1])
    if context:
        platform_store.write_audit(
            "request.completed",
            context,
            action=request.method,
            decision="allowed" if response.status_code < 400 else "error",
            metadata={"path": request.url.path, "status_code": response.status_code},
        )
    response.headers["X-Request-ID"] = request_id
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mcp/memory", memory_mcp.app)
app.mount("/mcp/idea", owner_agent_mcp.app)

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ===================================================================
# 账户认证
# ===================================================================
@app.post("/api/auth/password/login")
async def password_login(request: Request):
    body = await request.json()
    email, password = body.get("email"), body.get("password")
    device_id = request.headers.get("X-Device-ID", "").strip()[:100] or None
    if not isinstance(email, str) or not isinstance(password, str) or not (3 <= len(email.strip()) <= 254) or not (1 <= len(password) <= 1024):
        raise HTTPException(status_code=400, detail="邮箱或密码格式无效")
    try:
        principal, session, route = platform_store.authenticate_password_login(email, password, device_id)
    except ValueError as error:
        platform_store.write_audit("authentication.password_failed", request_id=request.headers.get("X-Request-ID"), device_id=device_id, action="sign_in", decision="denied", reason_code="invalid_credentials")
        raise HTTPException(status_code=429 if str(error).startswith("登录尝试过于频繁") else 401, detail=str(error))
    platform_store.write_audit("authentication.password_succeeded", request_id=request.headers.get("X-Request-ID"), principal_id=principal.principal_id, account_id=principal.account_id, device_id=device_id, action="sign_in", decision="allowed", metadata={"route": route})
    return {**session, "principal": {"principal_id": principal.principal_id, "account_id": principal.account_id, "role": principal.role}, "route": route}


@app.post("/api/auth/email/send")
async def send_email_verification(request: Request):
    raise HTTPException(status_code=410, detail="邮箱验证码登录已停用")


@app.post("/api/auth/email/verify")
async def verify_email_login(request: Request):
    raise HTTPException(status_code=410, detail="邮箱验证码登录已停用")


@app.post("/api/auth/refresh")
async def refresh_login(request: Request):
    body = await request.json()
    refresh_token = body.get("refresh_token", "")
    device_id = request.headers.get("X-Device-ID", "").strip()[:100] or None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=400, detail="缺少刷新令牌")
    try:
        principal, session = platform_store.refresh_session(refresh_token, device_id)
    except ValueError as error:
        raise HTTPException(status_code=401, detail=str(error))
    authenticated = platform_store.authenticate(session["access_token"], device_id)
    return {**session, "principal": {"principal_id": principal.principal_id, "account_id": principal.account_id, "role": principal.role}, "route": platform_store.route_for_principal(authenticated) if authenticated else "idea_assistant"}


@app.post("/api/auth/logout")
async def logout(request: Request):
    body = await request.json()
    refresh_token = body.get("refresh_token", "")
    if isinstance(refresh_token, str) and refresh_token:
        platform_store.revoke_session(refresh_token)
    return {"status": "logged_out"}


@app.post("/api/platform/owner/link-email")
async def link_owner_email(request: Request):
    context = require_context(request)
    if context.principal.principal_id != "principal-owner":
        raise HTTPException(status_code=403, detail="仅部署引导身份可以关联 Owner 邮箱")
    body = await request.json()
    email = body.get("email", "")
    try:
        linked = platform_store.link_owner_account(email)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not linked:
        raise HTTPException(status_code=404, detail="该邮箱尚未完成首次登录")
    platform_store.write_audit("owner.account_linked", context, action="link_email", decision="allowed", metadata={"email": platform_store.normalize_email(email)})
    return {"status": "linked"}


def _require_owner_controller(context: RequestContext) -> None:
    if not platform_store.is_owner_controller(context.principal):
        raise HTTPException(status_code=403, detail="需要已批准的私有设备")


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@app.get("/api/platform/owner/devices")
async def list_owner_devices(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    return {"devices": platform_store.list_owner_devices(context.principal)}


@app.post("/api/platform/owner/devices/{owner_device_id}/approve")
async def approve_owner_device(owner_device_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not platform_store.approve_owner_device(context.principal, owner_device_id):
        raise HTTPException(status_code=404, detail="待批准设备不存在")
    platform_store.write_audit("owner.device_approved", context, action="approve", resource_type="owner_device", resource_id=owner_device_id, decision="allowed")
    return {"status": "approved"}


@app.post("/api/platform/owner/devices/{owner_device_id}/revoke")
async def revoke_owner_device(owner_device_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not platform_store.revoke_owner_device(context.principal, owner_device_id):
        raise HTTPException(status_code=404, detail="设备不存在或无法撤销")
    platform_store.write_audit("owner.device_revoked", context, action="revoke", resource_type="owner_device", resource_id=owner_device_id, decision="allowed")
    return {"status": "revoked"}


@app.get("/api/platform/owner/credentials")
async def list_automated_device_credentials(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    return {"credentials": platform_store.list_automated_device_credentials(context.principal)}


@app.post("/api/platform/owner/credentials/issue")
async def issue_automated_device_credential(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    expires_in_days = body.get("expires_in_days")
    try:
        expires_at = time.time() + float(expires_in_days) * 86400 if expires_in_days is not None else None
        credential = platform_store.create_mcp_credential(
            context.principal,
            context.space_id,
            body.get("device_label", ""),
            capability=body.get("capability", "idea"),
            expires_at=expires_at,
            credential_kind="automated_device",
        )
    except (TypeError, ValueError, PermissionError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    platform_store.write_audit(
        "owner.automated_device_credential_issued",
        context,
        action="issue",
        resource_type="automated_device_credential",
        resource_id=credential["credential_id"],
        decision="allowed",
        metadata={"device_label": credential["device_label"], "capability": credential["capability"]},
    )
    return credential


@app.get("/api/platform/owner/credentials/{credential_id}/token")
async def recover_automated_device_credential(credential_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    token = platform_store.recover_automated_device_credential(context.principal, credential_id)
    if not token:
        raise HTTPException(status_code=404, detail="凭据不存在、已撤销或签发时未启用托管恢复")
    platform_store.write_audit(
        "owner.automated_device_credential_recovered",
        context,
        action="recover",
        resource_type="automated_device_credential",
        resource_id=credential_id,
        decision="allowed",
    )
    return {"credential_id": credential_id, "token": token}


@app.post("/api/platform/owner/credentials/{credential_id}/revoke")
async def revoke_automated_device_credential(credential_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not platform_store.revoke_automated_device_credential(context.principal, credential_id):
        raise HTTPException(status_code=404, detail="自动设备凭据不存在或已撤销")
    platform_store.write_audit(
        "owner.automated_device_credential_revoked",
        context,
        action="revoke",
        resource_type="automated_device_credential",
        resource_id=credential_id,
        decision="allowed",
    )
    return {"status": "revoked"}


@app.get("/api/platform/owner/mcp-credentials")
async def list_mcp_credentials(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    return {"credentials": platform_store.list_mcp_credentials(context.principal)}


@app.post("/api/platform/owner/mcp-credentials")
async def create_mcp_credential(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    try:
        credential = platform_store.create_mcp_credential(
            context.principal,
            context.space_id,
            body.get("device_label", ""),
            capability=body.get("capability", "memory"),
            expires_at=body.get("expires_at"),
        )
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=403, detail=str(error))
    platform_store.write_audit(
        "mcp.credential_created",
        context,
        action="create",
        resource_type="mcp_credential",
        resource_id=credential["credential_id"],
        decision="allowed",
        metadata={"device_label": credential["device_label"], "capability": credential["capability"]},
    )
    return credential


@app.post("/api/platform/owner/mcp-credentials/{credential_id}/revoke")
async def revoke_mcp_credential(credential_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not platform_store.revoke_mcp_credential(context.principal, credential_id):
        raise HTTPException(status_code=404, detail="MCP 凭据不存在或已撤销")
    platform_store.write_audit(
        "mcp.credential_revoked",
        context,
        action="revoke",
        resource_type="mcp_credential",
        resource_id=credential_id,
        decision="allowed",
    )
    return {"status": "revoked"}


@app.get("/api/platform/account-role")
async def get_account_role(request: Request, account_id: str):
    context = require_context(request)
    _require_owner_controller(context)
    role = platform_store.get_account_role(account_id)
    if role is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    return {"account_id": account_id, "work_role": role}


@app.put("/api/platform/account-role")
async def set_account_role(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    account_id, work_role = body.get("account_id"), body.get("work_role")
    if not isinstance(account_id, str) or not VALID_ID.fullmatch(account_id) or work_role not in {"owner", "researcher", "novelist", "user"}:
        raise HTTPException(status_code=400, detail="账户角色输入无效")
    if work_role == "owner" and account_id != context.principal.account_id:
        raise HTTPException(status_code=403, detail="不能将其他账户设为 owner")
    try:
        platform_store.set_account_role(account_id, work_role)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    except LookupError:
        raise HTTPException(status_code=404, detail="账户不存在")
    return {"account_id": account_id, "work_role": work_role}


@app.get("/api/platform/projects")
async def list_projects(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    return {"projects": platform_store.list_projects()}


@app.post("/api/platform/projects")
async def create_project(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    name, project_type, status = body.get("name"), body.get("project_type"), body.get("status", "active")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 200) or project_type not in {"research", "novel", "general"} or not isinstance(status, str) or not status.strip():
        raise HTTPException(status_code=400, detail="项目输入无效")
    return platform_store.create_project(name.strip(), project_type, context.principal.principal_id, status.strip()[:80])


@app.get("/api/platform/projects/{project_id}/members")
async def list_project_members(project_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(project_id):
        raise HTTPException(status_code=400, detail="project_id 格式无效")
    return {"members": platform_store.list_project_members(project_id)}


@app.put("/api/platform/projects/{project_id}/members")
async def set_project_member(project_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    principal_id, permissions = body.get("principal_id"), body.get("permissions")
    if not VALID_ID.fullmatch(project_id) or not isinstance(principal_id, str) or not VALID_ID.fullmatch(principal_id):
        raise HTTPException(status_code=400, detail="项目成员输入无效")
    try:
        return platform_store.set_project_member(project_id, principal_id, permissions)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))


@app.get("/api/platform/daily-memories")
async def list_daily_memories(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    memories = platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        ["daily/owner-shiroha-nao"],
        limit=100,
    )
    return {"count": len(memories), "memories": memories}


@app.post("/api/platform/daily-memories")
async def create_daily_memory(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    content, category, confidence, retention = body.get("content"), body.get("category"), body.get("confidence"), body.get("retention")
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= MAX_MEMORY_LENGTH) or not isinstance(category, str) or not (1 <= len(category.strip()) <= 80) or confidence not in {"verified", "hypothesis"} or retention not in {"short", "long"}:
        raise HTTPException(status_code=400, detail="daily memory 输入无效")
    return platform_store.create_memory("account-owner", context.space_id, "daily/owner-shiroha-nao", f"{category.strip()} [{confidence}/{retention}]", content.strip(), context.principal.principal_id)


@app.delete("/api/platform/daily-memories/{memory_id}")
async def delete_daily_memory(memory_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(memory_id):
        raise HTTPException(status_code=400, detail="memory_id 格式无效")
    body = await request.json()
    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 1:
        raise HTTPException(status_code=400, detail="expected_revision 必须是正整数")
    try:
        deleted, current_revision = platform_store.delete_memory("account-owner", context.space_id, memory_id, ["daily/owner-shiroha-nao"], expected_revision, context.principal.principal_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if current_revision is not None:
        raise HTTPException(status_code=409, detail="记忆已被其他成员修改，请刷新后重试", headers={"X-Memory-Revision": str(current_revision)})
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    platform_store.write_audit("memory.deleted", context, resource_type="memory", resource_id=memory_id, action="delete", decision="allowed", metadata={"namespace": "daily/owner-shiroha-nao", "revision": expected_revision + 1})
    return {"id": memory_id, "status": "deleted", "revision": expected_revision + 1}


@app.post("/api/platform/daily-memories/cleanup")
async def cleanup_daily_memories(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    deleted_count = platform_store.cleanup_daily_short_memories(
        "account-owner",
        context.space_id,
        "daily/owner-shiroha-nao",
        time.time() - DAILY_SHORT_RETENTION_SECONDS,
        context.principal.principal_id,
    )
    platform_store.write_audit("daily_memory.cleanup", context, action="cleanup", decision="allowed", metadata={"deleted_count": deleted_count})
    return {"deleted_count": deleted_count}


@app.post("/api/platform/rag/authorize")
async def rag_authorize(request: Request):
    rag_service_token = os.environ.get("RAG_IDEA_SERVICE_TOKEN", "")
    provided = request.headers.get("X-RAG-Service-Token", "")
    if not rag_service_token or not provided or not hmac.compare_digest(rag_service_token, provided):
        raise HTTPException(status_code=401, detail="RAG 服务令牌无效")
    context = require_context(request)
    body = await request.json()
    project_id, permission = body.get("project_id", ""), body.get("permission", "")
    if not VALID_ID.fullmatch(project_id) or permission not in RAG_PERMISSIONS:
        raise HTTPException(status_code=400, detail="授权请求无效")
    if not platform_store.get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    allowed = platform_store.route_for_principal(context.principal) == "owner_idea" or platform_store.project_permission_allowed(project_id, context.principal.principal_id, permission)
    if not allowed:
        raise HTTPException(status_code=403, detail="无权执行该项目操作")
    platform_store.write_audit("rag.authorized", context, action="authorize", resource_type="project", resource_id=project_id, decision="allowed", metadata={"permission": permission})
    return {"authorized": True, "project_id": project_id, "permission": permission}


@app.get("/api/platform/approvals")
async def list_tool_approvals(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    pending = platform_store.list_tool_approvals(context.principal.account_id, context.space_id, status="pending", limit=50)
    recent = platform_store.list_tool_approvals(context.principal.account_id, context.space_id, limit=20)
    return {"pending": pending, "recent": recent}


@app.post("/api/platform/approvals/{approval_id}/approve")
async def approve_tool_approval(approval_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(approval_id):
        raise HTTPException(status_code=400, detail="approval_id 格式无效")
    updated = platform_store.decide_tool_approval(approval_id, context.principal.account_id, context.space_id, "approved", context.principal.principal_id)
    if not updated:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    platform_store.write_audit("tool_approval.decided", context, resource_type="tool_approval", resource_id=approval_id, action="approve", decision="allowed", metadata={"tool_name": updated["tool_name"], "status": updated["status"]})
    return updated


@app.post("/api/platform/approvals/{approval_id}/deny")
async def deny_tool_approval(approval_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(approval_id):
        raise HTTPException(status_code=400, detail="approval_id 格式无效")
    updated = platform_store.decide_tool_approval(approval_id, context.principal.account_id, context.space_id, "denied", context.principal.principal_id)
    if not updated:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    platform_store.write_audit("tool_approval.decided", context, resource_type="tool_approval", resource_id=approval_id, action="deny", decision="allowed", metadata={"tool_name": updated["tool_name"], "status": updated["status"]})
    return updated


@app.get("/api/platform/grants")
async def list_capability_grants(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    return {"grants": platform_store.list_capability_grants()}


@app.post("/api/platform/grants")
async def create_capability_grant(request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    body = await request.json()
    account_id, capability = body.get("account_id", ""), body.get("capability", "")
    workspace, expires_in_days = body.get("workspace", ""), body.get("expires_in_days")
    if not VALID_ID.fullmatch(account_id) or capability not in platform_store.VALID_GRANT_CAPABILITIES:
        raise HTTPException(status_code=400, detail="授权输入无效")
    if workspace and not VALID_ID.fullmatch(workspace):
        raise HTTPException(status_code=400, detail="workspace 无效")
    if expires_in_days is not None and (not isinstance(expires_in_days, int) or not 1 <= expires_in_days <= 3650):
        raise HTTPException(status_code=400, detail="expires_in_days 必须是 1-3650 的整数")
    try:
        grant = platform_store.create_capability_grant(account_id, context.principal.principal_id, capability, workspace or "", expires_in_days=expires_in_days)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    platform_store.write_audit("grant.created", context, resource_type="capability_grant", resource_id=grant["grant_id"], action="create", decision="allowed", metadata={"account_id": account_id, "capability": capability, "workspace": workspace})
    return grant


@app.post("/api/platform/grants/{grant_id}/revoke")
async def revoke_capability_grant(grant_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(grant_id):
        raise HTTPException(status_code=400, detail="grant_id 格式无效")
    updated = platform_store.revoke_capability_grant(grant_id, context.principal.principal_id)
    if not updated:
        raise HTTPException(status_code=404, detail="授权不存在")
    platform_store.write_audit("grant.revoked", context, resource_type="capability_grant", resource_id=grant_id, action="revoke", decision="allowed", metadata={"capability": updated["capability"]})
    return updated


@app.get("/api/platform/file-changes")
async def list_file_changes(request: Request, status: str = None, limit: int = 100):
    context = require_context(request)
    _require_owner_controller(context)
    limit = max(1, min(limit, 200))
    if status and status not in ("pending", "accepted", "reverted"):
        raise HTTPException(status_code=400, detail="status 无效")
    return {"changes": platform_store.list_file_change_reviews(context.principal.account_id, context.space_id, status, limit)}


@app.post("/api/platform/file-changes/{change_id}/accept")
async def accept_file_change(change_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(change_id):
        raise HTTPException(status_code=400, detail="change_id 格式无效")
    updated = platform_store.review_file_change(change_id, context.principal.account_id, context.space_id, "accepted", context.principal.principal_id)
    if not updated:
        raise HTTPException(status_code=404, detail="变更记录不存在")
    platform_store.write_audit("file_change.accepted", context, resource_type="file_change", resource_id=change_id, action="accept", decision="allowed", metadata={"tool_name": updated["tool_name"], "file_path": updated["file_path"]})
    return updated


@app.post("/api/platform/file-changes/{change_id}/revert")
async def revert_file_change(change_id: str, request: Request):
    context = require_context(request)
    _require_owner_controller(context)
    if not VALID_ID.fullmatch(change_id):
        raise HTTPException(status_code=400, detail="change_id 格式无效")
    change = platform_store.get_file_change_review(change_id, context.principal.account_id, context.space_id)
    if not change:
        raise HTTPException(status_code=404, detail="变更记录不存在")
    if change["status"] != "pending":
        raise HTTPException(status_code=409, detail="该变更已处理")
    if not change.get("backup_path"):
        raise HTTPException(status_code=400, detail="该变更没有可回滚的备份")
    backup_root = (PROJECT_ROOT / ".idea-assistant" / "backup").resolve()
    backup = (backup_root / Path(change["backup_path"]).name).resolve()
    if not _path_within(backup, backup_root) or not backup.is_file():
        raise HTTPException(status_code=404, detail="备份文件缺失")
    target = Path(change["file_path"]).resolve()
    if not any(_path_within(target, Path(root)) for root in approved_workspaces):
        raise HTTPException(status_code=403, detail="目标路径不在允许的工作区内")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(backup.read_bytes())
    updated = platform_store.review_file_change(change_id, context.principal.account_id, context.space_id, "reverted", context.principal.principal_id)
    platform_store.write_audit("file_change.reverted", context, resource_type="file_change", resource_id=change_id, action="revert", decision="allowed", metadata={"tool_name": updated["tool_name"], "file_path": updated["file_path"]})
    return updated


# ===================================================================
# 核心 API：独立智能体会话（非流式）
# ===================================================================
@app.post("/api/assistant/chat")
async def chat_with_assistant(request: Request):
    context = require_context(request)
    body = await request.json()
    agent_id = _routed_agent(context, body.get("agent_id"))
    model_key = body.get("model_key", "gpt")
    if model_key not in VALID_MODEL_KEYS:
        raise HTTPException(status_code=400, detail="model_key 无效")
    model_config = selected_model_config(model_key)
    message = body.get("message", "")
    if not isinstance(message, str):
        raise HTTPException(status_code=400, detail="message 必须是字符串")
    message = message.strip()
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=413, detail=f"message 不能超过 {MAX_MESSAGE_LENGTH} 个字符")
    raw_conversation_id = body.get("conversation_id")
    conversation_id = _validate_conversation_id(raw_conversation_id) if raw_conversation_id else None

    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    conversation_id = _create_conversation(context, agent_id, conversation_id)

    logger.info("[%s:%s] 用户: %s", agent_id, conversation_id[:8], message[:150])

    platform_store.append_message(context.principal.account_id, context.space_id, conversation_id, "user", message)
    history = platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id, limit=MAX_HISTORY)

    # IDEA 可获知其他会话的最近摘要；L1 的上下文严格隔离。
    runner_message = message
    if agent_id == "idea":
        global_context = _global_context(context, conversation_id)
        if global_context:
            runner_message = f"以下是其他会话的最近摘要，仅作必要上下文：\n{global_context}\n\n当前用户请求：\n{message}"
    memory_context = _memory_context(context, message)
    if memory_context:
        runner_message = f"以下是用户明确保存的长期记忆，仅作必要上下文：\n{memory_context}\n\n当前用户请求：\n{runner_message}"

    execution_context = ExecutionContext(
        request_context=context,
        agent_id=agent_id,
        is_owner=platform_store.route_for_principal(context.principal) == "owner_idea",
    )
    result = await agent_runners[agent_id].run(
        user_message=runner_message,
        history=history[-10:],
        llm_model_config=model_config,
        execution_context=execution_context,
    )

    reply = result.get("reply", "抱歉，我暂时无法处理这个请求。")
    tool_calls_log = result.get("tool_calls_log", [])
    iterations = result.get("iterations", 1)
    _record_daily_activity(context, tool_calls_log)

    # 提取调度信息（如果有 dispatch_to_agent）
    dispatched_to = None
    dispatch_result = None
    for tc in tool_calls_log:
        if tc.get("name") == "dispatch_to_agent":
            dispatched_to = tc.get("args", {}).get("agent")
            dispatch_result = tc.get("result", "")[:200]

    # 记录回复
    platform_store.append_message(
        context.principal.account_id,
        context.space_id,
        conversation_id,
        "assistant",
        reply,
        {"dispatched_to": dispatched_to, "tool_calls": [tc["name"] for tc in tool_calls_log], "model_key": model_key},
    )

    return {
        "reply": reply,
        "agent_id": agent_id,
        "dispatched_to": dispatched_to,
        "tool_calls": [{"name": tc["name"], "success": tc["success"]} for tc in tool_calls_log],
        "iterations": iterations,
        "conversation_id": conversation_id,
    "model_key": model_key,
    }

# ===================================================================
# 会话管理
# ===================================================================
@app.post("/api/reset")
async def reset_conversation(request: Request):
    """重置对话"""
    body = await request.json()
    cid = _validate_conversation_id(body.get("conversation_id"))
    context = require_context(request)
    if not platform_store.reset_conversation(context.principal.account_id, context.space_id, cid):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "reset", "conversation_id": cid}


@app.get("/api/conversations")
async def list_conversations(request: Request):
    """列出会话元数据，不返回完整消息内容。"""
    context = require_context(request)
    visible = platform_store.list_conversations(context.principal.account_id, context.space_id)
    return {
        "count": len(visible),
        "conversations": [
            {
                "id": item["conversation_id"],
                "agent_id": item["agent_id"],
                "messages": item["message_count"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in visible
        ],
    }


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    conversation_id = _validate_conversation_id(conversation_id)
    context = require_context(request)
    if not platform_store.delete_conversation(context.principal.account_id, context.space_id, conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "deleted", "conversation_id": conversation_id}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    conversation_id = _validate_conversation_id(conversation_id)
    context = require_context(request)
    conversation = platform_store.get_conversation(context.principal.account_id, context.space_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"id": conversation_id, **conversation, "messages": platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id)}


@app.post("/api/conversations/new")
async def new_conversation(request: Request):
    context = require_context(request)
    body = await request.json()
    agent_id = _routed_agent(context, body.get("agent_id", "idea"))
    conversation_id = _create_conversation(context, agent_id)
    conversation = platform_store.get_conversation(context.principal.account_id, context.space_id, conversation_id)
    return {"id": conversation_id, "agent_id": agent_id, **conversation}


@app.get("/api/agents")
async def list_agents():
    return {"agents": [{"id": agent_id, **info, "tools": agent_runners[agent_id].tools.get_all_tool_names()} for agent_id, info in AGENTS.items()]}


@app.get("/api/platform/me")
async def platform_me(request: Request):
    context = require_context(request)
    return {
        "principal_id": context.principal.principal_id,
        "account_id": context.principal.account_id,
        "role": context.principal.role,
        "token_id": context.principal.token_id,
        "device_id": context.device_id,
        "route": platform_store.route_for_principal(context.principal),
    }


@app.get("/api/platform/spaces")
async def platform_spaces(request: Request):
    context = require_context(request)
    return {"spaces": platform_store.list_spaces(context.principal.principal_id)}


@app.get("/api/platform/audit")
async def platform_audit(request: Request, limit: int = 100):
    context = require_context(request)
    if context.principal.role != "owner":
        raise HTTPException(status_code=403, detail="仅所有者可以查询平台审计")
    limit = max(1, min(limit, 500))
    return {"events": platform_store.list_audit(context.principal.account_id, limit)}


@app.get("/api/workspaces")
async def list_workspaces():
    """Expose the small, user-approved workspace list to the desktop shell."""
    labels = {
        PROJECT_ROOT.resolve(): "Program IDEA",
        SHARED_RAG_ROOT: "Shared RAG",
        SHIROHA_ROOT: "Shiroha Personal Website",
    }
    return {
        "workspaces": [
            {"path": str(path), "label": labels.get(path, path.name)}
            for path in existing_workspace_paths()
        ]
    }


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    if not isinstance(task_id, str) or not VALID_ID.fullmatch(task_id):
        raise HTTPException(status_code=400, detail="task_id 格式无效")
    context = require_context(request)
    if not platform_store.delete_task(context.principal.account_id, context.space_id, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "deleted", "task_id": task_id}


@app.get("/api/tasks")
async def list_tasks(request: Request):
    context = require_context(request)
    visible = platform_store.list_tasks(context.principal.account_id, context.space_id)
    return {"count": len(visible), "tasks": sorted(visible, key=lambda task: task["created_at"], reverse=True)}


@app.post("/api/tasks")
async def create_task(request: Request):
    context = require_context(request)
    body = await request.json()
    title = body.get("title", "")
    if not isinstance(title, str) or not (1 <= len(title.strip()) <= 500):
        raise HTTPException(status_code=400, detail="title 必须为 1 到 500 个字符")
    agent_id = _routed_agent(context, body.get("agent_id", "idea"))
    conversation_id = body.get("conversation_id")
    if conversation_id is not None:
        conversation_id = _validate_conversation_id(conversation_id)
        if not platform_store.get_conversation(context.principal.account_id, context.space_id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")
    return platform_store.create_task(context.principal.account_id, context.space_id, agent_id, title.strip(), str(body.get("description", ""))[:5000], conversation_id)


@app.get("/api/memories")
async def list_memories(request: Request, query: Optional[str] = None, limit: int = 50):
    context = require_context(request)
    if query is not None and not isinstance(query, str):
        raise HTTPException(status_code=400, detail="query 必须是字符串")
    limit = max(1, min(limit, 100))
    memories = platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        list(_memory_namespaces(context).values()),
        query=query.strip() if query else None,
        limit=limit,
    )
    return {"count": len(memories), "memories": memories}


@app.post("/api/memories")
async def create_memory(request: Request):
    context = require_context(request)
    body = await request.json()
    if body.get("confirmed") is not True:
        raise HTTPException(status_code=400, detail="长期记忆写入需要明确确认")
    scope, namespace = _memory_scope(context, body.get("scope", "personal"))
    if scope == "project" and not platform_store.memory_write_allowed(context.principal.principal_id, context.space_id, f"shared/{context.space_id}"):
        raise HTTPException(status_code=403, detail="无权写入项目记忆")
    content = body.get("content", "")
    category = body.get("category", "general")
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= MAX_MEMORY_LENGTH):
        raise HTTPException(status_code=400, detail=f"content 必须为 1 到 {MAX_MEMORY_LENGTH} 个字符")
    if not isinstance(category, str) or not (1 <= len(category.strip()) <= 80):
        raise HTTPException(status_code=400, detail="category 必须为 1 到 80 个字符")
    memory = platform_store.create_memory(context.principal.account_id, context.space_id, namespace, category.strip(), content.strip(), context.principal.principal_id)
    platform_store.write_audit("memory.created", context, resource_type="memory", resource_id=memory["id"], action="create", decision="allowed", metadata={"scope": scope, "category": memory["category"]})
    return memory


@app.put("/api/memories/{memory_id}")
async def update_memory(memory_id: str, request: Request):
    context = require_context(request)
    if not VALID_ID.fullmatch(memory_id):
        raise HTTPException(status_code=400, detail="memory_id 格式无效")
    body = await request.json()
    content = body.get("content", "")
    category = body.get("category", "general")
    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 1:
        raise HTTPException(status_code=400, detail="expected_revision 必须是正整数")
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= MAX_MEMORY_LENGTH):
        raise HTTPException(status_code=400, detail=f"content 必须为 1 到 {MAX_MEMORY_LENGTH} 个字符")
    if not isinstance(category, str) or not (1 <= len(category.strip()) <= 80):
        raise HTTPException(status_code=400, detail="category 必须为 1 到 80 个字符")
    try:
        memory, current_revision = platform_store.update_memory(context.principal.account_id, context.space_id, memory_id, list(_memory_namespaces(context).values()), category.strip(), content.strip(), expected_revision, context.principal.principal_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if current_revision is not None:
        raise HTTPException(status_code=409, detail="记忆已被其他成员修改，请刷新后重试", headers={"X-Memory-Revision": str(current_revision)})
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    platform_store.write_audit("memory.updated", context, resource_type="memory", resource_id=memory_id, action="update", decision="allowed", metadata={"category": memory["category"]})
    return memory


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request):
    context = require_context(request)
    if not VALID_ID.fullmatch(memory_id):
        raise HTTPException(status_code=400, detail="memory_id 格式无效")
    body = await request.json()
    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 1:
        raise HTTPException(status_code=400, detail="expected_revision 必须是正整数")
    try:
        deleted, current_revision = platform_store.delete_memory(context.principal.account_id, context.space_id, memory_id, list(_memory_namespaces(context).values()), expected_revision, context.principal.principal_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if current_revision is not None:
        raise HTTPException(status_code=409, detail="记忆已被其他成员修改，请刷新后重试", headers={"X-Memory-Revision": str(current_revision)})
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    platform_store.write_audit("memory.deleted", context, resource_type="memory", resource_id=memory_id, action="delete", decision="allowed", metadata={"revision": expected_revision + 1})
    return {"id": memory_id, "status": "deleted", "revision": expected_revision + 1}


@app.get("/api/sync/events")
async def sync_events(request: Request, after: int = 0, limit: int = 200):
    context = require_context(request)
    after = max(0, after)
    limit = max(1, min(limit, 500))
    events = platform_store.list_sync_events(context.principal.account_id, context.space_id, after, limit)
    return {"events": events, "next_cursor": events[-1]["event_id"] if events else after}


# ===================================================================
# MCP 协议端点（兼容 TRAE 连接）
# ===================================================================
@app.get("/mcp")
async def mcp_discover():
    return {
        "name": "idea-standalone-orchestrator",
        "version": "3.0.0",
        "description": "IDEA 主 Agent — LLM 推理 + 工具调用 + 下级智能体调度",
        "tools": idea_tool_registry.get_all_tool_names(),
        "sub_agents": ["pwa", "researcher", "agent_producer"],
    }


@app.post("/mcp/tools/list")
async def mcp_list_tools():
    """列出所有 MCP 工具"""
    schemas = idea_tool_registry.get_all_schemas()
    return {
        "tools": [
            {
                "name": s["name"],
                "description": s["description"],
                "inputSchema": s["parameters"],
            }
            for s in schemas
        ]
    }


@app.post("/mcp/tools/call")
async def mcp_call_tool(request: Request):
    """Legacy direct tool execution endpoint is permanently closed."""
    raise HTTPException(status_code=410, detail="直连工具调用已关闭；请使用受控 MCP 端点。")


# ===================================================================
# 健康检查 & 系统信息
# ===================================================================
@app.get("/health")
async def health():
    llm_available = llm_client.has_api_key()
    return {
        "status": "healthy",
        "service": "IDEA — Intelligent Delegation & Executive Architect",
        "version": "3.0.0",
        "llm_available": llm_available,
        "timestamp": datetime.now().isoformat(),
        "tools": {
            "count": len(idea_tool_registry.get_all_tool_names()),
            "names": idea_tool_registry.get_all_tool_names(),
        },
        "sub_agents": [
            {"id": "pwa", "name": "IDEA-ProgramWorldAdminister", "role": "项目管理"},
            {"id": "researcher", "name": "IDEA-Reasearcher", "role": "科研分析"},
            {"id": "agent_producer", "name": "IDEA-AgentProducer", "role": "智能体创建"},
        ],
        "active_conversations": platform_store.count_active_conversations(),
        "workspace": str(idea_tool_registry.workspace),
    }


# ===================================================================
# 根路径
# ===================================================================
@app.get("/")
async def index():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": "IDEA — Intelligent Delegation & Executive Architect",
        "version": "3.0.0",
        "llm_available": llm_client.has_api_key(),
        "web_ui": f"http://localhost:{config.get('server', {}).get('port', 8900)}",
        "api_docs": "/docs",
        "endpoints": {
            "assistant_chat": "POST /api/assistant/chat",
            "agents": "GET /api/agents",
            "tasks": "GET/POST /api/tasks",
            "conversations": "GET /api/conversations",
            "health": "GET /health",
            "mcp": "GET /mcp",
            "mcp_tools": "POST /mcp/tools/list",
            "mcp_call": "POST /mcp/tools/call",
        },
    }


# ===================================================================
# 启动
# ===================================================================
if __name__ == "__main__":
    import uvicorn
    host = config.get("server", {}).get("host", "0.0.0.0")
    port = config.get("server", {}).get("port", 8900)

    has_key = llm_client.has_api_key()
    provider = llm_client.default_provider
    model = llm_client.default_model

    logger.info("=" * 50)
    logger.info("  IDEA 智能体调度中心 v3.0")
    logger.info(f"  LLM: {provider} / {model}")
    logger.info(f"  LLM 状态: {'已配置' if has_key else '未配置 API Key（模板模式）'}")
    logger.info(f"  IDEA 工具数: {len(idea_tool_registry.get_all_tool_names())}")
    logger.info(f"  访问: http://localhost:{port}")
    logger.info(f"  API 文档: http://localhost:{port}/docs")
    logger.info("=" * 50)

    uvicorn.run(app, host=host, port=port, log_level="info")
