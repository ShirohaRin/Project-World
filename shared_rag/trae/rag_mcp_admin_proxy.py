"""
管理员 RAG MCP 代理。

这是服务器所有者使用的高权限代理，可检索 public、data、novel 和完整 private。
管理员 Key 只从本机环境变量 RAG_ADMIN_KEY 读取，不要写入此文件或提交到仓库。
"""

import asyncio
import logging
import os
import sys

import requests

SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://localhost:8080").rstrip("/")
ADMIN_KEY = os.environ.get("RAG_ADMIN_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAG-ADMIN-MCP] %(levelname)s - %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("rag-admin-mcp")


def api_search(collection: str, query: str, top_k: int) -> dict:
    if not ADMIN_KEY:
        return {"error": "未配置 RAG_ADMIN_KEY，管理员 MCP 不可用。"}
    try:
        response = requests.post(
            f"{SERVER_URL}/api/admin/search",
            headers={"X-API-Key": ADMIN_KEY},
            params={"collection": collection},
            json={"query": query, "top_k": min(max(top_k, 1), 10)},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "管理员检索请求超时。"}
    except requests.exceptions.RequestException as exc:
        try:
            detail = response.json().get("error", str(exc))
        except Exception:
            detail = str(exc)
        return {"error": f"服务器请求失败：{detail}"}


def format_result(label: str, result: dict, query: str) -> str:
    if "error" in result:
        return f"❌ {result['error']}"
    results = result.get("results", [])
    if not results:
        return f"🔍 {label}中未找到与「{query}」相关的内容。"
    parts = [f"🔐 管理员检索 {label}（{len(results)} 条结果）：「{query}」\n"]
    for item in results:
        parts.append(
            f"── 结果 {item['rank']}（{item['similarity']:.0%}）──\n"
            f"📄 {item['source']}\n{item['content']}\n"
        )
    return "\n".join(parts)


def search(collection: str, label: str, query: str, top_k: int = 5) -> str:
    log.info("管理员查询 %s：%s", collection, query[:50])
    return format_result(label, api_search(collection, query, top_k), query)


async def main():
    log.info("管理员 MCP 启动，服务器：%s，Key：%s", SERVER_URL, "已配置" if ADMIN_KEY else "未配置")
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("rag-admin-knowledge-proxy")
    tools = {
        "search_public_knowledge": ("public", "Public 知识库", "管理员检索 Public 知识库。"),
        "search_data_knowledge": ("data", "Data 知识库", "管理员检索 Data 知识库。"),
        "search_novel_knowledge": ("novel", "Novel 知识库", "管理员检索 Novel 知识库。"),
        "search_private_knowledge": (
            "private",
            "完整 Private 知识库",
            "管理员检索完整 Private 知识库，包括所有用户私密文件。",
        ),
    }
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要检索的内容"},
            "top_k": {"type": "integer", "description": "返回结果数量，范围 1 到 10", "default": 5},
        },
        "required": ["query"],
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=name, description=description, inputSchema=input_schema)
            for name, (_, _, description) in tools.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name not in tools:
            raise ValueError(f"未知工具：{name}")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")
        top_k = arguments.get("top_k", 5)
        if not isinstance(top_k, int):
            raise ValueError("top_k 必须是整数")
        collection, label, _ = tools[name]
        result = await asyncio.to_thread(search, collection, label, query, top_k)
        return [types.TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
