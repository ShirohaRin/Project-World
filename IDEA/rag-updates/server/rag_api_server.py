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
import uuid
import secrets as _secrets
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
FM_UPLOAD_PASSWORD = os.environ.get("RAG_FM_UPLOAD_PASSWORD", "")
FM_ADMIN_PASSWORD  = os.environ.get("RAG_FM_ADMIN_PASSWORD", "")
FM_JWT_SECRET      = os.environ.get("RAG_FM_JWT_SECRET", "")
FM_JWT_EXPIRY_HOURS = int(os.environ.get("RAG_FM_JWT_EXPIRY_HOURS", "24"))
FM_ENABLED = bool(FM_UPLOAD_PASSWORD or FM_ADMIN_PASSWORD)

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

# ========== 文件管理器 Models ==========
class LoginRequest(BaseModel):
    password: str

class LoginResponse(BaseModel):
    token: str
    role: str

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
    KeyLevel.ADMIN:    {"private", "public", "novel", "data"},
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


# ======================================================================
# 文件管理器 JWT 认证
# ======================================================================
def _fm_create_jwt(role: str) -> str:
    payload = {
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

def _fm_get_collection_dir(collection: str) -> str:
    """文件管理器仅允许 public 和 data 集合"""
    mapping = {"public": PUBLIC_DOCS_DIR, "data": DATA_DOCS_DIR}
    if collection not in mapping:
        raise HTTPException(status_code=400, detail=f"仅支持 public 和 data 集合")
    d = mapping[collection]
    os.makedirs(d, exist_ok=True)
    return d

def _fm_safe_path(base_dir: str, rel_path: str) -> str:
    """安全拼接路径，防止路径穿越"""
    base = os.path.normpath(base_dir)
    target = os.path.normpath(os.path.join(base, rel_path))
    if not target.startswith(base):
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
        return  # 未配置密码时跳过认证
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
    return role


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
    log.info("  文件管理器: Upload=%s Admin=%s",
             "✓" if FM_UPLOAD_PASSWORD else "✗",
             "✓" if FM_ADMIN_PASSWORD else "✗")
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


@app.get("/api/search", response_model=SearchResponse)
async def search_get(request: Request, q: str = Query(...), collection: str = Query("public"), top_k: int = Query(DEFAULT_TOP_K)):
    key_level = verify_key(request, "langbot")
    allowed = COLLECTION_ACCESS.get(key_level, set())
    if collection not in allowed:
        raise HTTPException(status_code=403, detail=f"{key_level} Key 无权访问 {collection} 库")
    return rag.search(collection, q, top_k)


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
        if kl == KeyLevel.ADMIN:
            tools.append({"name": "search_private_knowledge", "description": "检索私有知识库（仅 ADMIN）", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}})
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
            elif tn == "search_private_knowledge":
                if kl != KeyLevel.ADMIN:
                    raise HTTPException(status_code=403)
                r = rag.search("private", args.get("query", ""), args.get("top_k", 5))
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
# 文件管理器 API (public + data，JWT 密码认证)
# ======================================================================

@app.post("/api/auth/login", response_model=LoginResponse)
async def fm_login(body: LoginRequest):
    """文件管理器登录。上传密码 → uploader 角色，管理员密码 → admin 角色。"""
    if not FM_ENABLED:
        raise HTTPException(status_code=503, detail="文件管理器未配置密码")
    pwd = body.password.strip()
    if FM_ADMIN_PASSWORD and pwd == FM_ADMIN_PASSWORD:
        token = _fm_create_jwt("admin")
        return LoginResponse(token=token, role="admin")
    if FM_UPLOAD_PASSWORD and pwd == FM_UPLOAD_PASSWORD:
        token = _fm_create_jwt("uploader")
        return LoginResponse(token=token, role="uploader")
    raise HTTPException(status_code=403, detail="密码错误")


@app.get("/api/browse/{collection}", response_model=BrowseResponse)
async def fm_browse(request: Request, collection: str):
    """浏览集合的完整文件夹树（仅 public / data）"""
    verify_fm_token(request, "uploader")
    base_dir = _fm_get_collection_dir(collection)
    tree = _fm_build_tree(base_dir)
    return BrowseResponse(collection=collection, tree=tree)


@app.post("/api/browse/upload")
async def fm_upload(
    request: Request,
    file: UploadFile = File(...),
    collection: str = Query(..., description="public 或 data"),
    folder: str = Query("", description="目标子目录，空字符串表示根目录"),
):
    """上传文件到指定集合的指定文件夹"""
    verify_fm_token(request, "uploader")
    base_dir = _fm_get_collection_dir(collection)

    ext = Path(file.filename).suffix.lower()
    if ext not in (".txt", ".md", ".pdf", ".docx"):
        raise HTTPException(status_code=400, detail=f"不支持的格式: {ext}，仅支持 .txt .md .pdf .docx")

    target_dir = _fm_safe_path(base_dir, folder) if folder else base_dir
    os.makedirs(target_dir, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
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
        "hint": "上传完成。管理员需重建索引才能检索新文件。",
    }


@app.post("/api/browse/folder")
async def fm_create_folder(
    request: Request,
    collection: str = Query(..., description="public 或 data"),
    path: str = Query(..., description="以 / 分隔的路径，如 '子目录/新文件夹'"),
):
    """创建子文件夹（管理员权限）"""
    verify_fm_token(request, "admin")
    base_dir = _fm_get_collection_dir(collection)
    target = _fm_safe_path(base_dir, path)
    os.makedirs(target, exist_ok=True)
    log.info("[FM] 创建文件夹: [%s] %s", collection, path)
    return {"status": "ok", "path": path, "collection": collection}


@app.delete("/api/browse/delete")
async def fm_delete(
    request: Request,
    collection: str = Query(..., description="public 或 data"),
    path: str = Query(..., description="要删除的文件或文件夹路径"),
):
    """删除文件或空文件夹（管理员权限）"""
    verify_fm_token(request, "admin")
    base_dir = _fm_get_collection_dir(collection)
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
    collection: str = Query(..., description="public 或 data"),
    path: str = Query(..., description="文件路径"),
):
    """下载/预览文件"""
    verify_fm_token(request, "uploader")
    base_dir = _fm_get_collection_dir(collection)
    target = _fm_safe_path(base_dir, path)

    if not os.path.exists(target) or os.path.isdir(target):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(target, filename=os.path.basename(path))


@app.post("/api/browse/rebuild/{collection}")
async def fm_rebuild(request: Request, collection: str):
    """重建指定集合的 FAISS 索引（管理员权限，仅支持 public / data）"""
    verify_fm_token(request, "admin")
    if collection not in ("public", "data"):
        raise HTTPException(status_code=400, detail="仅支持 public 和 data")
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
