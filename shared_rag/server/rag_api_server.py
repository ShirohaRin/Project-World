"""
共享 RAG 知识库服务 — 服务器端 (FAISS 版)
部署在你的 Linux 服务器上（Docker 容器内）。

功能：
  - 四知识库：private / public / novel / data
  - 五级 API Key 权限控制（MCP / LangBot 用）
  - 文件管理器：密码认证（访客上传 + 管理员管理），仅限 public / data
  - 自动文档摄入（PDF/TXT/MD/DOCX → 分块 → 向量化 → 入库）
  - REST API + MCP over SSE 双协议
  - 使用 FAISS 向量库（无 SQLite，Docker 下零问题）

启动方式：
  python rag_api_server.py --port 8080
"""

import os
import sys
import json
import shutil
import logging
import mimetypes
import uuid
import secrets as _secrets
import re
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
import jwt as pyjwt
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

# ======================================================================
# 配置
# ======================================================================
PRIVATE_DOCS_DIR = os.environ.get("RAG_PRIVATE_DIR", "/app/knowledge_private")
PUBLIC_DOCS_DIR  = os.environ.get("RAG_PUBLIC_DIR",  "/app/knowledge_public")
NOVEL_DOCS_DIR   = os.environ.get("RAG_NOVEL_DIR",   "/app/knowledge_novel")
DATA_DOCS_DIR    = os.environ.get("RAG_DATA_DIR",    "/app/knowledge_data")
VECTOR_DIR = os.environ.get("RAG_CHROMA_DIR", "/app/vector_data")
EMBEDDING_MODEL = os.environ.get("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
DEFAULT_TOP_K = 5

# 全局默认分块参数（兜底）
CHUNK_SIZE    = int(os.environ.get("RAG_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "200"))

# 按集合独立分块参数（每个集合可单独覆盖）
CHUNK_CONFIG = {
    "private": {
        "size":    int(os.environ.get("RAG_CHUNK_SIZE_PRIVATE", os.environ.get("RAG_CHUNK_SIZE", "800"))),
        "overlap": int(os.environ.get("RAG_CHUNK_OVERLAP_PRIVATE", os.environ.get("RAG_CHUNK_OVERLAP", "100"))),
    },
    "public": {
        "size":    int(os.environ.get("RAG_CHUNK_SIZE_PUBLIC", os.environ.get("RAG_CHUNK_SIZE", "1200"))),
        "overlap": int(os.environ.get("RAG_CHUNK_OVERLAP_PUBLIC", os.environ.get("RAG_CHUNK_OVERLAP", "200"))),
    },
    "novel": {
        "size":    int(os.environ.get("RAG_CHUNK_SIZE_NOVEL", os.environ.get("RAG_CHUNK_SIZE", "2000"))),
        "overlap": int(os.environ.get("RAG_CHUNK_OVERLAP_NOVEL", os.environ.get("RAG_CHUNK_OVERLAP", "300"))),
    },
    "data": {
        "size":    int(os.environ.get("RAG_CHUNK_SIZE_DATA", os.environ.get("RAG_CHUNK_SIZE", "1200"))),
        "overlap": int(os.environ.get("RAG_CHUNK_OVERLAP_DATA", os.environ.get("RAG_CHUNK_OVERLAP", "200"))),
    },
}

ADMIN_KEY     = os.environ.get("RAG_ADMIN_KEY",     "")
LANGBOT_KEY   = os.environ.get("RAG_LANGBOT_KEY",   "")
RESEARCH_KEY  = os.environ.get("RAG_RESEARCH_KEY",  "")
PUBLIC_KEY    = os.environ.get("RAG_PUBLIC_KEY",    "")
INGEST_KEY    = os.environ.get("RAG_INGEST_KEY",    "")

# ========== 文件管理器认证配置 ==========
FM_ADMIN_USERNAME = os.environ.get("RAG_FM_ADMIN_USERNAME", "admin")
FM_ADMIN_PASSWORD = os.environ.get("RAG_FM_ADMIN_PASSWORD", "")
FM_JWT_SECRET      = os.environ.get("RAG_FM_JWT_SECRET", "")
FM_JWT_EXPIRY_HOURS = int(os.environ.get("RAG_FM_JWT_EXPIRY_HOURS", "24"))
FM_USERS_FILE = os.environ.get("RAG_FM_USERS_FILE", "/app/vector_data/file_manager_users.json")
FM_AGENT_TOKEN_EXPIRY_DAYS = int(os.environ.get("RAG_FM_AGENT_TOKEN_EXPIRY_DAYS", "30"))
FM_ENABLED = bool(FM_ADMIN_PASSWORD)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RAG] %(levelname)s - %(message)s")
log = logging.getLogger("rag-server")

# 启动时自动生成 JWT Secret（如果未配置）
if FM_ENABLED and not FM_JWT_SECRET:
    FM_JWT_SECRET = _secrets.token_hex(32)
    log.info("文件管理器 JWT_SECRET 已自动生成（容器重启后会变化，用户需重新登录）")


# ======================================================================
# Models
# ======================================================================
class SearchRequest(BaseModel):
    query: str
    top_k: int = DEFAULT_TOP_K

class SearchResultItem(BaseModel):
    rank: int
    similarity: float
    source: str
    content: str

class SearchResponse(BaseModel):
    query: str
    collection: str
    total_results: int
    results: list[SearchResultItem]

class ChunkParam(BaseModel):
    size: int
    overlap: int

class StatsResponse(BaseModel):
    status: str
    private_records: int
    public_records: int
    novel_records: int
    data_records: int
    embedding_model: str
    chunk_config: dict[str, ChunkParam]

class DocListResponse(BaseModel):
    private: list[str]
    public: list[str]
    novel: list[str]
    data: list[str]


class SourceDocumentUpdateRequest(BaseModel):
    content: str

# ========== 文件管理器 Models ==========
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    role: str
    username: str

class AgentTokenResponse(BaseModel):
    token: str
    expires_at: str

class FileNode(BaseModel):
    name: str
    type: str  # "file" | "folder"
    path: str
    children: Optional[list["FileNode"]] = None

FileNode.model_rebuild()

class BrowseResponse(BaseModel):
    collection: str
    tree: list[FileNode]


# ======================================================================
# API Key Auth（原系统保持不变）
# ======================================================================
class KeyLevel:
    ADMIN    = "admin"
    RESEARCH = "research"
    LANGBOT  = "langbot"
    PUBLIC   = "public"
    INGEST   = "ingest"

COLLECTION_ACCESS = {
    # 用户 private 检索只允许使用用户专属 Agent Token，禁止静态 Key 跨用户搜索。
    KeyLevel.ADMIN:    {"public", "novel", "data"},
    KeyLevel.RESEARCH: {"public", "data"},
    KeyLevel.LANGBOT:  {"public", "novel"},
    KeyLevel.PUBLIC:   {"public"},
    KeyLevel.INGEST:   set(),
}

def verify_key(request: Request, required_level: str):
    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        api_key = request.query_params.get("api_key", "")
    if not ADMIN_KEY and not LANGBOT_KEY and not RESEARCH_KEY and not PUBLIC_KEY and not INGEST_KEY:
        return
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 API Key")
    matched = None
    if ADMIN_KEY and api_key == ADMIN_KEY:
        matched = KeyLevel.ADMIN
    elif RESEARCH_KEY and api_key == RESEARCH_KEY:
        matched = KeyLevel.RESEARCH
    elif LANGBOT_KEY and api_key == LANGBOT_KEY:
        matched = KeyLevel.LANGBOT
    elif PUBLIC_KEY and api_key == PUBLIC_KEY:
        matched = KeyLevel.PUBLIC
    elif INGEST_KEY and api_key == INGEST_KEY:
        matched = KeyLevel.INGEST
    if matched is None:
        raise HTTPException(status_code=403, detail="API Key 无效")
    hierarchy = {KeyLevel.ADMIN: 5, KeyLevel.INGEST: 4, KeyLevel.RESEARCH: 3, KeyLevel.LANGBOT: 2, KeyLevel.PUBLIC: 1}
    required = {"admin": 5, "ingest": 4, "research": 3, "langbot": 2, "public": 1}
    if hierarchy.get(matched, 0) < required.get(required_level, 0):
        raise HTTPException(status_code=403, detail=f"权限不足：需要 {required_level} 级别")
    return matched


def _source_document_root(collection: str) -> str:
    mapping = {
        "private": PRIVATE_DOCS_DIR,
        "public": PUBLIC_DOCS_DIR,
        "novel": NOVEL_DOCS_DIR,
        "data": DATA_DOCS_DIR,
    }
    root = mapping.get(collection)
    if root is None:
        raise HTTPException(status_code=400, detail="collection 必须是 public、data、novel 或 private")
    return root


def _verify_source_document_access(request: Request, collection: str) -> str:
    """原文接口必须显式携带 Admin 或 Research API Key，不能回退为无鉴权。"""
    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="原文接口必须提供 API Key")
    if ADMIN_KEY and _secrets.compare_digest(api_key, ADMIN_KEY):
        key_level = KeyLevel.ADMIN
    elif RESEARCH_KEY and _secrets.compare_digest(api_key, RESEARCH_KEY):
        key_level = KeyLevel.RESEARCH
    else:
        raise HTTPException(status_code=403, detail="仅 RAG Admin Key 或 Research Key 可访问原文接口")

    allowed = {KeyLevel.ADMIN: {"public", "data", "novel", "private"}, KeyLevel.RESEARCH: {"public", "data"}}
    if collection not in allowed[key_level]:
        raise HTTPException(status_code=403, detail=f"{key_level} Key 无权访问 {collection} 集合原文")
    return key_level


def _source_document_path(collection: str, document_path: str) -> Path:
    if not isinstance(document_path, str) or not document_path or "\x00" in document_path:
        raise HTTPException(status_code=400, detail="document_path 必须是非空相对路径")
    normalized = document_path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise HTTPException(status_code=400, detail="document_path 必须是集合内的安全相对路径，不能包含路径穿越")

    root = Path(_source_document_root(collection)).resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="document_path 超出集合目录，已拒绝")
    return target


