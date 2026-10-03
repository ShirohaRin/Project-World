import contextvars
import hashlib
import json
import re
import time

from mcp.server.fastmcp import FastMCP


request_context = contextvars.ContextVar("owner_agent_mcp_context", default=None)
platform_store = None
agent_runner = None
create_conversation = None
memory_context = None
global_context = None
memory_namespaces = None
daily_activity_recorder = None
agent_model_resolver = None
agent_policy_resolver = None
prompt_metadata_resolver = None
max_history = 50
max_message_length = 20_000
max_memory_length = 10_000


def configure(store, runner, conversation_creator, memory_resolver, global_resolver, namespace_resolver, activity_recorder=None, model_resolver=None, policy_resolver=None, prompt_resolver=None):
    global platform_store, agent_runner, create_conversation, memory_context, global_context, memory_namespaces, daily_activity_recorder, agent_model_resolver, agent_policy_resolver, prompt_metadata_resolver
    platform_store = store
    agent_runner = runner
    create_conversation = conversation_creator
    memory_context = memory_resolver
    global_context = global_resolver
    memory_namespaces = namespace_resolver
    daily_activity_recorder = activity_recorder
    agent_model_resolver = model_resolver
    agent_policy_resolver = policy_resolver
    prompt_metadata_resolver = prompt_resolver


mcp = FastMCP("IDEA Owner Agent", stateless_http=True, json_response=True)


def _context():
    context = request_context.get()
    if (
        context is None
        or platform_store is None
        or agent_runner is None
        or create_conversation is None
        or memory_context is None
        or global_context is None
        or memory_namespaces is None
    ):
        raise RuntimeError("IDEA MCP 请求未完成授权")
    return context


