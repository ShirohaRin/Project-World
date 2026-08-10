"""
Owner RAG 管理 MCP。

使用 RAG_ADMIN_KEY 与 RAG_INGEST_KEY 连接共享 RAG 知识库。
两个 Key 只从本机环境变量读取，禁止写入代码、Git 或聊天记录。
"""

import asyncio
import base64
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


SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://113.249.105.33:8080").rstrip("/")
ADMIN_KEY = os.environ.get("RAG_ADMIN_KEY", "")
INGEST_KEY = os.environ.get("RAG_INGEST_KEY", "")
COLLECTIONS = {"public", "data", "novel", "private"}
UPLOAD_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAG-OWNER-MCP] %(levelname)s - %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("rag-owner-mcp")


def require_collection(value: Any) -> str:
    if not isinstance(value, str) or value not in COLLECTIONS:
        raise ValueError("collection 必须是 public、data、novel 或 private。")
    return value


def request_json(
    method: str,
    endpoint: str,
    *,
    key: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not key:
        raise RuntimeError("所需 RAG Key 尚未配置。")
    try:
        response = requests.request(
            method,
            f"{SERVER_URL}{endpoint}",
            headers={"X-API-Key": key},
            params=params,
            json=body,
            files=files,
            timeout=180,
        )
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        try:
            detail = error.response.json().get("detail") or error.response.json().get("error")
        except ValueError:
            detail = error.response.text
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}：{detail}") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error


def search_knowledge(collection: str, query: str, top_k: int = 5) -> str:
    collection = require_collection(collection)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串。")
    if not isinstance(top_k, int):
        raise ValueError("top_k 必须是整数。")

    result = request_json(
        "POST",
        "/api/admin/search",
        key=ADMIN_KEY,
        params={"collection": collection},
        body={"query": query.strip(), "top_k": max(1, min(top_k, 10))},
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


def list_documents() -> str:
    result = request_json("GET", "/api/documents", key=ADMIN_KEY)
    return json.dumps(result, ensure_ascii=False, indent=2)


def require_document_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_path 必须是 list_source_documents 返回的非空相对路径。")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError("document_path 必须是集合内的安全相对路径，不能包含路径穿越。")
    return normalized


def list_source_documents(collection: str) -> str:
    collection = require_collection(collection)
    result = request_json("GET", f"/api/source-documents/{collection}", key=ADMIN_KEY)
    return json.dumps(result, ensure_ascii=False, indent=2)


def read_source_document(collection: str, document_path: str) -> str:
    collection = require_collection(collection)
    document_path = require_document_path(document_path)
    if not ADMIN_KEY:
        raise RuntimeError("所需 RAG Key 尚未配置。")
    try:
        response = requests.get(
            f"{SERVER_URL}/api/source-documents/{collection}/{quote(document_path, safe='/')}",
            headers={"X-API-Key": ADMIN_KEY}, timeout=180,
        )
        response.raise_for_status()
        if Path(document_path).suffix.lower() in {".txt", ".md"}:
            return response.content.decode("utf-8")
        return json.dumps({
            "document_path": document_path,
            "encoding": "base64",
            "content": base64.b64encode(response.content).decode("ascii"),
            "hint": "这是服务器返回的原始文件字节的 Base64 表示；PDF/DOCX 不可直接文本编辑，请通过上传替换文件。",
        }, ensure_ascii=False, indent=2)
    except requests.HTTPError as error:
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}：{error.response.text}") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error


def update_source_document(collection: str, document_path: str, content: str) -> str:
    collection = require_collection(collection)
    document_path = require_document_path(document_path)
    if Path(document_path).suffix.lower() not in {".txt", ".md"}:
        raise ValueError("仅可编辑 .txt 与 .md；PDF/DOCX 请通过上传替换。")
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串。")
    result = request_json("PUT", f"/api/source-documents/{collection}/{document_path}", key=ADMIN_KEY, body={"content": content})
    return json.dumps(result, ensure_ascii=False, indent=2)


