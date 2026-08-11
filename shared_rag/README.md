# 共享 RAG 知识库 — 架构文档

## 目录

1. [项目概览](#1-项目概览)
2. [核心架构](#2-核心架构)
3. [三库设计与权限模型](#3-三库设计与权限模型)
4. [分块策略](#4-分块策略)
5. [服务端详解](#5-服务端详解)
6. [客户端接入](#6-客户端接入)
7. [数据流全链路](#7-数据流全链路)
8. [部署](#8-部署)
9. [API 参考](#9-api-参考)

---

## 1. 项目概览

共享 RAG 知识库是一个**自部署的文档检索增强生成服务**，核心定位是：将你的文档（世界观设定、小说正文、私有资料等）向量化并存入库中，通过 REST API 或 MCP 协议对外提供语义检索能力，供 TRAE、LangBot（飞书机器人）等 AI 客户端在生成回答前注入相关上下文。

### 项目结构

```
shared_rag/
├── README.md                          ← 本文档
├── docker-compose.yml                 ← 简化版 Docker 部署（单 public 库）
│
├── server/                            ← 服务端核心
│   ├── rag_api_server.py              ← FastAPI 主程序（约 490 行）
│   ├── Dockerfile                     ← Docker 镜像构建
│   ├── docker-compose.yml             ← 完整 Docker 部署（三库）
│   ├── requirements.txt               ← Python 依赖
│   └── .env.template                  ← 环境变量模板
│
├── trae/                              ← TRAE IDE 接入层
│   ├── rag_mcp_proxy.py               ← MCP 代理（本地 stdio → 远程 HTTP）
│   ├── mcp_config.json                ← MCP 配置示例（LANGBOT Key）
│   ├── mcp_config_example.json        ← MCP 配置示例（ADMIN Key）
│   ├── rag_mcp_owner_admin.py         ← IDEA Owner 凭据项目管理 MCP
│   └── test_connection.py             ← 连通性测试脚本
│
├── langbot/                           ← LangBot（飞书）接入层
│   └── rag_plugin.py                  ← LangBot 插件
│
└── 服务器部署指南.md                   ← 分步部署教程
```

### 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 嵌入模型 | `BAAI/bge-small-zh-v1.5`（HuggingFace，中文优化） |
| 向量库 | FAISS（纯内存，无 SQLite 依赖） |
| 分块引擎 | LangChain `RecursiveCharacterTextSplitter` |
| 文档解析 | PyPDF（PDF）、docx2txt（DOCX）、TextLoader（TXT/MD） |
| 协议 | REST API + MCP over SSE + MCP over stdio |
| 部署 | Docker Compose |

---

## 2. 核心架构

整个系统分为**三个独立部署的组件**，之间通过 HTTP 通信：

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                        Windows 开发机                           │
 │  ┌──────────┐    stdio (MCP)    ┌──────────────────────┐        │
 │  │ TRAE IDE │ ◄──────────────► │ rag_mcp_proxy.py      │        │
 │  └──────────┘                   │ (协议适配层)          │        │
 │                                 └─────────┬────────────┘        │
 └───────────────────────────────────────────┼─────────────────────┘
                                              │ HTTP
                                              ▼
 ┌───────────────────────────────────────────┼─────────────────────┐
 │                        Linux 服务器（Docker）                   │
 │                                           │                     │
 │  ┌──────────────── rag-knowledge-server :8080 ───────────────┐ │
 │  │                  │                                        │ │
 │  │  ┌───────────┐  │  ┌──────────────┐  ┌────────────────┐  │ │
 │  │  │ FastAPI   │◄─┘  │  RAGEngine   │  │ FAISS 向量索引 │  │ │
 │  │  │ REST+MCP  │────►│  嵌入+检索   │──│ (持久化到磁盘) │  │ │
 │  │  └───────────┘     └──────┬───────┘  └────────────────┘  │ │
 │  │                          │                                │ │
 │  └──────────────────────────┼────────────────────────────────┘ │
 │                             │摄入                               │
 │            ┌────────────────┼────────────────┐                  │
 │            ▼                ▼                ▼                  │
 │     knowledge_private  knowledge_public  knowledge_novel        │
 │     (私有文档)         (公开设定)        (正文章节)             │
 └─────────────────────────────────────────────────────────────────┘
                                              ▲ HTTP
                                              │
 ┌───────────────────────────────────────────┼─────────────────────┐
 │                     LangBot 服务器                              │
 │  ┌──────────────┐        ┌────────────────────────┐            │
 │  │ 飞书消息 ────┼───────►│ rag_plugin.py           │            │
 │  │ (用户提问)   │        │ 拦截 → 检索 → 注入上下文 │            │
 │  └──────────────┘        └────────────────────────┘            │
 └─────────────────────────────────────────────────────────────────┘
```

| 组件 | 运行位置 | 职责 |
|------|----------|------|
| **服务端** | Linux 服务器（Docker） | 文档摄入、向量化、语义检索 |
| **MCP 代理** | Windows 开发机 | 将 TRAE 的 stdio MCP 请求转为 HTTP |
| **LangBot 插件** | LangBot 服务器 | 拦截飞书消息，自动检索后注入 LLM 上下文 |

### 为什么需要 MCP 代理？

TRAE 原生支持 stdio MCP 协议（通过标准输入输出与进程通信），而服务端暴露的是 HTTP REST API。`rag_mcp_proxy.py` 作为一个**协议适配层**运行在开发机上：

```
TRAE → [stdio] → MCP Proxy → [HTTP POST /api/search] → RAG Server
                                                          ↓
TRAE ← [stdio] ← MCP Proxy ← [JSON results]        ← RAG Server
```

### IDEA Owner 项目管理 MCP

`trae/rag_mcp_owner_admin.py` 仅调用 `/api/projects/...` 项目 API，使用本机 `RAG_IDEA_OWNER_TOKEN` 中的 IDEA `idea` capability credential 作为 Bearer Token；还必须由 RAG 服务配置的服务 Token 向 IDEA 完成二次授权。它仅支持 `public`、`data`、`novel` 项目集合，不使用 `RAG_ADMIN_KEY` 或 `RAG_INGEST_KEY`，项目列表仍由 IDEA 控制台管理。

---

## 3. 三库设计与权限模型

系统设有**三个独立的知识库集合**，各有独立的文档目录和向量存储：

```
       ADMIN Key              LANGBOT Key           INGEST Key
      (全权限)              (公开+小说)            (仅上传)
          │                      │                    │
          ▼                      ▼                    ▼
   ┌─────────────┐     ┌─────────────┐      ┌─────────────┐
   │   private   │     │   public    │      │    novel    │
   │   私有文档   │     │  公开设定    │      │  正文章节   │
   └─────────────┘     └─────────────┘      └─────────────┘
   仅 ADMIN 可查        ADMIN + LANGBOT       ADMIN + LANGBOT
```

### 各库定位

| 集合 | 目录 | 典型内容 | 检索权限 |
|------|------|----------|----------|
| **private** | `knowledge_private/` | 未发表的科研数据、内部规范、实验记录 | 仅 ADMIN Key |
| **public** | `knowledge_public/` | 世界观设定文档、事件追踪、组织结构化资料 | ADMIN + LANGBOT |
| **novel** | `knowledge_novel/` | 正文章节、叙事内容（大块文本） | ADMIN + LANGBOT |

### 三级 API Key 体系

| Key | 权限级别 | 用途 |
|-----|----------|------|
| `RAG_ADMIN_KEY` | 3（最高） | TRAE 开发机使用，可查全部三库、管理文档、重建索引 |
| `RAG_INGEST_KEY` | 2 | 文档上传（CI/CD 或自动化脚本用） |
| `RAG_LANGBOT_KEY` | 1 | LangBot（飞书）使用，仅可查 public + novel |

### 为什么把"正文"和"设定"分开？

正文（小说章节）和世界观设定文档的性质完全不同：

- **正文**：叙事流，连续性强，语义单元大（一次对话+环境描写=800~1500 中文词），需要大分块保持完整
- **设定**：结构化，YAML frontmatter + 层级标题，每个条目自包含，小块即可精确命中

分开后的好处：
1. 可配置**不同的分块参数**（见下文分块策略）
2. 检索时可分别命中，互不污染
3. 正文库可单独重建而不影响设定库的稳定性

---

## 4. 分块策略

分块是 RAG 系统最关键的性能因素之一。本系统采用**按集合差异化**的分块策略。

### 当前配置（代码级默认值）

| 集合 | 块大小（字符） | 重叠（字符） | 适用文档 |
|------|---------------|-------------|----------|
| **novel** | **2000** | **300** | 正文章节、叙事文本 |
| **public** | **1200** | **200** | 世界观设定、事件追踪 |
| **private** | **800** | **100** | 通用私有文档 |
| *全局* | *1200* | *200* | 兜底默认值 |

### 设计思路

**叙事文本（novel）— 2000 字符：**

```
┌────────────────────────────────────────────────────────────┐
│  "他把剑收回鞘中，转身看向窗外。雨已经停了，月光透过云层  │
│   的缝隙洒在石板路上。她还在生气，他知道。                  │
│   '我不是故意要瞒你的。'他的声音很低。                      │
│   '那你为什么不早说？'她终于转过身来，眼眶泛红。            │
│   '因为……我害怕失去你。'"                                  │
│                                                            │
│   ←—— 一个完整的对话回合 + 环境描写（约300中文词）           │
│   2000字符 ≈ 1000中文词，足以覆盖2-3个完整场景              │
└────────────────────────────────────────────────────────────┘
                         ↕ 300字符重叠
┌────────────────────────────────────────────────────────────┐
│  （承接上文）"她愣了一瞬，随即扑进他怀里。'笨蛋。'她闷闷   │
│   地说，眼泪打湿了他的胸口。窗外的世界仿佛静止了。远处传来  │
│   教堂的钟声，一声，两声，三声……"                           │
└────────────────────────────────────────────────────────────┘
```

**结构化文档（public）— 1200 字符：**

```
┌──────────────────────────────────┐
│  ## 白羽一族                    │
│  - 种族特征：白翼、银发...      │
│  - 核心能力：折跃...   ← 一个条目│
│                                  │
│  ## 创世教会                    │
│  - 教阶体系：教宗>...  ← 另一个 │
└──────────────────────────────────┘
         ↕ 200字符重叠（保证相邻条目不丢失）
```

### 为何 800 不够而 2000 合理

对于中文叙事正文，一个不被切断的"语义完整单元"通常需要：

- 1 次对话交换：200-500 中文词
- 加环境/动作铺垫：200-400 中文词
- 合计：400-900 中文词 ≈ **800-1800 字符**

800 字符必然把上述内容切为 2-3 块。Agent 检索时可能只命中"她还在生气"但找不到"我不是故意要瞒你的"，导致上下文割裂。2000 字符（约 1000 中文词）能保证绝大多数场景的完整性。

### 分块分隔符优先级

`RecursiveCharacterTextSplitter` 使用以下分隔符依次尝试切分：

```
separators = ["\n\n", "\n", "。", ".", "；", ";", "，", ",", " ", ""]
```

这意味着优先在**段落边界**（`\n\n`）切，其次在**句子边界**（`。`）切，最后才在字符处切。对中文叙事而言，`\n\n` 和 `。` 的高优先级保证了对话回合和句子的完整性——分块线几乎总是落在自然段落或句子末尾。

### 修改分块参数

有两种方式（优先级从高到低）：

**方式一：按集合独立设置（推荐）**

```bash
# .env 或 docker-compose.yml
RAG_CHUNK_SIZE_NOVEL=2000
RAG_CHUNK_OVERLAP_NOVEL=300
RAG_CHUNK_SIZE_PUBLIC=1200
RAG_CHUNK_OVERLAP_PUBLIC=200
```

**方式二：全局设置（影响所有集合的 fallback）**

```bash
RAG_CHUNK_SIZE=1200
RAG_CHUNK_OVERLAP=200
```

修改后必须**重建索引**才能生效：

```bash
curl -X POST "http://localhost:8080/api/admin/rebuild?collection=all" \
     -H "X-API-Key: 你的ADMIN_KEY"
```

### 环境变量优先级链

以 novel 集合为例：

```
RAG_CHUNK_SIZE_NOVEL env var  →  有则用，否则 ↓
RAG_CHUNK_SIZE env var        →  有则用，否则 ↓
代码内硬编码 "2000"           →  最终兜底
```

---

## 5. 服务端详解

服务端是系统的核心，一个约 490 行的 FastAPI 应用，核心类是 `RAGEngine`。

### 5.1 启动流程

```
服务器启动
    │
    ▼
加载嵌入模型（BGE-small-zh-v1.5，约 400MB，首次下载 2-5 分钟）
    │
    ▼
检查 FAISS 索引文件（vector_data/*/index.faiss）是否存在？
    │
    ├── 存在 → 直接加载（秒级）
    │
    └── 不存在 → _rebuild()
                   │
                   ├── _load_docs()  扫描目录下所有 .md/.txt/.pdf/.docx
                   ├── RecursiveCharacterTextSplitter 按集合配置分块
                   ├── FAISS.from_documents() 逐块向量化 + 建索引
                   └── store.save_local() 持久化到磁盘
```

### 5.2 检索流程

```
用户查询 "洛星雪的身世"
    │
    ▼
embedding.embed_query() → 查询向量
    │
    ▼
store.similarity_search_with_score() → 余弦相似度 Top-K
    │
    ▼
返回 [{ rank, similarity, source, content }, ...]
```

### 5.3 文档摄入

支持四种格式，递归扫描子目录：

| 格式 | 加载器 | 说明 |
|------|--------|------|
| `.md` / `.txt` | `TextLoader(encoding="utf-8")` | 递归扫描 |
| `.pdf` | `PyPDFLoader` | 需要 `pypdf` |
| `.docx` | `Docx2txtLoader` | 需要 `docx2txt` |

### 5.4 嵌入模型选择

默认使用 **`BAAI/bge-small-zh-v1.5`**：

- **中文优先**：BGE 系列是中文语义检索标杆模型
- **体积适中**：small 版本约 100MB 参数量，Docker 内 2GB 内存即可运行
- **无需 GPU**：纯 CPU 推理

如需换模型：

```bash
# 更小（约 80MB，英文为主）
RAG_EMBEDDING_MODEL=all-MiniLM-L6-v2

# 更大更准（约 400MB，中文极强）
RAG_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
```

---

## 6. 客户端接入

### 6.1 TRAE IDE（开发机）

**协议链路**：TRAE MCP → stdio → `rag_mcp_proxy.py` → HTTP → RAG Server

在 TRAE 设置 → MCP 中添加 stdio 类型：

```json
{
  "mcpServers": {
    "rag-knowledge-base": {
      "command": "python",
      "args": ["D:\\shared_rag\\trae\\rag_mcp_proxy.py"],
      "env": {
        "RAG_SERVER_URL": "http://你的服务器IP:8080",
        "RAG_ADMIN_KEY": "你的ADMIN_KEY"
      }
    }
  }
}
```

首次使用前用 `test_connection.py` 验证连通性：

```powershell
$env:RAG_SERVER_URL="http://你的服务器IP:8080"
$env:RAG_ADMIN_KEY="你的ADMIN_KEY"
python D:\shared_rag\trae\test_connection.py
```

**暴露的 MCP 工具**：

| 工具名 | 查询库 | 说明 |
|--------|--------|------|
| `search_public_knowledge` | public | 查世界观设定、事件追踪 |
| `search_novel_knowledge` | novel | 查正文章节、叙事内容 |
| `search_private_knowledge` | private | 查私有文档（仅 ADMIN） |
| `get_knowledge_base_info` | — | 查看各库状态和分块参数 |

### 6.2 LangBot（飞书机器人）

**协议链路**：飞书消息 → LangBot → `rag_plugin.py` → HTTP → RAG Server → LLM → 回复

**配置**：将 `langbot/rag_plugin.py` 放入 LangBot 的 `plugins/` 目录，在 Web 面板启用：

```yaml
rag_server_url: "http://localhost:8080"
rag_api_key: "你的LANGBOT_KEY"     # 注意：是 LANGBOT_KEY
rag_top_k: 5
rag_auto_trigger: true
```

**工作原理**：

```
飞书用户问："洛星雪的印记是什么"
        ↓
LangBot 接收消息
        ↓
rag_plugin.on_message() 自动拦截
        ↓
POST /api/search?collection=public  →  检索设定库
POST /api/search?collection=novel   →  检索正文库
        ↓
合并去重（按相似度降序）
        ↓
将检索结果拼接到消息末尾：
  【以下是知识库中检索到的相关文档...】
  [85%] 来源: 07_主要角色_主角团与学生.md
  洛星雪的印记：辰和伊瑟尔能看到雪身上的印记...
        ↓
LLM 基于上下文生成回答 → 回复飞书用户
```

**权限安全**：LangBot 使用 `LANGBOT_KEY`，无法检索 `private` 库，飞书用户看不到私有文档。

### 6.3 直接 HTTP 调用

任何能发 HTTP 请求的工具都可以直接调用：

```bash
# 检索公开发设定库
curl -X POST http://服务器IP:8080/api/search \
     -H "X-API-Key: 你的ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"query":"海德纳事件","top_k":5}'

# 检索正文库
curl -X POST "http://服务器IP:8080/api/search?collection=novel" \
     -H "X-API-Key: 你的LANGBOT_KEY" \
     -H "Content-Type: application/json" \
     -d '{"query":"罪西在矿难中做了什么","top_k":5}'
```

---

## 7. 数据流全链路

以下是一次完整的"TRAE 用户提问 → 知识库检索 → 增强回答"的数据流：

```
TRAE 用户: "洛星雪的身世是什么？"
    │
    ▼
TRAE AI 判断需要查询知识库
    │
    ├──► MCP Proxy (本地 stdio)
    │       │
    │       ▼ HTTP POST /api/search
    │    RAG Server (Docker)
    │       │
    │       ├── embed_query("洛星雪 身世") → 查询向量
    │       ├── FAISS.similarity_search(向量, k=5)
    │       └── 返回: [90% 07_主要角色.md, 82% 01_世界观基础设定.md, ...]
    │
    ├──► MCP Proxy (再次调用)
    │       │
    │       ▼ HTTP POST /api/search?collection=novel
    │    RAG Server
    │       │
    │       └── 返回: [88% 序章_第一章.md 片段, ...]
    │
    ▼
TRAE AI 综合两部分结果，生成回答:
  "洛星雪是从伯斯塔光柱异象中降临的知性生命体。
   教宗绯吉亚亲自下令销毁她的真实档案，由洛星宁收养。
   她身上有某种'印记'，辰和伊瑟尔都能看到..."
```

---

## 8. 部署

### 快速开始

```bash
# 1. 上传到服务器
scp -r D:\shared_rag\server user@服务器IP:/opt/

# 2. SSH 登录
ssh user@服务器IP
cd /opt/server

# 3. 创建目录和配置
mkdir -p knowledge_private knowledge_public knowledge_novel
cp .env.template .env
nano .env    # 填入三把 API Key

# 4. 启动
docker compose up -d --build

# 5. 上传文档后重建索引
curl -X POST "http://localhost:8080/api/admin/rebuild?collection=all" \
     -H "X-API-Key: 你的ADMIN_KEY"
```

### 服务器要求

| 资源 | 最低 | 推荐 |
|------|------|------|
| 内存 | 2 GB | 4 GB |
| 磁盘 | 5 GB | 10 GB+ |
| CPU | 2 核 | 4 核 |

完整的分步教程（SCP 上传、宝塔面板配置、故障排查等）见 [服务器部署指南.md](./服务器部署指南.md)。

---

## 9. API 参考

### 端点总览

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/health` | 公开 | 健康检查 |
| `GET` | `/api/stats` | LANGBOT+ | 各库状态、分块参数 |
| `POST` | `/api/search?collection=public` | LANGBOT+ | 检索公开库 |
| `POST` | `/api/search?collection=novel` | LANGBOT+ | 检索小说库 |
| `POST` | `/api/search?collection=private` | ADMIN | 检索私有库 |
| `GET` | `/api/documents` | ADMIN | 列出所有文档 |
| `POST` | `/api/documents/upload` | INGEST+ | 上传文档 |
| `DELETE` | `/api/documents/{collection}/{filename}` | ADMIN | 删除文档 |
| `POST` | `/api/admin/rebuild?collection=all` | ADMIN | 重建索引 |
| `GET` | `/sse` | 公开 | MCP SSE 端点 |
| `POST` | `/messages` | 公开 | MCP JSON-RPC 消息 |

### 检索请求

```json
{
  "query": "查询文本",
  "top_k": 5        // 1-10，默认 5
}
```

### 检索响应

```json
{
  "query": "查询文本",
  "collection": "novel",
  "total_results": 3,
  "results": [
    {
      "rank": 1,
      "similarity": 0.9134,
      "source": "06_第六章.md",
      "content": "伊瑟尔放下手中的茶杯..."
    }
  ]
}
```

- `similarity`：余弦相似度，0~1
- `content`：分块后的原始文本
- `source`：来源文件名

---

## 附：文件职责速查

| 文件 | 职责 | 运行位置 |
|------|------|----------|
| `rag_api_server.py` | FastAPI 服务，RAGEngine，所有 API 端点 | 服务器 Docker |
| `rag_mcp_proxy.py` | MCP stdio → HTTP 协议转换，TRAE 工具注册 | 开发机 |
| `rag_plugin.py` | LangBot 消息拦截 → 检索 → 上下文注入 | LangBot 服务器 |
| `test_connection.py` | 连通性测试（独立运行） | 开发机 |
| `.env.template` | 环境变量参考模板 | 服务器 |
| `Dockerfile` | Docker 镜像构建 | 服务器 |
| `docker-compose.yml` | Docker 编排配置 | 服务器 |
| `mcp_config.json` | TRAE MCP 配置参考 | 开发机 |