@mcp.tool()
async def idea_chat(message: str, conversation_id: str | None = None, use_memory: bool = True) -> str:
    """向 Owner 专属云端 IDEA 发送消息，并返回可跨设备续接的会话 ID 与回复。"""
    if not isinstance(use_memory, bool):
        raise ValueError("use_memory 必须是布尔值")
    context = _context()
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 必须是非空字符串")
    message = message.strip()
    if len(message) > max_message_length:
        raise ValueError(f"message 不能超过 {max_message_length} 个字符")
    if conversation_id is not None and (not isinstance(conversation_id, str) or len(conversation_id) > 100):
        raise ValueError("conversation_id 格式无效")
    if agent_model_resolver is None:
        raise RuntimeError("IDEA Registry 模型策略未配置")
    model = agent_model_resolver("idea")
    if agent_policy_resolver is None:
        raise RuntimeError("IDEA Registry 工具策略未配置")
    policy = agent_policy_resolver("idea")
    if prompt_metadata_resolver is None:
        raise RuntimeError("IDEA Registry 提示词策略未配置")
    prompt_meta = prompt_metadata_resolver("idea")

    conversation_id = create_conversation(context, "idea", conversation_id)
    platform_store.append_message(context.principal.account_id, context.space_id, conversation_id, "user", message)
    history = platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id, limit=max_history)
    runner_message = message
    other_context = global_context(context, conversation_id)
    if other_context:
        runner_message = f"以下是其他会话的最近摘要，仅作必要上下文：\n{other_context}\n\n当前用户请求：\n{runner_message}"
    saved_context = memory_context(context, message, policy["memory_scopes"]) if use_memory else ""
    if saved_context:
        runner_message = f"以下是用户明确保存的长期记忆，仅作必要上下文：\n{saved_context}\n\n当前用户请求：\n{runner_message}"

    from tool_runtime.permissions import ExecutionContext

    snapshot = platform_store.create_runtime_snapshot(
        context.principal.account_id,
        context.space_id,
        conversation_id,
        {
            "agent_id": "idea",
            "model_key": model["model_key"],
            "prompt_version": prompt_meta["prompt_version"],
            "prompt_hash": prompt_meta["prompt_hash"],
            "history_message_count": len(history),
            "context_block_count": 0,
            "context_block_tokens": 0,
            "memory_count": saved_context.count("\n") + 1 if saved_context else 0,
            "memory_tokens": len(saved_context) if saved_context else 0,
        },
    )
    run = platform_store.create_agent_run(
        context.principal.account_id,
        context.space_id,
        conversation_id,
        "idea",
        snapshot["id"],
        model["model_key"],
        prompt_version=prompt_meta["prompt_version"],
        prompt_hash=prompt_meta["prompt_hash"],
    )
    try:
        result = await agent_runner.run(
            user_message=runner_message,
            history=history[-10:],
            llm_model_config=model["config"],
            execution_context=ExecutionContext(
                context,
                "idea",
                is_owner=True,
                conversation_id=conversation_id,
                tool_capabilities=frozenset(policy["tools"]),
                registry_version=policy["version"],
                prompt_version=prompt_meta["prompt_version"],
                prompt_text=prompt_meta["prompt"],
            ),
        )
    except Exception as error:
        platform_store.fail_agent_run(
            context.principal.account_id,
            context.space_id,
            run["id"],
            type(error).__name__,
        )
        raise
    reply = result.get("reply", "抱歉，我暂时无法处理这个请求。")
    tool_calls_log = result.get("tool_calls_log", [])
    if tool_calls_log and daily_activity_recorder is not None:
        daily_activity_recorder(context, tool_calls_log)
    platform_store.append_message(
        context.principal.account_id,
        context.space_id,
        conversation_id,
        "assistant",
        reply,
        {"tool_calls": [item["name"] for item in tool_calls_log]},
    )
    platform_store.complete_agent_run(
        context.principal.account_id,
        context.space_id,
        run["id"],
        reply,
        result.get("iterations", 1),
        tool_calls_log,
    )
    platform_store.write_audit(
        "mcp.idea_chat",
        context,
        action="idea_chat",
        resource_type="conversation",
        resource_id=conversation_id,
        decision="allowed",
        metadata={"tool_call_count": len(tool_calls_log)},
    )
    return json.dumps(
        {
            "agent_id": "idea",
            "conversation_id": conversation_id,
            "run_id": run["id"],
            "reply": reply,
            "tool_calls": [{"name": item["name"], "success": item["success"]} for item in tool_calls_log],
            "iterations": result.get("iterations", 1),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def idea_task_handoff_create(conversation_id: str, target_runtime_id: str, relative_path: str, mode: str = "run", task_id: str | None = None) -> str:
    """为指定桌面 Runtime 创建待 Owner 审批的单文件交接请求；不会立即执行。"""
    context = _context()
    if not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 100:
        raise ValueError("conversation_id 格式无效")
    if not isinstance(target_runtime_id, str) or not target_runtime_id.strip() or len(target_runtime_id) > 128:
        raise ValueError("target_runtime_id 格式无效")
    if mode not in {"run", "debug"}:
        raise ValueError("mode 只支持 run 或 debug")
    if not isinstance(relative_path, str) or not relative_path.strip() or len(relative_path) > 240:
        raise ValueError("relative_path 格式无效")
    normalized_path = relative_path.strip().replace("\\", "/")
    if normalized_path.startswith("/") or ":" in normalized_path or any(part in {"", ".", ".."} for part in normalized_path.split("/")):
        raise ValueError("relative_path 必须是工作区内的相对路径")
    conversation = platform_store.get_conversation(context.principal.account_id, context.space_id, conversation_id)
    if not conversation or conversation["agent_id"] != "idea":
        raise ValueError("会话不存在或不是 IDEA 会话")
    runtime = next((item for item in platform_store.list_device_runtimes(context.principal.account_id, context.space_id) if item["id"] == target_runtime_id), None)
    if not runtime or runtime["status"] != "online" or not runtime["capabilities"].get("workspace") or not runtime["capabilities"].get("terminal"):
        raise ValueError("目标 Runtime 不可用于本地工作区执行")
    manifest = {"kind": "run_file", "relative_path": normalized_path, "mode": mode}
    manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    approval = platform_store.find_tool_approval(context.principal.account_id, context.space_id, context.principal.principal_id, "handoff.run_file", manifest_hash)
    if not approval:
        approval = platform_store.create_tool_approval(context.principal.account_id, context.space_id, context.principal.principal_id, "idea", "handoff.run_file", manifest_hash, f"运行工作区相对文件：{normalized_path}（{mode}）")
    if approval["status"] != "approved":
        platform_store.write_audit("mcp.idea_task_handoff_requested", context, action="request", resource_type="handoff", decision="pending", metadata={"conversation_id": conversation_id, "target_runtime_id": target_runtime_id, "manifest_hash": manifest_hash, "approval_id": approval["approval_id"]})
        return json.dumps({"status": "pending_approval", "approval_id": approval["approval_id"], "target_runtime_id": target_runtime_id, "manifest": manifest, "manifest_hash": manifest_hash}, ensure_ascii=False)
    snapshot = platform_store.create_runtime_snapshot(context.principal.account_id, context.space_id, conversation_id, {"agent_id": "idea", "source": "owner_mcp_handoff", "target_runtime_id": target_runtime_id, "execution_kind": "run_file", "execution_mode": mode, "manifest_hash": manifest_hash})
    handoff = platform_store.create_task_handoff(context.principal.account_id, context.space_id, conversation_id, "idea", snapshot["id"], "cloud_to_local", task_id, None, target_runtime_id, manifest, approval["approval_id"])
    platform_store.write_audit("mcp.idea_task_handoff_created", context, action="create", resource_type="handoff", resource_id=handoff["id"], decision="allowed", metadata={"target_runtime_id": target_runtime_id, "manifest_hash": manifest_hash, "approval_id": approval["approval_id"]})
    return json.dumps({"status": "pending", "handoff": handoff, "approval_id": approval["approval_id"], "manifest": manifest}, ensure_ascii=False)


_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9_]+")
_MEMORY_LOOKBACK_LIMIT = 500
_MEMORY_RECENCY_HALF_LIFE_DAYS = 180.0


def _memory_normalize(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _memory_tokens(text) -> set:
    normalized = _memory_normalize(text)
    tokens = {word for word in _LATIN_WORD.findall(normalized) if len(word) >= 2}
    for run in _CJK_RUN.findall(normalized):
        if len(run) <= 3:
            tokens.add(run)
        for size in (2, 3):
            for index in range(max(0, len(run) - size + 1)):
                tokens.add(run[index:index + size])
    return tokens


def _memory_score(query: str, query_tokens: set, memory: dict) -> float:
    content = _memory_normalize(memory.get("content"))
    category = _memory_normalize(memory.get("category"))
    raw_query = _memory_normalize(query)
    overlap = query_tokens & _memory_tokens(content)
    if not overlap:
        return 0.0
    score = sum(len(token) ** 1.2 for token in overlap)
    score /= 1.0 + 0.35 * max(0, len(query_tokens) - len(overlap))
    if raw_query in content:
        score *= 2.0
    if raw_query in category:
        score *= 1.5
    age_days = max(0.0, (time.time() - float(memory.get("updated_at") or 0.0)) / 86400.0)
    return score * (0.5 ** (age_days / _MEMORY_RECENCY_HALF_LIFE_DAYS))


def _owner_namespace(context) -> str:
    namespace = memory_namespaces(context).get("owner")
    if not namespace:
        raise PermissionError("当前身份无权访问 Owner 私域记忆")
    return namespace


def _owner_memories(context, category=None) -> list:
    memories = platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        [_owner_namespace(context)],
        limit=_MEMORY_LOOKBACK_LIMIT,
    )
    if isinstance(category, str) and category.strip():
        expected = category.strip().lower()
        memories = [item for item in memories if _memory_normalize(item.get("category")) == expected]
    return memories


def _memory_payload(memory: dict) -> dict:
    return {
        "id": memory["id"],
        "category": memory["category"],
        "content": memory["content"],
        "revision": memory["revision"],
        "updated_at": memory["updated_at"],
    }


@mcp.tool()
def idea_memory_save(content: str, category: str = "general") -> str:
    """将确认过的长期事项写入当前 Owner 私域记忆，供其他获批设备续接使用。内容完全相同则不会重复写入。"""
    context = _context()
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= max_memory_length):
        raise ValueError(f"content 必须为 1 到 {max_memory_length} 个字符")
    if not isinstance(category, str) or not (1 <= len(category.strip()) <= 80):
        raise ValueError("category 必须为 1 到 80 个字符")

    namespace = _owner_namespace(context)
    normalized = _memory_normalize(content)
    existing = next((item for item in _owner_memories(context) if _memory_normalize(item["content"]) == normalized), None)
    if existing is not None:
        payload = dict(existing)
        payload["deduplicated"] = True
        return json.dumps(payload, ensure_ascii=False)

    memory = platform_store.create_memory(
        context.principal.account_id,
        context.space_id,
        namespace,
        category.strip(),
        content.strip(),
        context.principal.principal_id,
        metadata={"source": "idea-owner-mcp"},
    )
    platform_store.write_audit(
        "mcp.idea_memory_saved",
        context,
        action="create",
        resource_type="memory",
        resource_id=memory["id"],
        decision="allowed",
        metadata={"category": memory["category"], "namespace": namespace},
    )
    payload = dict(memory)
    payload["deduplicated"] = False
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
def idea_memory_search(query: str, limit: int = 8, category: str | None = None) -> str:
    """按关键词检索 Owner 私域长期记忆。支持中文自然语言片段，按相关度和新鲜度排序，不调用 LLM。"""
    context = _context()
    if not isinstance(query, str) or not (1 <= len(query.strip()) <= 500):
        raise ValueError("query 必须为 1 到 500 个字符")
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")

    namespace = _owner_namespace(context)
    query = query.strip()
    query_tokens = _memory_tokens(query)
    scored = []
    for memory in _owner_memories(context, category):
        score = _memory_score(query, query_tokens, memory)
        if score > 0:
            scored.append((score, memory))
    scored.sort(key=lambda pair: (pair[0], float(pair[1].get("updated_at") or 0.0)), reverse=True)
    results = []
    for score, memory in scored[: max(1, min(limit, 50))]:
        item = _memory_payload(memory)
        item["score"] = round(score, 4)
        results.append(item)
    platform_store.write_audit(
        "mcp.idea_memory_searched",
        context,
        action="search",
        resource_type="memory",
        decision="allowed",
        metadata={"namespace": namespace, "result_count": len(results), "candidates": len(scored)},
    )
    return json.dumps({"count": len(results), "memories": results}, ensure_ascii=False)