def _list_source_documents(collection: str) -> list[str]:
    root = Path(_source_document_root(collection))
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*") if path.is_file())


# ======================================================================
# 文件管理器 JWT 认证
# ======================================================================
def _fm_create_jwt(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=FM_JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return pyjwt.encode(payload, FM_JWT_SECRET, algorithm="HS256")

def _fm_verify_jwt(token: str):
    try:
        return pyjwt.decode(token, FM_JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None

def _fm_create_agent_token(username: str) -> str:
    payload = {
        "sub": username,
        "role": "agent",
        "scope": ["public:search", "data:search", "private:search"],
        "aud": "rag-agent",
        "exp": datetime.utcnow() + timedelta(days=FM_AGENT_TOKEN_EXPIRY_DAYS),
        "iat": datetime.utcnow(),
    }
    return pyjwt.encode(payload, FM_JWT_SECRET, algorithm="HS256")

def _fm_verify_agent_token(request: Request) -> dict:
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth:
        raise HTTPException(status_code=401, detail="缺少个人 Agent Token")
    try:
        payload = pyjwt.decode(auth, FM_JWT_SECRET, algorithms=["HS256"], audience="rag-agent")
    except Exception:
        raise HTTPException(status_code=401, detail="个人 Agent Token 无效或已过期")
    if payload.get("role") != "agent":
        raise HTTPException(status_code=403, detail="个人 Agent Token 权限不足")
    _fm_normalize_username(payload.get("sub", ""))
    return payload

def _fm_normalize_username(username: str) -> str:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", username):
        raise HTTPException(status_code=400, detail="账号需为 3-32 位字母、数字、下划线或连字符")
    return username

def _fm_validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少需要 8 位")

def _fm_load_users() -> dict:
    if not os.path.exists(FM_USERS_FILE):
        return {"version": 1, "users": {}}
    try:
        with open(FM_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data.get("users"), dict) else {"version": 1, "users": {}}
    except (OSError, json.JSONDecodeError):
        log.exception("无法读取文件管理器账户文件")
        raise HTTPException(status_code=500, detail="账户数据不可用")

def _fm_save_users(data: dict) -> None:
    os.makedirs(os.path.dirname(FM_USERS_FILE), exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix="fm-users-", dir=os.path.dirname(FM_USERS_FILE), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temporary_path, FM_USERS_FILE)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)

def _fm_hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _fm_verify_password(password: str, password_hash: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False

def _fm_get_private_user_dir(username: str) -> str:
    return _fm_safe_path(os.path.join(PRIVATE_DOCS_DIR, "users"), username)

def _fm_get_private_user_vector_dir(username: str) -> str:
    return _fm_safe_path(os.path.join(VECTOR_DIR, "private_users"), username)

def _fm_get_collection_dir(collection: str, payload: dict) -> str:
    """返回当前登录用户在指定集合可访问的根目录。"""
    mapping = {"public": PUBLIC_DOCS_DIR, "data": DATA_DOCS_DIR}
    if collection in mapping:
        d = mapping[collection]
    elif collection == "private":
        d = PRIVATE_DOCS_DIR if payload.get("role") == "admin" else _fm_get_private_user_dir(payload.get("sub", ""))
    else:
        raise HTTPException(status_code=400, detail="仅支持 public、data 和 private 集合")
    os.makedirs(d, exist_ok=True)
    return d

def _fm_safe_path(base_dir: str, rel_path: str) -> str:
    """安全拼接路径，防止路径穿越"""
    base = os.path.abspath(os.path.normpath(base_dir))
    target = os.path.abspath(os.path.normpath(os.path.join(base, rel_path)))
    try:
        is_safe = os.path.commonpath([base, target]) == base
    except ValueError:
        is_safe = False
    if not is_safe:
        raise HTTPException(status_code=400, detail="非法路径")
    return target

def _fm_build_tree(base_dir: str, relative_path: str = "") -> list:
    """递归构建文件夹树，仅显示支持的文件格式"""
    nodes = []
    full_path = _fm_safe_path(base_dir, relative_path) if relative_path else base_dir
    if not os.path.exists(full_path):
        return nodes
    try:
        items = sorted(os.listdir(full_path))
    except PermissionError:
        return nodes
    for name in items:
        item_path = os.path.join(full_path, name)
        rel = f"{relative_path}/{name}".lstrip("/") if relative_path else name
        if os.path.isdir(item_path):
            children = _fm_build_tree(base_dir, rel)
            nodes.append(FileNode(name=name, type="folder", path=rel, children=children))
        else:
            ext = os.path.splitext(name)[1].lower()
            if ext in (".txt", ".md", ".pdf", ".docx"):
                nodes.append(FileNode(name=name, type="file", path=rel, children=None))
    return nodes

def verify_fm_token(request: Request, required_role: str = "uploader"):
    """验证文件管理器 JWT token"""
    if not FM_ENABLED:
        raise HTTPException(status_code=503, detail="文件管理器未配置管理员密码")
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth:
        raise HTTPException(status_code=401, detail="请先登录")
    payload = _fm_verify_jwt(auth)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    role = payload.get("role", "")
    if required_role == "admin" and role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if required_role == "uploader" and role not in ("uploader", "admin"):
        raise HTTPException(status_code=403, detail="需要上传权限")
    return payload


# ======================================================================
# RAG Engine (FAISS)
# ======================================================================
class RAGEngine:
    def __init__(self):
        self.embeddings = None
        self.private_store = None
        self.public_store = None
        self.novel_store = None
        self.data_store = None
        self.private_user_stores = {}
        self.private_user_locks = {}
        self.private_user_locks_guard = threading.Lock()
        self.is_ready = False

    def initialize(self):
        self._load_embedding_model()
        self._load_stores()

    def _load_embedding_model(self):
        log.info("加载嵌入模型: %s", EMBEDDING_MODEL)
        from langchain_huggingface import HuggingFaceEmbeddings
        self.embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL, model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        log.info("嵌入模型加载完成")

    def _load_stores(self):
        from langchain_community.vectorstores import FAISS

        for name, attr, docs_dir in [
            ("private", "private_store", PRIVATE_DOCS_DIR),
            ("public",  "public_store",  PUBLIC_DOCS_DIR),
            ("novel",   "novel_store",   NOVEL_DOCS_DIR),
            ("data",    "data_store",    DATA_DOCS_DIR),
        ]:
            faiss_dir = os.path.join(VECTOR_DIR, name)
            index_file = os.path.join(faiss_dir, "index.faiss")
            if os.path.exists(index_file):
                store = FAISS.load_local(faiss_dir, self.embeddings, allow_dangerous_deserialization=True)
                setattr(self, attr, store)
                log.info("[%s] FAISS 已加载: %d 条", name, store.index.ntotal)
            else:
                self._rebuild(name, docs_dir, attr)

        self.is_ready = True

    def _rebuild(self, name: str, docs_dir: str, attr: str):
        from langchain_community.vectorstores import FAISS
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        docs = self._load_docs(docs_dir, name)
        if not docs:
            log.warning("[%s] 目录为空，跳过", name)
            return

        cfg = CHUNK_CONFIG.get(name, {"size": CHUNK_SIZE, "overlap": CHUNK_OVERLAP})
        c_size, c_overlap = cfg["size"], cfg["overlap"]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=c_size, chunk_overlap=c_overlap,
            separators=["\n\n", "\n", "。", ".", "；", ";", "，", ",", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        log.info("[%s] 分块: %d 个 (size=%d, overlap=%d)", name, len(chunks), c_size, c_overlap)

        faiss_dir = os.path.join(VECTOR_DIR, name)
        if os.path.exists(faiss_dir):
            shutil.rmtree(faiss_dir)
        os.makedirs(faiss_dir, exist_ok=True)

        store = FAISS.from_documents(chunks, self.embeddings)
        store.save_local(faiss_dir)
        setattr(self, attr, store)
        log.info("[%s] FAISS 索引已保存: %d 条", name, store.index.ntotal)

    def rebuild(self, name: str):
        mapping = {"private": (PRIVATE_DOCS_DIR, "private_store"),
                   "public":  (PUBLIC_DOCS_DIR,  "public_store"),
                   "novel":   (NOVEL_DOCS_DIR,   "novel_store"),
                   "data":    (DATA_DOCS_DIR,    "data_store")}
        docs_dir, attr = mapping[name]
        self._rebuild(name, docs_dir, attr)

    def rebuild_all(self):
        self.rebuild("private")
        self.rebuild("public")
        self.rebuild("novel")
        self.rebuild("data")

    def _private_user_lock(self, username: str):
        with self.private_user_locks_guard:
            return self.private_user_locks.setdefault(username, threading.Lock())

    def rebuild_private_user(self, username: str):
        """只重建某位用户的 private 索引，绝不扫描其他用户目录。"""
        from langchain_community.vectorstores import FAISS
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        with self._private_user_lock(username):
            docs_dir = _fm_get_private_user_dir(username)
            vector_dir = _fm_get_private_user_vector_dir(username)
            docs = self._load_docs(docs_dir, f"private:{username}")
            if not docs:
                self.private_user_stores.pop(username, None)
                if os.path.exists(vector_dir):
                    shutil.rmtree(vector_dir)
                return {"documents": 0, "chunks": 0}

            cfg = CHUNK_CONFIG["private"]
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=cfg["size"], chunk_overlap=cfg["overlap"],
                separators=["\n\n", "\n", "。", ".", "；", ";", "，", ",", " ", ""],
            )
            chunks = splitter.split_documents(docs)
            store = FAISS.from_documents(chunks, self.embeddings)
            temporary_dir = f"{vector_dir}.tmp-{uuid.uuid4().hex}"
            os.makedirs(os.path.dirname(vector_dir), exist_ok=True)
            store.save_local(temporary_dir)
            if os.path.exists(vector_dir):
                shutil.rmtree(vector_dir)
            os.replace(temporary_dir, vector_dir)
            self.private_user_stores[username] = store
            log.info("[private:%s] 用户索引已更新: %d 条", username, store.index.ntotal)
            return {"documents": len(docs), "chunks": len(chunks)}

    def _get_private_user_store(self, username: str):
        store = self.private_user_stores.get(username)
        if store is not None:
            return store
        from langchain_community.vectorstores import FAISS
        vector_dir = _fm_get_private_user_vector_dir(username)
        if not os.path.exists(os.path.join(vector_dir, "index.faiss")):
            return None
        store = FAISS.load_local(vector_dir, self.embeddings, allow_dangerous_deserialization=True)
        self.private_user_stores[username] = store
        return store

    def search_private_user(self, username: str, query: str, top_k: int) -> SearchResponse:
        if not self.is_ready:
            raise HTTPException(status_code=503, detail="知识库未就绪")
        store = self._get_private_user_store(username)
        if store is None:
            return SearchResponse(query=query, collection="private", total_results=0, results=[])
        results = store.similarity_search_with_score(query, k=min(top_k, 10))
        items = [
            SearchResultItem(
                rank=i + 1,
                similarity=round(1.0 - min(score, 1.0), 4),
                source=os.path.basename(doc.metadata.get("source", "unknown")),
                content=doc.page_content.strip(),
            )
            for i, (doc, score) in enumerate(results)
        ]
        return SearchResponse(query=query, collection="private", total_results=len(items), results=items)

    def search(self, collection: str, query: str, top_k: int) -> SearchResponse:
        if not self.is_ready:
            raise HTTPException(status_code=503, detail="知识库未就绪")
        store_map = {"private": self.private_store, "public": self.public_store, "novel": self.novel_store, "data": self.data_store}
        store = store_map.get(collection)
        if store is None:
            raise HTTPException(status_code=503, detail=f"{collection} 知识库未就绪")
        results = store.similarity_search_with_score(query, k=min(top_k, 10))
        items = []
        for i, (doc, score) in enumerate(results):
            items.append(SearchResultItem(
                rank=i + 1,
                similarity=round(1.0 - min(score, 1.0), 4),
                source=os.path.basename(doc.metadata.get("source", "unknown")),
                content=doc.page_content.strip(),
            ))
        return SearchResponse(query=query, collection=collection, total_results=len(items), results=items)

    def _load_docs(self, docs_dir: str, label: str):
        if not os.path.exists(docs_dir):
            os.makedirs(docs_dir, exist_ok=True)
            return []
        from langchain_community.document_loaders import TextLoader
        all_docs = []
        for ext in ["*.txt", "*.md"]:
            for f in Path(docs_dir).glob(f"**/{ext}"):
                try:
                    loader = TextLoader(str(f), encoding="utf-8")
                    all_docs.extend(loader.load())
                except Exception as e:
                    log.warning("跳过 %s: %s", f.name, e)
        pdfs = list(Path(docs_dir).glob("**/*.pdf"))
        if pdfs:
            try:
                from langchain_community.document_loaders import PyPDFLoader
                for f in pdfs:
                    try:
                        all_docs.extend(PyPDFLoader(str(f)).load())
                    except Exception as e:
                        log.warning("跳过 PDF %s: %s", f.name, e)
            except ImportError:
                log.warning("缺少 pypdf")
        docxs = list(Path(docs_dir).glob("**/*.docx"))
        if docxs:
            try:
                from langchain_community.document_loaders import Docx2txtLoader
                for f in docxs:
                    try:
                        all_docs.extend(Docx2txtLoader(str(f)).load())
                    except Exception as e:
                        log.warning("跳过 DOCX %s: %s", f.name, e)
            except ImportError:
                log.warning("缺少 docx2txt")
        log.info("[%s] 加载文档: %d 份", label, len(all_docs))
        return all_docs

    def store_count(self, store) -> int:
        try:
            return store.index.ntotal
        except Exception:
            return 0

    def list_docs(self, collection: str) -> list[str]:
        mapping = {"private": PRIVATE_DOCS_DIR, "public": PUBLIC_DOCS_DIR, "novel": NOVEL_DOCS_DIR, "data": DATA_DOCS_DIR}
        d = mapping.get(collection, "")
        if not d or not os.path.exists(d):
            return []
        return sorted([f.name for f in Path(d).iterdir() if f.is_file()])

    def add_document(self, collection: str, file_path: str) -> str:
        mapping = {"private": PRIVATE_DOCS_DIR, "public": PUBLIC_DOCS_DIR, "novel": NOVEL_DOCS_DIR, "data": DATA_DOCS_DIR}
        d = mapping[collection]
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, os.path.basename(file_path))
        shutil.copy2(file_path, dest)
        return os.path.basename(file_path)

    def delete_document(self, collection: str, filename: str):
        mapping = {"private": PRIVATE_DOCS_DIR, "public": PUBLIC_DOCS_DIR, "novel": NOVEL_DOCS_DIR, "data": DATA_DOCS_DIR}
        d = mapping.get(collection, "")
        fp = os.path.join(d, filename)
        if not os.path.exists(fp):
            raise HTTPException(status_code=404, detail=f"文档不存在: {filename}")
        os.remove(fp)


# ======================================================================
# FastAPI
# ======================================================================
app = FastAPI(title="RAG 知识库 (FAISS)", version="3.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
rag = RAGEngine()


@app.on_event("startup")
async def startup():
    log.info("=" * 55)
    log.info("  RAG 知识库 v3.1 (FAISS) 启动中...")
    log.info("  Private: %s  Public: %s  Novel: %s  Data: %s  Vector: %s",
             PRIVATE_DOCS_DIR, PUBLIC_DOCS_DIR, NOVEL_DOCS_DIR, DATA_DOCS_DIR, VECTOR_DIR)
    log.info("  模型: %s  分块: %d/%d", EMBEDDING_MODEL, CHUNK_SIZE, CHUNK_OVERLAP)
    log.info("  Keys: Admin=%s Research=%s Langbot=%s Public=%s Ingest=%s",
             "✓" if ADMIN_KEY else "✗", "✓" if RESEARCH_KEY else "✗",
             "✓" if LANGBOT_KEY else "✗", "✓" if PUBLIC_KEY else "✗",
             "✓" if INGEST_KEY else "✗")
    log.info("  文件管理器: Admin=%s UsersFile=%s",
             "✓" if FM_ADMIN_PASSWORD else "✗", FM_USERS_FILE)
    log.info("=" * 55)
    rag.initialize()


@app.exception_handler(HTTPException)
async def http_exc(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# --- Health/Stats ---
@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/stats", response_model=StatsResponse)
async def stats(request: Request):
    verify_key(request, "langbot")
    return StatsResponse(
        status="ready" if rag.is_ready else "not_ready",
        private_records=rag.store_count(rag.private_store) if rag.private_store else 0,
        public_records=rag.store_count(rag.public_store) if rag.public_store else 0,
        novel_records=rag.store_count(rag.novel_store) if rag.novel_store else 0,
        data_records=rag.store_count(rag.data_store) if rag.data_store else 0,
        embedding_model=EMBEDDING_MODEL,
        chunk_config={
            k: ChunkParam(size=v["size"], overlap=v["overlap"])
            for k, v in CHUNK_CONFIG.items()
        },
    )


# --- Search ---
@app.post("/api/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest):
    key_level = verify_key(request, "langbot")
    collection = request.query_params.get("collection", "public")
    allowed = COLLECTION_ACCESS.get(key_level, set())
    if collection not in allowed:
        raise HTTPException(status_code=403, detail=f"{key_level} Key 无权访问 {collection} 库")
    return rag.search(collection, body.query, body.top_k)


@app.post("/api/admin/search", response_model=SearchResponse)
async def admin_search(request: Request, body: SearchRequest, collection: str = Query(...)):
    """管理员专用检索接口，可访问包括完整 private 在内的全部集合。"""
    key_level = verify_key(request, "admin")
    if key_level != KeyLevel.ADMIN or collection not in ("public", "data", "novel", "private"):
        raise HTTPException(status_code=403, detail="仅管理员 Key 可访问该集合")
    return rag.search(collection, body.query, body.top_k)


@app.post("/api/me/search", response_model=SearchResponse)
async def search_my_collections(request: Request, body: SearchRequest, collection: str = Query(...)):
    """供个人 Agent 使用：仅可检索 public、data 与当前用户 private。"""
    payload = _fm_verify_agent_token(request)
    scope = payload.get("scope", [])
    if collection not in ("public", "data", "private") or f"{collection}:search" not in scope:
        raise HTTPException(status_code=403, detail="个人 Agent Token 无权访问该知识库")
    if collection == "private":
        return rag.search_private_user(payload["sub"], body.query, body.top_k)
    return rag.search(collection, body.query, body.top_k)


@app.get("/api/search", response_model=SearchResponse)
async def search_get(request: Request, q: str = Query(...), collection: str = Query("public"), top_k: int = Query(DEFAULT_TOP_K)):
    key_level = verify_key(request, "langbot")
    allowed = COLLECTION_ACCESS.get(key_level, set())
    if collection not in allowed:
        raise HTTPException(status_code=403, detail=f"{key_level} Key 无权访问 {collection} 库")
    return rag.search(collection, q, top_k)


# --- Source documents (API Key authorization required) ---
@app.get("/api/source-documents/{collection}")
async def list_source_documents(request: Request, collection: str):
    _verify_source_document_access(request, collection)
    return {"collection": collection, "documents": _list_source_documents(collection)}


@app.get("/api/source-documents/{collection}/{document_path:path}")
async def read_source_document(request: Request, collection: str, document_path: str):
    _verify_source_document_access(request, collection)
    target = _source_document_path(collection, document_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="原始文档不存在")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", filename=target.name)


@app.put("/api/source-documents/{collection}/{document_path:path}")
async def update_source_document(request: Request, collection: str, document_path: str, body: SourceDocumentUpdateRequest):
    _verify_source_document_access(request, collection)
    target = _source_document_path(collection, document_path)
    if target.suffix.lower() not in (".txt", ".md"):
        raise HTTPException(status_code=400, detail="PDF/DOCX 等非文本原文不可直接文本编辑，请通过上传替换文件")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="原始文档不存在")
    try:
        target.write_text(body.content, encoding="utf-8")
    except OSError:
        log.exception("无法更新原文: %s", target)
        raise HTTPException(status_code=500, detail="原始文档更新失败")
    return {"status": "ok", "collection": collection, "document_path": document_path, "hint": "原文已更新，需要重建索引；系统不会自动重建。"}


# --- Documents (原有 API，保持不变) ---
@app.get("/api/documents", response_model=DocListResponse)
async def list_docs(request: Request):
    verify_key(request, "admin")
    return DocListResponse(
        private=rag.list_docs("private"),
        public=rag.list_docs("public"),
        novel=rag.list_docs("novel"),
        data=rag.list_docs("data")
    )


@app.post("/api/documents/upload")
async def upload(request: Request, file: UploadFile = File(...), collection: str = Query("public")):
    verify_key(request, "ingest")
    if collection not in ("public", "private", "novel", "data"):
        raise HTTPException(status_code=400)
    ext = Path(file.filename).suffix.lower()
    if ext not in (".txt", ".md", ".pdf", ".docx"):
        raise HTTPException(status_code=400, detail=f"不支持的格式: {ext}")
    mapping = {"private": PRIVATE_DOCS_DIR, "public": PUBLIC_DOCS_DIR, "novel": NOVEL_DOCS_DIR, "data": DATA_DOCS_DIR}
    d = mapping[collection]
    os.makedirs(d, exist_ok=True)
    safe = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    with open(os.path.join(d, safe), "wb") as f:
        f.write(await file.read())
    return {"status": "ok", "filename": safe, "collection": collection, "hint": "请调用 POST /api/admin/rebuild 重建索引"}


@app.delete("/api/documents/{collection}/{filename}")
async def delete_doc(request: Request, collection: str, filename: str):
    verify_key(request, "admin")
    if collection not in ("public", "private", "novel", "data"):
        raise HTTPException(status_code=400)
    rag.delete_document(collection, filename)
    return {"status": "ok", "message": f"[{collection}] 已删除: {filename}"}


# --- Admin ---
@app.post("/api/admin/rebuild")
async def rebuild(request: Request, collection: str = Query("all")):
    verify_key(request, "admin")
    if collection == "all":
        rag.rebuild_all()
    elif collection in ("public", "private", "novel", "data"):
        rag.rebuild(collection)
    else:
        raise HTTPException(status_code=400)
    return {"status": "ok", "message": f"[{collection}] 索引重建完成"}


# --- MCP SSE ---
@app.get("/sse")
async def sse():
    from sse_starlette.sse import EventSourceResponse
    async def gen():
        yield {"event": "endpoint", "data": "/messages"}
    return EventSourceResponse(gen())


@app.post("/messages")
async def mcp(request: Request):
    body = await request.json()
    method = body.get("method", "")
    rid = body.get("id")
    api_key = request.headers.get("X-API-Key", "")
    kl = None
    if ADMIN_KEY and api_key == ADMIN_KEY:
        kl = KeyLevel.ADMIN
    elif RESEARCH_KEY and api_key == RESEARCH_KEY:
        kl = KeyLevel.RESEARCH
    elif LANGBOT_KEY and api_key == LANGBOT_KEY:
        kl = KeyLevel.LANGBOT
    elif PUBLIC_KEY and api_key == PUBLIC_KEY:
        kl = KeyLevel.PUBLIC

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "rag-kb", "version": "3.1.0"}, "capabilities": {"tools": {}}}}

    if method == "tools/list":
        tools = [
            {"name": "search_public_knowledge", "description": "检索公开知识库", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}},
            {"name": "search_novel_knowledge", "description": "检索小说/创作知识库（架空世界观、角色设定、情节等内容）", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}},
            {"name": "search_data_knowledge", "description": "检索研究数据知识库（论文、数据、参考资料等）", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}},
        ]
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}

    if method == "tools/call":
        params = body.get("params", {})
        tn = params.get("name", "")
        args = params.get("arguments", {})
        try:
            if tn == "search_public_knowledge":
                r = rag.search("public", args.get("query", ""), args.get("top_k", 5))
            elif tn == "search_novel_knowledge":
                r = rag.search("novel", args.get("query", ""), args.get("top_k", 5))
            elif tn == "search_data_knowledge":
                r = rag.search("data", args.get("query", ""), args.get("top_k", 5))
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"未知: {tn}"}}
            parts = [f"检索({r.collection}): {r.query}\n"]
            for x in r.results:
                parts.append(f"--- {x.rank} ({x.similarity:.0%}) ---\n{x.source}\n{x.content}\n")
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "\n".join(parts)}]}}
        except HTTPException as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"错误: {e.detail}"}]}}

    if method == "notifications/initialized":
        return {}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"未知方法: {method}"}}


