import contextvars
import json

from mcp.server.fastmcp import FastMCP


request_context = contextvars.ContextVar("owner_agent_mcp_context", default=None)
platform_store = None
agent_runner = None
create_conversation = None
memory_context = None
global_context = None
memory_namespaces = None
daily_activity_recorder = None
max_history = 50
max_message_length = 20_000
max_memory_length = 10_000


def configure(store, runner, conversation_creator, memory_resolver, global_resolver, namespace_resolver, activity_recorder=None):
    global platform_store, agent_runner, create_conversation, memory_context, global_context, memory_namespaces, daily_activity_recorder
    platform_store = store
    agent_runner = runner
    create_conversation = conversation_creator
    memory_context = memory_resolver
    global_context = global_resolver
    memory_namespaces = namespace_resolver
    daily_activity_recorder = activity_recorder


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

    conversation_id = create_conversation(context, "idea", conversation_id)
    platform_store.append_message(context.principal.account_id, context.space_id, conversation_id, "user", message)
    history = platform_store.list_messages(context.principal.account_id, context.space_id, conversation_id, limit=max_history)
    runner_message = message
    other_context = global_context(context, conversation_id)
    if other_context:
        runner_message = f"以下是其他会话的最近摘要，仅作必要上下文：\n{other_context}\n\n当前用户请求：\n{runner_message}"
    saved_context = memory_context(context, message)
    if saved_context:
        runner_message = f"以下是用户明确保存的长期记忆，仅作必要上下文：\n{saved_context}\n\n当前用户请求：\n{runner_message}"

    from tool_runtime.permissions import ExecutionContext

    result = await agent_runner.run(
        user_message=runner_message,
        history=history[-10:],
        execution_context=ExecutionContext(context, "idea", is_owner=True),
    )
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
            "reply": reply,
            "tool_calls": [{"name": item["name"], "success": item["success"]} for item in tool_calls_log],
            "iterations": result.get("iterations", 1),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def idea_memory_save(content: str, category: str = "general") -> str:
    """将确认过的长期事项写入当前 Owner 私域记忆，供其他获批设备续接使用。"""
    context = _context()
    if not isinstance(content, str) or not (1 <= len(content.strip()) <= max_memory_length):
        raise ValueError(f"content 必须为 1 到 {max_memory_length} 个字符")
    if not isinstance(category, str) or not (1 <= len(category.strip()) <= 80):
        raise ValueError("category 必须为 1 到 80 个字符")

    namespaces = memory_namespaces(context)
    namespace = namespaces.get("owner")
    if not namespace:
        raise PermissionError("当前身份无权写入 Owner 私域记忆")
    memory = platform_store.create_memory(
        context.principal.account_id,
        context.space_id,
        namespace,
        category.strip(),
        content.strip(),
        context.principal.principal_id,
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
    return json.dumps(memory, ensure_ascii=False)


@mcp.tool()
def idea_memory_search(query: str, limit: int = 10) -> str:
    """检索当前 Owner 私域长期记忆，不调用 LLM 或执行智能体任务。"""
    context = _context()
    if not isinstance(query, str) or not (1 <= len(query.strip()) <= 500):
        raise ValueError("query 必须为 1 到 500 个字符")
    if not isinstance(limit, int):
        raise ValueError("limit 必须是整数")

    namespace = memory_namespaces(context).get("owner")
    if not namespace:
        raise PermissionError("当前身份无权读取 Owner 私域记忆")
    memories = platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        [namespace],
        query=query.strip(),
        limit=max(1, min(limit, 50)),
    )
    platform_store.write_audit(
        "mcp.idea_memory_searched",
        context,
        action="search",
        resource_type="memory",
        decision="allowed",
        metadata={"namespace": namespace, "result_count": len(memories)},
    )
    return json.dumps({"count": len(memories), "memories": memories}, ensure_ascii=False)


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