@mcp.tool()
def idea_memory_list(category: str | None = None, limit: int = 20) -> str:
    """按更新时间列出 Owner 私域长期记忆，用于了解近期上下文，不做相关性排序。"""
    context = _context()
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")
    if category is not None and not isinstance(category, str):
        raise ValueError("category 必须是字符串")
    memories = _owner_memories(context, category)
    memories = sorted(memories, key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
    results = [_memory_payload(memory) for memory in memories[: max(1, min(limit, 100))]]
    return json.dumps({"count": len(results), "total": len(memories), "memories": results}, ensure_ascii=False)


@mcp.tool()
def idea_memory_update(memory_id: str, content: str, category: str | None = None) -> str:
    """修订一条已存在的 Owner 私域记忆；revision 自动取当前值，不需要手工传。"""
    context = _context()
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id 格式无效")
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= max_memory_length):
        raise ValueError(f"content 必须为 1 到 {max_memory_length} 个字符")
    if category is not None and not isinstance(category, str):
        raise ValueError("category 必须是字符串")

    namespace = _owner_namespace(context)
    current = next((item for item in _owner_memories(context) if item["id"] == memory_id.strip()), None)
    if current is None:
        raise ValueError("记忆不存在或无权访问")
    next_category = category.strip() if category and category.strip() else current["category"]
    memory, conflict = platform_store.update_memory(
        context.principal.account_id,
        context.space_id,
        current["id"],
        [namespace],
        next_category,
        content.strip(),
        current["revision"],
        context.principal.principal_id,
    )
    if conflict is not None or memory is None:
        raise RuntimeError("记忆已被其他设备修改，请重新检索后再更新")
    platform_store.write_audit(
        "mcp.idea_memory_updated",
        context,
        action="update",
        resource_type="memory",
        resource_id=current["id"],
        decision="allowed",
        metadata={"category": memory["category"], "revision": memory["revision"]},
    )
    return json.dumps(_memory_payload(memory), ensure_ascii=False)


