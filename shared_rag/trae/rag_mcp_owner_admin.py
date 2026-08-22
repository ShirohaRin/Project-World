"""Owner RAG project administration MCP backed by an IDEA Owner Agent credential."""

import asyncio
import base64
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


SERVER_URL = os.environ.get("RAG_SERVER_URL", "").rstrip("/")
IDEA_OWNER_TOKEN = os.environ.get("RAG_IDEA_OWNER_TOKEN", "")
COLLECTIONS = {"public", "data", "novel"}
UPLOAD_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RAG-OWNER-MCP] %(levelname)s - %(message)s", stream=sys.stderr)
log = logging.getLogger("rag-owner-mcp")


def require_project_id(value: Any, *, optional: bool = False) -> str:
    """Owner / 完整权限 IDEA 可省略 project_id（省略时返回 all，表示整个 RAG 库）。"""
    if value is None or value == "":
        if optional:
            return "all"
        raise ValueError("project_id 必须是非空字符串。")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("project_id 必须是非空字符串。")
    return value.strip()


def require_collection(value: Any) -> str:
    if not isinstance(value, str) or value not in COLLECTIONS:
        raise ValueError("collection 必须是 public、data 或 novel。")
    return value


def require_document_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_path 必须是非空相对路径。")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError("document_path 必须是安全的集合内相对路径。")
    return normalized


def request_json(method: str, endpoint: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> dict[str, Any]:
    if not SERVER_URL:
        raise RuntimeError("RAG_SERVER_URL 尚未配置。")
    if not IDEA_OWNER_TOKEN:
        raise RuntimeError("RAG_IDEA_OWNER_TOKEN 尚未配置。")
    try:
        response = requests.request(method, f"{SERVER_URL}{endpoint}", headers={"Authorization": f"Bearer {IDEA_OWNER_TOKEN}"}, params=params, json=body, files=files, timeout=180)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}。") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error


def list_project_documents(project_id: str, collection: str) -> str:
    project_id, collection = require_project_id(project_id, optional=True), require_collection(collection)
    return json.dumps(request_json("GET", f"/api/projects/{quote(project_id, safe='')}/documents/{collection}"), ensure_ascii=False, indent=2)


def read_project_document(project_id: str, collection: str, document_path: str) -> str:
    project_id, collection, document_path = require_project_id(project_id, optional=True), require_collection(collection), require_document_path(document_path)
    if not SERVER_URL or not IDEA_OWNER_TOKEN:
        raise RuntimeError("RAG_SERVER_URL 或 RAG_IDEA_OWNER_TOKEN 尚未配置。")
    try:
        response = requests.get(f"{SERVER_URL}/api/projects/{quote(project_id, safe='')}/documents/{collection}/{quote(document_path, safe='/')}", headers={"Authorization": f"Bearer {IDEA_OWNER_TOKEN}"}, timeout=180)
        response.raise_for_status()
        if Path(document_path).suffix.lower() in {".txt", ".md"}:
            return response.content.decode("utf-8")
        return json.dumps({"document_path": document_path, "encoding": "base64", "content": base64.b64encode(response.content).decode("ascii")}, ensure_ascii=False, indent=2)
    except requests.HTTPError as error:
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}。") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error


def update_project_document(project_id: str, collection: str, document_path: str, content: str) -> str:
    project_id, collection, document_path = require_project_id(project_id, optional=True), require_collection(collection), require_document_path(document_path)
    if Path(document_path).suffix.lower() not in {".txt", ".md"} or not isinstance(content, str):
        raise ValueError("仅可用字符串内容更新 .txt 或 .md 文档。")
    return json.dumps(request_json("PUT", f"/api/projects/{quote(project_id, safe='')}/documents/{collection}/{quote(document_path, safe='/')}", body={"content": content}), ensure_ascii=False, indent=2)


def upload_project_document(project_id: str, collection: str, local_path: str) -> str:
    project_id, collection = require_project_id(project_id, optional=True), require_collection(collection)
    path = Path(local_path).expanduser().resolve() if isinstance(local_path, str) else None
    if not path or not path.is_file() or path.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ValueError("local_path 必须是存在的 TXT、MD、PDF 或 DOCX 文件。")
    with path.open("rb") as file:
        result = request_json("POST", f"/api/projects/{quote(project_id, safe='')}/documents/{collection}/upload", files={"file": (path.name, file, "application/octet-stream")})
    return json.dumps(result, ensure_ascii=False, indent=2)