# ======================================================================
# 文件管理器 API（账户认证，public / data / private）
# ======================================================================

@app.post("/api/auth/register", response_model=LoginResponse, status_code=201)
async def fm_register(body: RegisterRequest):
    """仅注册访客账户，并为其创建独立的 private 文件夹。"""
    if not FM_ENABLED:
        raise HTTPException(status_code=503, detail="文件管理器未配置管理员密码")
    username = _fm_normalize_username(body.username)
    _fm_validate_password(body.password)
    if username == FM_ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="该账号不可注册")

    users_data = _fm_load_users()
    if username in users_data["users"]:
        raise HTTPException(status_code=409, detail="账号已存在")
    users_data["users"][username] = {
        "password_hash": _fm_hash_password(body.password),
        "created_at": datetime.utcnow().isoformat(),
        "enabled": True,
    }
    _fm_save_users(users_data)
    os.makedirs(_fm_get_private_user_dir(username), exist_ok=True)
    log.info("[FM] 注册访客账户: %s", username)
    return LoginResponse(token=_fm_create_jwt(username, "uploader"), role="uploader", username=username)


@app.post("/api/auth/login", response_model=LoginResponse)
async def fm_login(body: LoginRequest):
    """文件管理器账号密码登录。管理员账号只可由服务端环境变量配置。"""
    if not FM_ENABLED:
        raise HTTPException(status_code=503, detail="文件管理器未配置管理员密码")
    username = _fm_normalize_username(body.username)
    password = body.password
    if username == FM_ADMIN_USERNAME:
        if password != FM_ADMIN_PASSWORD:
            raise HTTPException(status_code=403, detail="账号或密码错误")
        role = "admin"
    else:
        user = _fm_load_users()["users"].get(username)
        if not user or not user.get("enabled") or not _fm_verify_password(password, user.get("password_hash", "")):
            raise HTTPException(status_code=403, detail="账号或密码错误")
        role = "uploader"
    return LoginResponse(token=_fm_create_jwt(username, role), role=role, username=username)