def upload_document(collection: str, local_path: str) -> str:
    collection = require_collection(collection)
    if not isinstance(local_path, str) or not local_path.strip():
        raise ValueError("local_path 必须是本机文件的绝对路径。")

    path = Path(local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")
    if path.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 .txt、.md、.pdf、.docx 文件。")

    with path.open("rb") as file:
        result = request_json(
            "POST",
            "/api/documents/upload",
            key=INGEST_KEY,
            params={"collection": collection},
            files={"file": (path.name, file, "application/octet-stream")},
        )
    result["next_step"] = f"调用 rebuild_knowledge_index，collection 设为 {collection}，使文档进入检索索引。"
    return json.dumps(result, ensure_ascii=False, indent=2)


def delete_document(collection: str, filename: str) -> str:
    collection = require_collection(collection)
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename 必须是 list_knowledge_documents 返回的非空文件名。")
    if "/" in filename or "\\" in filename:
        raise ValueError("filename 不能包含路径分隔符。")

    result = request_json(
        "DELETE",
        f"/api/documents/{collection}/{filename}",
        key=ADMIN_KEY,
    )
    result["next_step"] = f"调用 rebuild_knowledge_index，collection 设为 {collection}，清理旧索引。"
    return json.dumps(result, ensure_ascii=False, indent=2)


def rebuild_knowledge_index(collection: str = "all") -> str:
    if collection != "all":
        require_collection(collection)
    result = request_json(
        "POST",
        "/api/admin/rebuild",
        key=ADMIN_KEY,
        params={"collection": collection},
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


async def main() -> None:
    server = Server("rag-owner-admin")

    tools = {
        "search_knowledge": (
            "以 Owner 管理权限检索任意知识库集合，包括完整 private 内容。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": sorted(COLLECTIONS)},
                    "query": {"type": "string", "description": "检索问题或关键词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["collection", "query"],
            },
        ),
        "list_knowledge_documents": (
            "列出所有知识库集合中的已上传文档。",
            {"type": "object", "properties": {}},
        ),
        "list_source_documents": (
            "列出指定集合内可读取的原始文档路径。",
            {
                "type": "object",
                "properties": {"collection": {"type": "string", "enum": sorted(COLLECTIONS)}},
                "required": ["collection"],
            },
        ),
        "read_source_document": (
            "读取指定原始文档；支持所有服务器支持的文件格式。非文本格式以替换字符呈现原始字节，不可直接编辑。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": sorted(COLLECTIONS)},
                    "document_path": {"type": "string", "description": "list_source_documents 返回的集合内相对路径"},
                },
                "required": ["collection", "document_path"],
            },
        ),
        "update_source_document": (
            "更新指定 .txt 或 .md 原文；PDF/DOCX 不可直接文本编辑，应上传替换。更新后需要手动重建索引。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": sorted(COLLECTIONS)},
                    "document_path": {"type": "string", "description": "集合内 .txt 或 .md 相对路径"},
                    "content": {"type": "string", "description": "完整替换后的 UTF-8 文本内容"},
                },
                "required": ["collection", "document_path", "content"],
            },
        ),
        "upload_knowledge_document": (
            "上传本机 TXT、Markdown、PDF 或 DOCX 到知识库。上传后需重建该集合索引。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": sorted(COLLECTIONS)},
                    "local_path": {"type": "string", "description": "本机文件的绝对路径"},
                },
                "required": ["collection", "local_path"],
            },
        ),
        "delete_knowledge_document": (
            "删除指定集合中的文档。删除后需重建该集合索引。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": sorted(COLLECTIONS)},
                    "filename": {"type": "string", "description": "文档列表中的文件名"},
                },
                "required": ["collection", "filename"],
            },
        ),
        "rebuild_knowledge_index": (
            "重建一个知识库集合或全部集合的 FAISS 索引。",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": ["all", *sorted(COLLECTIONS)], "default": "all"},
                },
            },
        ),
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=name, description=definition[0], inputSchema=definition[1])
            for name, definition in tools.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name == "search_knowledge":
            result = await asyncio.to_thread(
                search_knowledge,
                arguments.get("collection"),
                arguments.get("query"),
                arguments.get("top_k", 5),
            )
        elif name == "list_knowledge_documents":
            result = await asyncio.to_thread(list_documents)
        elif name == "list_source_documents":
            result = await asyncio.to_thread(list_source_documents, arguments.get("collection"))
        elif name == "read_source_document":
            result = await asyncio.to_thread(read_source_document, arguments.get("collection"), arguments.get("document_path"))
        elif name == "update_source_document":
            result = await asyncio.to_thread(update_source_document, arguments.get("collection"), arguments.get("document_path"), arguments.get("content"))
        elif name == "upload_knowledge_document":
            result = await asyncio.to_thread(
                upload_document,
                arguments.get("collection"),
                arguments.get("local_path"),
            )
        elif name == "delete_knowledge_document":
            result = await asyncio.to_thread(
                delete_document,
                arguments.get("collection"),
                arguments.get("filename"),
            )
        elif name == "rebuild_knowledge_index":
            result = await asyncio.to_thread(rebuild_knowledge_index, arguments.get("collection", "all"))
        else:
            raise ValueError(f"未知工具：{name}")
        return [types.TextContent(type="text", text=result)]

    log.info("Owner RAG MCP 已启动；服务地址：%s", SERVER_URL)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
