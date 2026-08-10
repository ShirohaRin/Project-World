"""
研究者 RAG 原文 MCP 代理。

仅从环境变量读取 RAG_RESEARCH_KEY；可操作 public 和 data 集合中的原始文档。
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


SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://localhost:8080").rstrip("/")
RESEARCH_KEY = os.environ.get("RAG_RESEARCH_KEY", "")
COLLECTIONS = {"public", "data"}
TEXT_EXTENSIONS = {".txt", ".md"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAG-RESEARCH-MCP] %(levelname)s - %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("rag-research-mcp")


def require_collection(value: Any) -> str:
    if not isinstance(value, str) or value not in COLLECTIONS:
        raise ValueError("collection 必须是 public 或 data。")
    return value


def require_document_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_path 必须是 list_source_documents 返回的非空相对路径。")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError("document_path 必须是集合内的安全相对路径，不能包含路径穿越。")
    return normalized


def request_json(method: str, endpoint: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not RESEARCH_KEY:
        raise RuntimeError("RAG_RESEARCH_KEY 尚未配置。")
    try:
        response = requests.request(
            method, f"{SERVER_URL}{endpoint}", headers={"X-API-Key": RESEARCH_KEY}, json=body, timeout=180,
        )
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        try:
            detail = error.response.json().get("error", error.response.text)
        except ValueError:
            detail = error.response.text
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}：{detail}") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error


def list_source_documents(collection: str) -> str:
    collection = require_collection(collection)
    return json.dumps(request_json("GET", f"/api/source-documents/{collection}"), ensure_ascii=False, indent=2)


def read_source_document(collection: str, document_path: str) -> str:
    collection = require_collection(collection)
    document_path = require_document_path(document_path)
    if not RESEARCH_KEY:
        raise RuntimeError("RAG_RESEARCH_KEY 尚未配置。")
    try:
        response = requests.get(
            f"{SERVER_URL}/api/source-documents/{collection}/{quote(document_path, safe='/')}",
            headers={"X-API-Key": RESEARCH_KEY}, timeout=180,
        )
        response.raise_for_status()
    except requests.HTTPError as error:
        raise RuntimeError(f"RAG 服务返回 HTTP {error.response.status_code}：{error.response.text}") from error
    except requests.RequestException as error:
        raise RuntimeError(f"无法连接 RAG 服务：{type(error).__name__}") from error

    if Path(document_path).suffix.lower() in TEXT_EXTENSIONS:
        return response.content.decode("utf-8")
    return json.dumps({
        "document_path": document_path,
        "encoding": "base64",
        "content": base64.b64encode(response.content).decode("ascii"),
        "hint": "这是服务器返回的原始文件字节的 Base64 表示；PDF/DOCX 不可直接文本编辑，请通过上传替换文件。",
    }, ensure_ascii=False, indent=2)


def update_source_document(collection: str, document_path: str, content: str) -> str:
    collection = require_collection(collection)
    document_path = require_document_path(document_path)
    if Path(document_path).suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError("仅可编辑 .txt 与 .md；PDF/DOCX 请通过上传替换。")
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串。")
    return json.dumps(
        request_json("PUT", f"/api/source-documents/{collection}/{quote(document_path, safe='/')}", body={"content": content}),
        ensure_ascii=False,
        indent=2,
    )


async def main() -> None:
    server = Server("rag-research-source-documents")
    tools = {
        "list_source_documents": (
            "列出研究者获授权集合（public、data）内的原始文档路径。",
            {"type": "object", "properties": {"collection": {"type": "string", "enum": sorted(COLLECTIONS)}}, "required": ["collection"]},
        ),
        "read_source_document": (
            "读取原始文档。TXT/Markdown 返回 UTF-8 文本；其他支持格式返回原始字节的 Base64。",
            {"type": "object", "properties": {"collection": {"type": "string", "enum": sorted(COLLECTIONS)}, "document_path": {"type": "string"}}, "required": ["collection", "document_path"]},
        ),
        "update_source_document": (
            "更新 public 或 data 中的 .txt/.md 原文。更新后需手动重建索引；PDF/DOCX 请上传替换。",
            {"type": "object", "properties": {"collection": {"type": "string", "enum": sorted(COLLECTIONS)}, "document_path": {"type": "string"}, "content": {"type": "string"}}, "required": ["collection", "document_path", "content"]},
        ),
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=name, description=definition[0], inputSchema=definition[1]) for name, definition in tools.items()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name == "list_source_documents":
            result = await asyncio.to_thread(list_source_documents, arguments.get("collection"))
        elif name == "read_source_document":
            result = await asyncio.to_thread(read_source_document, arguments.get("collection"), arguments.get("document_path"))
        elif name == "update_source_document":
            result = await asyncio.to_thread(update_source_document, arguments.get("collection"), arguments.get("document_path"), arguments.get("content"))
        else:
            raise ValueError(f"未知工具：{name}")
        return [types.TextContent(type="text", text=result)]

    log.info("Research RAG 原文 MCP 已启动；服务地址：%s", SERVER_URL)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