@app.post("/api/me/private/agent-token", response_model=AgentTokenResponse)
async def create_my_agent_token(request: Request):
    """生成可配置给个人 Agent 的短期检索 Token。"""
    payload = verify_fm_token(request, "uploader")
    username = payload["sub"]
    token = _fm_create_agent_token(username)
    expires_at = (datetime.utcnow() + timedelta(days=FM_AGENT_TOKEN_EXPIRY_DAYS)).isoformat()
    return AgentTokenResponse(token=token, expires_at=expires_at)


@app.post("/api/me/rebuild")
async def rebuild_my_collections(request: Request):
    """访客更新 public、data 及自己 private 的索引；私有索引保持用户隔离。"""
    payload = verify_fm_token(request, "uploader")
    if payload.get("role") == "admin":
        raise HTTPException(status_code=400, detail="管理员请使用当前库的确认按钮")
    rag.rebuild("public")
    rag.rebuild("data")
    private_result = rag.rebuild_private_user(payload["sub"])
    return {
        "status": "ok",
        "message": "Public、Data 与个人私密索引已更新",
        "private": private_result,
    }


@app.get("/api/browse/{collection}", response_model=BrowseResponse)
async def fm_browse(request: Request, collection: str):
    """浏览当前用户在集合中有权限访问的文件夹树。"""
    payload = verify_fm_token(request, "uploader")
    base_dir = _fm_get_collection_dir(collection, payload)
    tree = _fm_build_tree(base_dir)
    return BrowseResponse(collection=collection, tree=tree)


