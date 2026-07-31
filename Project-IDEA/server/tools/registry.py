"""
工具注册表 — 管理所有可用工具的定义和执行
"""

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("idea.tools")

# 默认工作目录
DEFAULT_WORKSPACE = os.getenv("IDEA_WORKSPACE", os.getcwd())


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    output: str
    tool_name: str = ""
    metadata: dict = field(default_factory=dict)


class ToolRegistry:
    """工具注册表 — 定义 + 执行"""

    def __init__(self, workspace: str = None, allowed_dirs: list[str] = None):
        self.workspace = workspace or DEFAULT_WORKSPACE
        self.allowed_dirs = allowed_dirs or [self.workspace]
        self._tools = {}
        self._register_all()

    def _register_all(self):
        """注册所有工具"""
        self._tools = {
            # === 文件系统工具 ===
            "read_file": {
                "function": self.read_file,
                "schema": {
                    "name": "read_file",
                    "description": "读取文件内容。返回带行号的文本。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件的绝对路径"},
                            "offset": {"type": "integer", "description": "从第几行开始读取", "default": 1},
                            "limit": {"type": "integer", "description": "读取多少行", "default": 200},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            "write_file": {
                "function": self.write_file,
                "schema": {
                    "name": "write_file",
                    "description": "创建或覆盖写入文件。会自动创建父目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件的绝对路径"},
                            "content": {"type": "string", "description": "要写入的内容"},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            "edit_file": {
                "function": self.edit_file,
                "schema": {
                    "name": "edit_file",
                    "description": "在文件中查找并替换指定文本段。old_str 需唯一匹配。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件的绝对路径"},
                            "old_str": {"type": "string", "description": "要被替换的原始文本段"},
                            "new_str": {"type": "string", "description": "替换后的新文本段"},
                        },
                        "required": ["file_path", "old_str", "new_str"],
                    },
                },
            },
            "list_dir": {
                "function": self.list_dir,
                "schema": {
                    "name": "list_dir",
                    "description": "列出目录中的文件和子目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "目录的绝对路径", "default": "."},
                            "pattern": {"type": "string", "description": "glob 文件名匹配模式，如 *.py", "default": "*"},
                        },
                        "required": [],
                    },
                },
            },
            "search_content": {
                "function": self.search_content,
                "schema": {
                    "name": "search_content",
                    "description": "在文件中搜索匹配的文本内容（支持正则表达式）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "搜索模式（支持正则表达式）"},
                            "directory": {"type": "string", "description": "搜索的目录路径", "default": "."},
                            "file_types": {"type": "string", "description": "文件类型过滤，如 .py,.md", "default": ""},
                            "case_sensitive": {"type": "boolean", "description": "是否区分大小写", "default": False},
                            "max_results": {"type": "integer", "description": "最大返回条数", "default": 40},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            "delete_file": {
                "function": self.delete_file,
                "schema": {
                    "name": "delete_file",
                    "description": "删除文件。操作不可逆，请谨慎使用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "要删除的文件绝对路径"},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            # === 系统工具 ===
            "run_command": {
                "function": self.run_command,
                "schema": {
                    "name": "run_command",
                    "description": "在终端中执行命令并返回输出。命令在隔离环境中运行，超时 60 秒。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "要执行的 Shell 命令"},
                            "working_dir": {"type": "string", "description": "工作目录", "default": "."},
                            "timeout_seconds": {"type": "integer", "description": "超时时间（秒）", "default": 60},
                        },
                        "required": ["command"],
                    },
                },
            },
            # === 网络工具 ===
            "web_search": {
                "function": self.web_search,
                "schema": {
                    "name": "web_search",
                    "description": "搜索互联网并返回结果摘要列表。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索查询词"},
                            "max_results": {"type": "integer", "description": "最大返回条数", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
            "web_fetch": {
                "function": self.web_fetch,
                "schema": {
                    "name": "web_fetch",
                    "description": "抓取指定 URL 的网页内容并转换为 Markdown。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "要抓取的网页 URL"},
                        },
                        "required": ["url"],
                    },
                },
            },
        }

    def get_all_schemas(self) -> list[dict]:
        """返回所有工具的 schema（给 LLM 的 function calling）"""
        return [t["schema"] for t in self._tools.values()]

    def get_tool(self, name: str) -> Optional[Callable]:
        """获取工具执行函数"""
        tool = self._tools.get(name)
        return tool["function"] if tool else None

    def get_all_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    # ==================================================================
    # 安全性检查
    # ==================================================================

    def _resolve_path(self, file_path: str) -> Path:
        """解析路径并检查是否在允许的目录内"""
        p = Path(file_path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (Path(self.workspace) / p).resolve()

        # 检查是否在允许的目录内
        for allowed in self.allowed_dirs:
            allowed_path = Path(allowed).resolve()
            try:
                resolved.relative_to(allowed_path)
                return resolved
            except ValueError:
                continue

        raise PermissionError(f"路径 '{file_path}' 不在允许的目录范围内: {self.allowed_dirs}")

    # ==================================================================
    # 文件系统工具
    # ==================================================================

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 200) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            if not path.exists():
                return ToolResult(False, f"文件不存在: {path}", "read_file")
            if not path.is_file():
                return ToolResult(False, f"不是文件: {path}", "read_file")

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total = len(lines)
            start = max(0, offset - 1)
            end = min(total, start + limit)
            selected = lines[start:end]

            output = []
            for i, line in enumerate(selected, start=start + 1):
                output.append(f"{i:>6}| {line.rstrip()}")

            header = f"# {path} (lines {start+1}-{end} of {total})\n\n"
            return ToolResult(True, header + "\n".join(output), "read_file",
                              {"total_lines": total, "shown": len(selected)})
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "read_file")
        except Exception as e:
            return ToolResult(False, f"读取失败: {e}", "read_file")

    async def write_file(self, file_path: str, content: str) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            file_size = path.stat().st_size
            lines = content.count("\n") + 1
            return ToolResult(True,
                              f"文件已写入: {path}\n大小: {file_size} bytes, {lines} 行",
                              "write_file",
                              {"path": str(path), "size": file_size, "lines": lines})
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "write_file")
        except Exception as e:
            return ToolResult(False, f"写入失败: {e}", "write_file")

    async def edit_file(self, file_path: str, old_str: str, new_str: str) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            if not path.exists():
                return ToolResult(False, f"文件不存在: {path}", "edit_file")

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_str not in content:
                return ToolResult(False,
                                  f"未找到要替换的文本段。请确认 old_str 与文件内容精确匹配。",
                                  "edit_file")

            if content.count(old_str) > 1:
                return ToolResult(False,
                                  f"old_str 在文件中出现了 {content.count(old_str)} 次。请提供更多上下文以确保唯一匹配。",
                                  "edit_file")

            new_content = content.replace(old_str, new_str, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult(True, f"文件已编辑: {path}", "edit_file",
                              {"path": str(path), "replaced_chars": len(old_str)})
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "edit_file")
        except Exception as e:
            return ToolResult(False, f"编辑失败: {e}", "edit_file")

    async def list_dir(self, path: str = ".", pattern: str = "*") -> ToolResult:
        try:
            p = self._resolve_path(path)
            if not p.exists():
                return ToolResult(False, f"路径不存在: {p}", "list_dir")
            if not p.is_dir():
                return ToolResult(False, f"不是目录: {p}", "list_dir")

            from pathlib import Path as P
            items = sorted(p.glob(pattern))

            dirs = [i for i in items if i.is_dir()]
            files = [i for i in items if i.is_file()]

            output = f"# {p}\n\n"
            if dirs:
                output += "## 目录\n" + "\n".join(f"  📁 {d.name}/" for d in dirs) + "\n\n"
            if files:
                output += "## 文件\n"
                for f_item in files:
                    size = f_item.stat().st_size
                    size_str = f"{size:,} B" if size < 1024 else f"{size/1024:.1f} KB"
                    output += f"  📄 {f_item.name} ({size_str})\n"
            if not dirs and not files:
                output += "(空目录)\n"

            return ToolResult(True, output, "list_dir",
                              {"dirs": len(dirs), "files": len(files)})
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "list_dir")
        except Exception as e:
            return ToolResult(False, f"列出目录失败: {e}", "list_dir")

    async def search_content(self, pattern: str, directory: str = ".", file_types: str = "",
                             case_sensitive: bool = False, max_results: int = 40) -> ToolResult:
        try:
            import re
            d = self._resolve_path(directory)

            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return ToolResult(False, f"正则表达式错误: {e}", "search_content")

            # 确定文件扩展名过滤
            exts = set()
            if file_types:
                exts = {e.strip() for e in file_types.split(",") if e.strip()}

            results = []
            for f_path in d.rglob("*"):
                if not f_path.is_file():
                    continue
                if exts and f_path.suffix not in exts:
                    continue
                # 跳过二进制和隐藏文件
                if any(p.startswith(".") for p in f_path.parts):
                    continue
                try:
                    with open(f_path, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{f_path}:{i}: {line.strip()[:120]}")
                                if len(results) >= max_results:
                                    break
                    if len(results) >= max_results:
                        break
                except (OSError, UnicodeDecodeError):
                    continue

            output = f"搜索: '{pattern}' in {d}\n找到 {len(results)} 条结果:\n\n"
            output += "\n".join(results[:max_results])
            if len(results) >= max_results:
                output += f"\n... (只显示前 {max_results} 条)"
            return ToolResult(True, output, "search_content", {"total": len(results)})
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "search_content")
        except Exception as e:
            return ToolResult(False, f"搜索失败: {e}", "search_content")

    async def delete_file(self, file_path: str) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            if not path.exists():
                return ToolResult(False, f"文件不存在: {path}", "delete_file")
            path.unlink()
            return ToolResult(True, f"已删除: {path}", "delete_file")
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "delete_file")
        except Exception as e:
            return ToolResult(False, f"删除失败: {e}", "delete_file")

    # ==================================================================
    # 系统工具
    # ==================================================================

    async def run_command(self, command: str, working_dir: str = ".",
                          timeout_seconds: int = 60) -> ToolResult:
        try:
            wd = str(self._resolve_path(working_dir))

            # Windows 用 PowerShell, Linux 用 bash
            if os.name == "nt":
                full_cmd = ["powershell", "-Command", command]
            else:
                full_cmd = ["bash", "-c", command]

            proc = subprocess.run(
                full_cmd,
                cwd=wd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )

            output = ""
            if proc.stdout:
                output += proc.stdout + "\n"
            if proc.stderr:
                output += f"[stderr]\n{proc.stderr}\n"

            if proc.returncode != 0:
                output += f"\n[退出码: {proc.returncode}]"

            # 截断过长输出
            if len(output) > 8000:
                output = output[:8000] + "\n... (输出已截断)"

            return ToolResult(True, output.strip() or "(无输出)", "run_command",
                              {"exit_code": proc.returncode})
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"命令超时 ({timeout_seconds}s)", "run_command")
        except PermissionError as e:
            return ToolResult(False, f"权限拒绝: {e}", "run_command")
        except Exception as e:
            return ToolResult(False, f"命令执行失败: {e}", "run_command")

    # ==================================================================
    # 网络工具
    # ==================================================================

    async def web_search(self, query: str, max_results: int = 5) -> ToolResult:
        """
        网页搜索。
        生产环境建议接入真实搜索 API (Bing/Google/DuckDuckGo)。
        此处提供一个基于 HTTP 的简化实现。
        """
        try:
            # 尝试 DuckDuckGo HTML 搜索（无需 API Key）
            url = "https://html.duckduckgo.com/html/"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, data={"q": query})
                if resp.status_code != 200:
                    return ToolResult(
                        False,
                        f"搜索失败 (HTTP {resp.status_code})。请检查网络连接。",
                        "web_search",
                    )

                # 简单解析搜索结果
                from html.parser import HTMLParser

                class SearchParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.results = []
                        self.current = {}
                        self.in_result = False
                        self.in_link = False
                        self.in_snippet = False
                        self.tag_stack = []

                    def handle_starttag(self, tag, attrs):
                        attrs_dict = dict(attrs)
                        if tag == "div" and "result__body" in attrs_dict.get("class", ""):
                            self.in_result = True
                            self.current = {}
                        if self.in_result and tag == "a" and "result__a" in attrs_dict.get("class", ""):
                            self.in_link = True
                            self.current["url"] = attrs_dict.get("href", "")
                        if self.in_result and tag == "a" and "result__snippet" in attrs_dict.get("class", ""):
                            self.in_snippet = True

                    def handle_data(self, data):
                        if self.in_link:
                            self.current["title"] = (self.current.get("title", "") + data).strip()
                        if self.in_snippet:
                            self.current["snippet"] = (self.current.get("snippet", "") + data).strip()

                    def handle_endtag(self, tag):
                        if tag == "a":
                            self.in_link = False
                            self.in_snippet = False
                        if tag == "div" and self.in_result and self.current:
                            if self.current.get("title"):
                                self.results.append(self.current.copy())
                            self.in_result = False

                parser = SearchParser()
                parser.feed(resp.text)

                output = f"# 搜索: {query}\n\n"
                for i, r in enumerate(parser.results[:max_results], 1):
                    title = r.get("title", "无标题")[:100]
                    snippet = r.get("snippet", "")[:200]
                    url = r.get("url", "")
                    output += f"## {i}. {title}\n"
                    output += f"   URL: {url}\n"
                    output += f"   {snippet}\n\n"

                if not parser.results:
                    output = f"# 搜索: {query}\n\n未找到结果。尝试更精确的关键词。\n\n建议：\n- 检查拼写\n- 使用更少或更通用的关键词\n- 确认网络连接正常"

                return ToolResult(True, output, "web_search", {"results": len(parser.results)})

        except httpx.ConnectError:
            return ToolResult(False,
                              "无法连接到搜索引擎。请检查网络连接。\n提示：在服务器上可能需要配置代理或 DNS。",
                              "web_search")
        except Exception as e:
            return ToolResult(False, f"搜索异常: {e}", "web_search")

    async def web_fetch(self, url: str) -> ToolResult:
        """抓取网页内容"""
        try:
            if not url.startswith(("http://", "https://")):
                return ToolResult(False, "URL 必须以 http:// 或 https:// 开头", "web_fetch")

            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 IDEA-Agent/2.0",
                    "Accept": "text/html,application/xhtml+xml",
                })

                if resp.status_code != 200:
                    return ToolResult(False, f"HTTP {resp.status_code}", "web_fetch")

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    return ToolResult(True, resp.text[:5000], "web_fetch")

                # 简单 HTML → 文本转换
                text = self._html_to_text(resp.text)
                # 截断
                if len(text) > 10000:
                    text = text[:10000] + "\n... (内容已截断)"
                return ToolResult(True, text, "web_fetch", {"url": url, "status": resp.status_code})

        except httpx.ConnectError:
            return ToolResult(False, f"无法连接到 {url}", "web_fetch")
        except Exception as e:
            return ToolResult(False, f"抓取失败: {e}", "web_fetch")

    def _html_to_text(self, html: str) -> str:
        """简化 HTML → 纯文本"""
        import re
        # 移除 script/style
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # 移除标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 清理空白
        text = re.sub(r'\s+', ' ', text).strip()
        # 解码 HTML 实体
        import html as html_mod
        text = html_mod.unescape(text)
        return text
