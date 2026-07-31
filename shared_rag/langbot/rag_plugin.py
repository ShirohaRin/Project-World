"""
======================================================================
LangBot RAG 知识库插件
======================================================================
让飞书机器人调用你的共享 RAG 知识库。

安装方式：
  1. 将此文件放入 LangBot 的 plugins/ 目录
  2. 在 LangBot 插件管理页面启用「rag_knowledge」插件
  3. 配置插件参数：
     - rag_server_url: RAG 服务器的地址（必填）
     - rag_api_key: 你的 LANGBOT_KEY
     - rag_top_k: 默认返回结果数（默认5）
     - rag_auto_trigger: 是否自动检索（默认true）

使用效果：
  - 飞书用户正常提问，LangBot 自动检索知识库后回答
  - 手动命令：/rag <查询内容>

工作流程：
  用户消息 → LangBot 接收 → 此插件拦截 → 调 RAG API →
  检索相关文档 → 注入 LLM 上下文 → 基于文档生成回答 → 回复用户

权限说明：
  此插件使用 LANGBOT_KEY，只能读取 public 知识库。
  私有库（private）对飞书用户不可见。
======================================================================
"""

import os
import logging
import requests

logger = logging.getLogger("langbot.rag-plugin")


# ============================================================
# 从 LangBot 插件配置中读取参数
# ============================================================
# LangBot 的插件系统会把配置注入进来，这里先设默认值保底
SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://localhost:8080")
API_KEY = os.environ.get("RAG_LANGBOT_KEY", "")
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))


def search_rag(query: str, top_k: int = None) -> list[dict]:
    """
    调用服务器 RAG API 检索公开+小说知识库。

    Args:
        query: 查询问题
        top_k: 返回数量

    Returns:
        检索结果列表（public + novel 合并，按相似度排序）
    """
    if not SERVER_URL:
        logger.warning("RAG 插件未配置服务器地址")
        return []

    k = top_k or TOP_K
    all_results = []
    seen = set()

    for collection in ("public", "novel"):
        try:
            headers = {}
            if API_KEY:
                headers["X-API-Key"] = API_KEY

            resp = requests.post(
                f"{SERVER_URL}/api/search?collection={collection}",
                headers=headers,
                json={"query": query, "top_k": min(k, 10)},
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                fingerprint = r.get("content", "")[:80]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    all_results.append(r)
        except requests.exceptions.ConnectionError:
            logger.error("无法连接 RAG 服务器：%s", SERVER_URL)
        except Exception as e:
            logger.error("RAG 检索 [%s] 异常：%s", collection, e)

    all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    logger.info("RAG 检索成功：query=「%s」→ %d 条结果", query[:30], len(all_results))
    return all_results[:k]


# ============================================================
# LangBot 插件入口
# ============================================================

# --- 方式一：LangBot 插件类 ---
try:
    from langbot.plugin import Plugin, PluginContext

    class RAGKnowledgePlugin(Plugin):
        """LangBot RAG 知识库插件"""

        def __init__(self, ctx: PluginContext):
            super().__init__(ctx)

            # 从 LangBot 配置中读取参数
            config = ctx.config or {}
            self.server_url = config.get("rag_server_url", SERVER_URL)
            self.api_key = config.get("rag_api_key", API_KEY)
            self.top_k = int(config.get("rag_top_k", TOP_K))
            self.auto_trigger = config.get("rag_auto_trigger", "true").lower() == "true"

            if not self.server_url:
                logger.warning("RAG 插件未配置服务器地址，已禁用")
                self.enabled = False
            else:
                logger.info("RAG 插件已初始化，服务器：%s", self.server_url)

        def _search(self, query: str) -> list[dict]:
            """内部检索方法：同时搜 public + novel，合并去重"""
            headers = {}
            if self.api_key:
                headers["X-API-Key"] = self.api_key

            all_results = []
            seen = set()

            # 同时检索 public 和 novel 两个集合
            for collection in ("public", "novel"):
                try:
                    resp = requests.post(
                        f"{self.server_url}/api/search?collection={collection}",
                        headers=headers,
                        json={"query": query, "top_k": min(self.top_k, 10)},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    for r in resp.json().get("results", []):
                        fingerprint = r.get("content", "")[:80]
                        if fingerprint not in seen:
                            seen.add(fingerprint)
                            all_results.append(r)
                except Exception as e:
                    logger.error("RAG 检索 [%s] 失败：%s", collection, e)

            # 按相似度降序排列
            all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
            return all_results[: self.top_k]

        async def on_message(self, message: dict):
            """消息前置处理：自动检索并注入上下文"""
            if not self.auto_trigger:
                return None

            msg_text = message.get("content", "").strip()
            if not msg_text:
                return None

            # 跳过命令消息
            if msg_text.startswith("/"):
                return None

            logger.info("RAG 自动检索：%s", msg_text[:40])
            results = self._search(msg_text)

            if not results:
                return None  # 没查到不影响，跳过

            # 构建上下文追加到消息
            context_parts = ["\n\n【以下是知识库中检索到的相关文档，请优先基于这些信息回答：】\n"]
            for r in results:
                context_parts.append(
                    f"\n[相关度: {r['similarity']:.0%}] 来源: {r['source']}\n"
                    f"{r['content']}\n"
                )

            message["content"] = msg_text + "\n".join(context_parts)
            message["_rag_enhanced"] = True
            return message

        async def on_command(self, command: str, args: str, message: dict):
            """手动命令：/rag <查询>"""
            if command != "rag":
                return None

            query = args.strip()
            if not query:
                return "用法：/rag <查询内容>\n例如：/rag 唐代诗歌的主要特点是什么？"

            results = self._search(query)

            if not results:
                return f"❌ 知识库中未找到与「{query}」相关的内容。"

            parts = [f"📚 **知识库检索**（查询：「{query}」）：\n"]
            for r in results:
                parts.append(
                    f"**{r['rank']}.** [{r['similarity']:.0%}] `{r['source']}`\n"
                    f"```\n{r['content'][:400]}\n```\n"
                )

            return "\n".join(parts)

        def get_commands(self):
            return {"rag": "检索知识库。用法：/rag <查询内容>"}

except ImportError:
    # LangBot 插件系统未安装，这个文件可以作为独立模块使用
    logger.info("LangBot 插件系统未检测到，rag_plugin 作为独立模块就绪")


# ============================================================
# 方式二：LangBot 流水线函数（如果你用的是流水线模式而非插件）
# ============================================================

def pipeline_pre_process(user_message: str) -> str:
    """
    LangBot 流水线前置处理函数。

    如果你用的是 LangBot 流水线（Pipeline）模式，
    将此函数注册为 pre_process 步骤即可。

    配置示例（LangBot 配置文件中）：
      pipeline:
        pre_process:
          - module: rag_plugin
            function: pipeline_pre_process
    """
    if not SERVER_URL:
        return user_message

    results = search_rag(user_message)

    if not results:
        return user_message

    context_parts = ["\n\n【参考知识库文档：】\n"]
    for r in results:
        context_parts.append(f"[{r['similarity']:.0%}] {r['content'][:300]}...\n")

    return user_message + "\n".join(context_parts)