def delete_project_document(project_id: str, collection: str, document_path: str) -> str:
    project_id, collection, document_path = require_project_id(project_id, optional=True), require_collection(collection), require_document_path(document_path)
    return json.dumps(request_json("DELETE", f"/api/projects/{quote(project_id, safe='')}/documents/{collection}/{quote(document_path, safe='/')}"), ensure_ascii=False, indent=2)


def rebuild_project_index(project_id: str, collection: str) -> str:
    project_id, collection = require_project_id(project_id, optional=True), require_collection(collection)
    return json.dumps(request_json("POST", f"/api/projects/{quote(project_id, safe='')}/rebuild", params={"collection": collection}), ensure_ascii=False, indent=2)


def search_project_knowledge(project_id: str, collection: str, query: str, top_k: int = 5) -> str:
    project_id, collection = require_project_id(project_id, optional=True), require_collection(collection)
    if not isinstance(query, str) or not query.strip() or not isinstance(top_k, int):
        raise ValueError("query 必须非空，top_k 必须是整数。")
    return json.dumps(request_json("POST", f"/api/projects/{quote(project_id, safe='')}/search", params={"collection": collection}, body={"query": query.strip(), "top_k": max(1, min(top_k, 10))}), ensure_ascii=False, indent=2)


async def main() -> None:
    specs = {
        "list_project_documents": ("列出项目集合内文档（Owner 省略 project_id 时列出整个 RAG 库）。", ["collection"]),
        "read_project_document": ("读取项目文档（Owner 省略 project_id 时从整个 RAG 库定位）。", ["collection", "document_path"]),
        "update_project_document": ("更新项目 TXT 或 Markdown 文档（Owner 省略 project_id 时对整个 RAG 库定位）。", ["collection", "document_path", "content"]),
        "upload_project_document": ("上传本机文档到项目集合（Owner 省略 project_id 时上传到整个 RAG 库）。", ["collection", "local_path"]),
        "delete_project_document": ("删除项目文档（Owner 省略 project_id 时对整个 RAG 库定位）。", ["collection", "document_path"]),
        "rebuild_project_index": ("重建项目集合索引（Owner 省略 project_id 时重建整个 RAG 库索引）。", ["collection"]),
        "search_project_knowledge": ("检索项目集合知识（Owner 省略 project_id 时检索整个 RAG 库）。", ["collection", "query"]),
    }
    properties = {"project_id": {"type": "string", "description": "项目 id；Owner 可省略以查询整个 RAG 库"}, "collection": {"type": "string", "enum": sorted(COLLECTIONS)}, "document_path": {"type": "string"}, "content": {"type": "string"}, "local_path": {"type": "string"}, "query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}

    async def list_tools(*_args: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=description,
                    inputSchema={
                        "type": "object",
                        "properties": {
                            key: properties[key]
                            for key in required + (["top_k"] if name == "search_project_knowledge" else [])
                        },
                        "required": required,
                    },
                )
                for name, (description, required) in specs.items()
            ]
        )

    async def call_tool(*args: Any) -> types.CallToolResult:
        if len(args) == 1 and isinstance(args[0], types.CallToolRequestParams):
            name = args[0].name
            arguments = args[0].arguments or {}
        elif len(args) >= 2:
            name = args[-2]
            arguments = args[-1] or {}
        else:
            raise ValueError("MCP 工具调用参数无效。")
        functions = {"list_project_documents": list_project_documents, "read_project_document": read_project_document, "update_project_document": update_project_document, "upload_project_document": upload_project_document, "delete_project_document": delete_project_document, "rebuild_project_index": rebuild_project_index, "search_project_knowledge": search_project_knowledge}
        if name not in functions:
            raise ValueError(f"未知工具：{name}")
        ordered = specs[name][1] + (["top_k"] if name == "search_project_knowledge" else [])
        result = await asyncio.to_thread(functions[name], *(arguments.get(key, 5) if key == "top_k" else arguments.get(key) for key in ordered))
        return types.CallToolResult(content=[types.TextContent(type="text", text=result)])

    if "on_list_tools" in inspect.signature(Server).parameters:
        server = Server(
            "rag-owner-admin",
            on_list_tools=list_tools,
            on_call_tool=call_tool,
        )
    else:
        server = Server("rag-owner-admin")
        server.list_tools()(list_tools)
        server.call_tool()(call_tool)

    log.info("Owner RAG MCP 已启动；服务地址：%s", SERVER_URL)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
