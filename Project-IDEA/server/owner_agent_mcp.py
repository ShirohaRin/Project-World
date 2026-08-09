import contextvars
import json

from mcp.server.fastmcp import FastMCP


request_context = contextvars.ContextVar("owner_agent_mcp_context", default=None)
platform_store = None
agent_runner = None
create_conversation = None
memory_context = None
global_context = None
max_history = 50
max_message_length = 20_000


def configure(store, runner, conversation_creator, memory_resolver, global_resolver):
    global platform_store, agent_runner, create_conversation, memory_context, global_context
    platform_store = store
    agent_runner = runner
    create_conversation = conversation_creator
    memory_context = memory_resolver
    global_context = global_resolver


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
    ):
        raise RuntimeError("IDEA MCP 请求未完成授权")
    return context


@mcp.tool()
async def idea_chat(message: str, conversation_id: str | None = None, use_memory: bool = True) -> str:
    """向 Owner 专属云端 IDEA 发送消息，并返回可跨设备续接的会话 ID 与回复。"""
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
    if use_memory:
        saved_context = memory_context(context, message)
        if saved_context:
            runner_message = f"以下是用户明确保存的长期记忆，仅作必要上下文：\n{saved_context}\n\n当前用户请求：\n{runner_message}"

    result = await agent_runner.run(user_message=runner_message, history=history[-10:])
    reply = result.get("reply", "抱歉，我暂时无法处理这个请求。")
    tool_calls_log = result.get("tool_calls_log", [])
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