@app.post("/api/browse/upload")
async def fm_upload(
    request: Request,
    file: UploadFile = File(...),
    collection: str = Query(..., description="public、data 或 private"),
    folder: str = Query("", description="目标子目录，空字符串表示根目录"),
):
    """上传文件到指定集合的指定文件夹"""
    payload = verify_fm_token(request, "uploader")
    if payload.get("role") != "admin" and collection != "private":
        raise HTTPException(status_code=403, detail="访客仅可上传到自己的 Private 库")
    base_dir = _fm_get_collection_dir(collection, payload)

    original_name = Path(file.filename or "").name
    ext = Path(original_name).suffix.lower()
    if ext not in (".txt", ".md", ".pdf", ".docx"):
        raise HTTPException(status_code=400, detail=f"不支持的格式: {ext}，仅支持 .txt .md .pdf .docx")

    target_dir = _fm_safe_path(base_dir, folder) if folder else base_dir
    os.makedirs(target_dir, exist_ok=True)

    if not original_name or original_name in (".", ".."):
        raise HTTPException(status_code=400, detail="非法文件名")
    safe_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
    dest = os.path.join(target_dir, safe_name)
    with open(dest, "wb") as f:
        f.write(await file.read())

    rel = f"{folder}/{safe_name}".lstrip("/") if folder else safe_name
    log.info("[FM] 上传: [%s] %s", collection, rel)
    return {
        "status": "ok",
        "filename": safe_name,
        "collection": collection,
        "folder": folder,
        "hint": "上传完成。请更新索引后让个人 Agent 使用新文件。" if collection == "private" else "上传完成。管理员需重建索引才能检索新文件。",
    }


