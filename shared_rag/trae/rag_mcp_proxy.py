"""
======================================================================
TRAE 本地 MCP 代理 —— 连接服务器上的 RAG 知识库
======================================================================
这个脚本跑在你的 Windows 开发机上。
工作方式：TRAE 通过 stdio MCP 协议启动此脚本 → 此脚本把请求转成 HTTP
          → 发送到你的 Linux 服务器 → 接收结果 → 返回给 TRAE

配置方式（在 TRAE 的 MCP 设置中）：
  命令：python
  参数：C:\\path\\to\\rag_mcp_proxy.py
  环境变量：
    RAG_SERVER_URL=http://你的服务器IP:8080
    RAG_AGENT_TOKEN=个人检索 Token           （可检索 public、data 与自己的 Private 库）
    RAG_LANGBOT_KEY=你生成的LANGBOT_KEY    （可选，有则用）

为什么需要代理？
  - TRAE 原生支持 stdio MCP，最简单稳定
  - 个人私有检索使用 RAG_AGENT_TOKEN，不使用管理员 Key
  - 服务器只暴露 HTTP，不暴露 MCP 协议

======================================================================
"""

import os
import sys
import json
import logging

import requests

# ============================================================
# 从环境变量读取配置（这些值在 TRAE 的 MCP 设置中配置）
# ============================================================
SERVER_URL    = os.environ.get("RAG_SERVER_URL", "http://localhost:8080")
AGENT_TOKEN   = os.environ.get("RAG_AGENT_TOKEN", "")
CAN_SEARCH = bool(AGENT_TOKEN)

# ============================================================
# 日志 → stderr（避免干扰 MCP stdio 通信）
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP-PROXY] %(levelname)s - %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp-proxy")


def api_request(method: str, path: str, json_data: dict = None, params: dict = None, agent_auth: bool = False) -> dict:
    """
    统一的 HTTP 请求封装。
    个人知识库请求仅携带用户专属 Agent Token。
    """
    headers = {}
    if agent_auth and AGENT_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_TOKEN}"

    url = f"{SERVER_URL}{path}"

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=json_data, params=params, timeout=30)
        else:
            raise ValueError(f"不支持的 HTTP 方法：{method}")

        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.ConnectionError:
        log.error("❌ 无法连接到服务器：%s", SERVER_URL)
        return {"error": f"无法连接到知识库服务器 {SERVER_URL}。请检查：\n1. 服务器是否启动\n2. 网络是否畅通\n3. SERVER_URL 是否正确"}
    except requests.exceptions.Timeout:
        log.error("❌ 请求超时：%s%s", SERVER_URL, path)
        return {"error": f"请求超时（{SERVER_URL}{path}），服务器可能负载较高或网络不稳定"}
    except requests.exceptions.HTTPError as e:
        log.error("❌ HTTP 错误：%s", e)
        try:
            detail = e.response.json().get("error", str(e))
        except Exception:
            detail = str(e)
        return {"error": f"服务器返回错误：{detail}"}


# ============================================================
# 核心工具函数 —— 这些会被注册成 MCP Tool
# ============================================================

def search_public(query: str, top_k: int = 5) -> str:
    """检索公开知识库"""
    log.info("查询公开库：%s", query[:50])

    result = api_request("POST", "/api/me/search", json_data={"query": query, "top_k": min(top_k, 10)}, params={"collection": "public"}, agent_auth=True)

    if "error" in result:
        return f"❌ {result['error']}"

    results = result.get("results", [])
    if not results:
        return f"🔍 公开知识库中未找到与「{query}」相关的内容。"

    parts = [f"🔍 公开知识库检索（{result.get('total_results', 0)} 条结果）：「{query}」\n"]
    for r in results:
        parts.append(
            f"── 结果 {r['rank']}（{r['similarity']:.0%}）──\n"
            f"📄 {r['source']}\n"
            f"{r['content']}\n"
        )
    return "\n".join(parts)


def search_data(query: str, top_k: int = 5) -> str:
    """检索研究数据知识库"""
    log.info("查询数据库：%s", query[:50])

    result = api_request("POST", "/api/me/search",
                         json_data={"query": query, "top_k": min(top_k, 10)},
                         params={"collection": "data"}, agent_auth=True)

    if "error" in result:
        return f"❌ {result['error']}"

    results = result.get("results", [])
    if not results:
        return f"🔍 数据知识库中未找到与「{query}」相关的内容。"

    parts = [f"🔍 数据知识库检索（{result.get('total_results', 0)} 条结果）：「{query}」\n"]
    for r in results:
        parts.append(
            f"── 结果 {r['rank']}（{r['similarity']:.0%}）──\n"
            f"📄 {r['source']}\n"
            f"{r['content']}\n"
        )
    return "\n".join(parts)


def search_my_private(query: str, top_k: int = 5) -> str:
    """检索当前个人 Agent 专属的私有知识库。"""
    if not CAN_SEARCH:
        return "❌ 未配置 RAG_AGENT_TOKEN，无法访问个人知识库。"

    log.info("查询私有库：%s", query[:50])

    result = api_request(
        "POST", "/api/me/search",
        json_data={"query": query, "top_k": min(top_k, 10)},
        params={"collection": "private"},
        agent_auth=True,
    )

    if "error" in result:
        return f"❌ {result['error']}"

    results = result.get("results", [])
    if not results:
        return f"🔍 私有知识库中未找到与「{query}」相关的内容。"

    parts = [f"🔒 个人私有知识库检索（{result.get('total_results', 0)} 条结果）：「{query}」\n"]
    for r in results:
        parts.append(
            f"── 结果 {r['rank']}（{r['similarity']:.0%}）──\n"
            f"📄 {r['source']}\n"
            f"{r['content']}\n"
        )
    return "\n".join(parts)


# ============================================================
# MCP Server 启动
# ============================================================
async def main():
    log.info("=" * 50)
    log.info("  RAG MCP 代理启动")
    log.info("  服务器地址：%s", SERVER_URL)
    log.info("  个人 Agent Token： %s", "✓" if AGENT_TOKEN else "✗（知识库不可用）")
    log.info("=" * 50)

    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("rag-knowledge-proxy")
    tools = {
        "search_public_knowledge": (
            "检索公开知识库。用于查询已发表论文、写作素材、风格指南和角色设定等公开文档。",
            search_public,
        ),
        "search_data_knowledge": (
            "检索当前用户可访问的研究数据知识库。",
            search_data,
        ),
    }
    if CAN_SEARCH:
        tools["search_my_private_knowledge"] = (
            "检索当前个人 Agent 专属的私有知识库。",
            search_my_private,
        )

    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=name, description=description, inputSchema=schema)
            for name, (description, _) in tools.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name not in tools:
            raise ValueError(f"未知工具：{name}")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串。")
        top_k = arguments.get("top_k", 5)
        if not isinstance(top_k, int):
            raise ValueError("top_k 必须是整数。")
        result = await asyncio.to_thread(tools[name][1], query.strip(), top_k)
        return [types.TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

    log.info("MCP 代理已退出")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
