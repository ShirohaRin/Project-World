import contextvars
import json

from mcp.server.fastmcp import FastMCP


request_context = contextvars.ContextVar("memory_mcp_context", default=None)
platform_store = None
namespace_resolver = None


def configure(store, resolver):
    global platform_store, namespace_resolver
    platform_store = store
    namespace_resolver = resolver


mcp = FastMCP("IDEA Controlled Memory", stateless_http=True, json_response=True)


def _visible_memories(query=None, limit=5):
    context = request_context.get()
    if context is None or platform_store is None or namespace_resolver is None:
        raise RuntimeError("MCP 记忆请求未完成授权")
    return platform_store.list_memories(
        context.principal.account_id,
        context.space_id,
        list(namespace_resolver(context).values()),
        query=query,
        limit=max(1, min(limit, 5)),
    )


@mcp.tool()
def memory_search(query: str, limit: int = 5) -> str:
    """检索当前获授权范围内、由用户明确保存的长期记忆。仅在确有必要时使用。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    memories = _visible_memories(query.strip(), limit)
    return json.dumps({"memories": memories}, ensure_ascii=False)


@mcp.tool()
def memory_get(memory_id: str) -> str:
    """读取当前获授权范围内的一条长期记忆。"""
    memories = _visible_memories(limit=5)
    for memory in memories:
        if memory["id"] == memory_id:
            return json.dumps(memory, ensure_ascii=False)
    raise ValueError("记忆不存在或无权访问")


app = mcp.streamable_http_app()