@app.post("/api/browse/folder")
async def fm_create_folder(
    request: Request,
    collection: str = Query(..., description="public、data 或 private"),
    path: str = Query(..., description="以 / 分隔的路径，如 '子目录/新文件夹'"),
):
    """管理员可在全部集合建目录；访客仅可在自己的 private 目录建目录。"""
    payload = verify_fm_token(request, "uploader")
    if payload.get("role") != "admin" and collection != "private":
        raise HTTPException(status_code=403, detail="访客仅可管理自己的私密文件夹")
    base_dir = _fm_get_collection_dir(collection, payload)
    target = _fm_safe_path(base_dir, path)
    os.makedirs(target, exist_ok=True)
    log.info("[FM] 创建文件夹: [%s] %s", collection, path)
    return {"status": "ok", "path": path, "collection": collection}


@app.delete("/api/browse/delete")
async def fm_delete(
    request: Request,
    collection: str = Query(..., description="public、data 或 private"),
    path: str = Query(..., description="要删除的文件或文件夹路径"),
):
    """管理员可删除全部内容；访客仅可删除自己的 private 内容。"""
    payload = verify_fm_token(request, "uploader")
    if payload.get("role") != "admin" and collection != "private":
        raise HTTPException(status_code=403, detail="访客仅可删除自己的私密文件")
    base_dir = _fm_get_collection_dir(collection, payload)
    target = _fm_safe_path(base_dir, path)

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="路径不存在")

    if os.path.isdir(target):
        if os.listdir(target):
            raise HTTPException(status_code=400, detail="文件夹不为空，请先删除其中的文件")
        os.rmdir(target)
    else:
        os.remove(target)

    log.info("[FM] 删除: [%s] %s", collection, path)
    return {"status": "ok", "deleted": path, "collection": collection}


@app.get("/api/browse/download")
async def fm_download(
    request: Request,
    collection: str = Query(..., description="public、data 或 private"),
    path: str = Query(..., description="文件路径"),
):
    """下载/预览文件"""
    payload = verify_fm_token(request, "uploader")
    base_dir = _fm_get_collection_dir(collection, payload)
    target = _fm_safe_path(base_dir, path)

    if not os.path.exists(target) or os.path.isdir(target):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(target, filename=os.path.basename(path))


@app.post("/api/browse/rebuild/{collection}")
async def fm_rebuild(request: Request, collection: str):
    """重建指定集合的 FAISS 索引（管理员权限）。"""
    verify_fm_token(request, "admin")
    if collection not in ("public", "data", "private"):
        raise HTTPException(status_code=400, detail="仅支持 public、data 和 private")
    log.info("[FM] 重建索引: %s", collection)
    rag.rebuild(collection)
    return {"status": "ok", "message": f"[{collection}] 索引重建完成"}


# ======================================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", type=str, default="0.0.0.0")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port)