@mcp.tool()
def idea_memory_delete(memory_id: str) -> str:
    """逻辑删除一条 Owner 私域记忆；保留审计与同步事件，不做物理清除。"""
    context = _context()
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id 格式无效")

    namespace = _owner_namespace(context)
    current = next((item for item in _owner_memories(context) if item["id"] == memory_id.strip()), None)
    if current is None:
        raise ValueError("记忆不存在或无权访问")
    deleted, conflict = platform_store.delete_memory(
        context.principal.account_id,
        context.space_id,
        current["id"],
        [namespace],
        current["revision"],
        context.principal.principal_id,
    )
    if conflict is not None or not deleted:
        raise RuntimeError("记忆已被其他设备修改，请重新检索后再删除")
    platform_store.write_audit(
        "mcp.idea_memory_deleted",
        context,
        action="delete",
        resource_type="memory",
        resource_id=current["id"],
        decision="allowed",
        metadata={"category": current["category"]},
    )
    return json.dumps({"id": current["id"], "status": "deleted", "revision": current["revision"] + 1}, ensure_ascii=False)


@mcp.tool()
def idea_session_get(conversation_id: str, limit: int = 20) -> str:
    """读取当前 Owner 空间内一段可跨设备续接的 IDEA 会话。"""
    context = _context()
    if not isinstance(conversation_id, str) or not conversation_id.strip() or len(conversation_id) > 100:
        raise ValueError("conversation_id 格式无效")
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")
    conversation = platform_store.get_conversation(context.principal.account_id, context.space_id, conversation_id)
    if not conversation or conversation["agent_id"] != "idea":
        raise ValueError("会话不存在或无权访问")
    messages = platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id, limit=max(1, min(limit, 50)))
    return json.dumps({"conversation_id": conversation_id, "agent_id": "idea", "messages": messages}, ensure_ascii=False)


@mcp.tool()
def idea_task_status(limit: int = 20) -> str:
    """读取当前 Owner 空间的 IDEA 任务状态，不执行或派发设备动作。"""
    context = _context()
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")
    tasks = platform_store.list_tasks(context.principal.account_id, context.space_id)
    return json.dumps({"tasks": tasks[:max(1, min(limit, 50))]}, ensure_ascii=False)


app = mcp.streamable_http_app()
