"""Tool registry with request-bound policy enforcement and filesystem boundaries."""

import asyncio
import difflib
import fnmatch
import hashlib
import inspect
import ipaddress
import json
import logging
import os
import re
import socket
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

from tool_runtime.permissions import ExecutionContext, PolicyDecision, ToolPolicy, ToolPolicyResult
from tool_runtime.sandbox import SandboxEnforcement, SandboxManager, SandboxMode, restricted_exec_argv

try:
    import paramiko  # type: ignore
    _PARAMIKO_AVAILABLE = True
except ImportError:
    _PARAMIKO_AVAILABLE = False

logger = logging.getLogger("idea.tools")
DEFAULT_WORKSPACE = os.getenv("IDEA_WORKSPACE", os.getcwd())
MAX_FILE_BYTES = 1_000_000
MAX_SEARCH_FILE_BYTES = 1_000_000
SENSITIVE_DIR_NAMES = {
    ".git", ".ssh", ".gnupg", "credentials", "secrets", "node_modules", ".venv",
    "venv", "__pycache__", "dist", "dist-electron", "release", "setup", ".idea-assistant",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
BACKUP_DIR_NAME = "backup"
FILE_CHANGE_TOOLS = {"write_file", "edit_file", "delete_file", "restore_file"}
MAX_SSH_FILE_BYTES = 5 * 1024 * 1024
# 工具展示意图（对齐 dsh presentCall/presentResult 卡片模型）：工具声明"UI 如何展示我"，
# 客户端按 card 类型渲染（diff/terminal/search/read/web/generic），服务端无需感知具体 UI。
TOOL_PRESENTATION = {
    "read_file": {"card": "read"},
    "write_file": {"card": "diff"},
    "edit_file": {"card": "diff"},
    "delete_file": {"card": "diff"},
    "restore_file": {"card": "diff"},
    "list_dir": {"card": "generic"},
    "search_content": {"card": "search"},
    "run_command": {"card": "terminal"},
    "web_search": {"card": "web"},
    "web_fetch": {"card": "web"},
    "ssh_run": {"card": "terminal"},
    "ssh_get": {"card": "generic"},
    "ssh_put": {"card": "generic"},
    "dispatch_to_agent": {"card": "generic"},
}
SSH_BLOCKED_REMOTE_PATTERNS = [
    re.compile(r"^/etc/(passwd|shadow|sudoers|group)$"),
    re.compile(r"^/root/\.ssh/"),
    re.compile(r"^/home/[^/]+/\.ssh/"),
]


def load_ssh_hosts_from_env() -> list[dict]:
    """从环境变量 IDEA_SSH_HOSTS 加载 SSH 主机白名单（JSON 数组）。"""
    raw = os.getenv("IDEA_SSH_HOSTS", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        logger.warning("IDEA_SSH_HOSTS 格式无效，已忽略")
        return []
GRANT_TOOLS = {
    "file.read": {"read_file", "list_dir", "search_content"},
    "file.write": {"write_file", "edit_file", "restore_file"},
    "file.delete": {"delete_file"},
    "command": {"run_command"},
    "network": {"web_search", "web_fetch"},
    "delegate": {"dispatch_to_agent"},
    "ssh": {"ssh_run", "ssh_get", "ssh_put"},
}
# 即使经过审批或授权也永远拒绝的危险命令模式（防止破坏性操作与凭据泄露）。
DANGEROUS_COMMAND_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*[rf]", re.IGNORECASE),
    re.compile(r"\b(rmdir|rd)\s+/\s*s", re.IGNORECASE),
    re.compile(r"\b(del|erase)\s+/[a-z]*[qs]", re.IGNORECASE),
    re.compile(r"\bgit\s+(push|commit|reset|checkout|merge|rebase|clean|tag\s+-d|branch\s+-[dD])\b", re.IGNORECASE),
    re.compile(r"\b(shutdown|reboot|format)\b", re.IGNORECASE),
    re.compile(r"\breg\s+(delete|add)\b", re.IGNORECASE),
    re.compile(r"\bnet\s+user\b", re.IGNORECASE),
    re.compile(r"\b(sudo\s+)?(chmod|chown)\s+-?[a-z]*\s*[47]?[0-7]{3}", re.IGNORECASE),
    re.compile(r"\bmv\s+[^\s<>]+\s+/etc/passwd", re.IGNORECASE),
]


@dataclass
class ToolResult:
    success: bool
    output: str
    tool_name: str = ""
    metadata: dict = field(default_factory=dict)


class ToolRegistry:
    """Defines tools and enforces policy at the only supported execution sink."""

    def __init__(self, workspace: str = None, allowed_dirs: list[str] = None, policy: ToolPolicy = None, audit_store: Any = None, ssh_hosts: list[dict] = None, sandbox_mode: Optional[SandboxMode] = None):
        self.workspace = str(Path(workspace or DEFAULT_WORKSPACE).resolve())
        self.allowed_dirs = [str(Path(path).resolve()) for path in (allowed_dirs or [self.workspace])]
        self.policy = policy or ToolPolicy()
        self.audit_store = audit_store
        self.ssh_hosts = ssh_hosts if ssh_hosts is not None else load_ssh_hosts_from_env()
        default_sandbox = os.getenv("IDEA_SANDBOX_MODE", "").strip()
        try:
            self.sandbox = SandboxManager(
                self.workspace,
                default_mode=sandbox_mode or (SandboxMode(default_sandbox) if default_sandbox else SandboxMode.WORKSPACE_WRITE),
            )
        except ValueError:
            logger.warning("IDEA_SANDBOX_MODE 取值无效，回退 workspace-write")
            self.sandbox = SandboxManager(self.workspace, SandboxMode.WORKSPACE_WRITE)
        self._tools: dict[str, dict[str, Any]] = {}
        self._guards: list[Callable[[str, dict, Optional[ExecutionContext]], Optional[str]]] = []
        self._register_all()

    def register_guard(self, guard: Callable[[str, dict, Optional[ExecutionContext]], Optional[str]]) -> Callable[[], None]:
        """注册一个单调工具守卫（对齐 dsh ToolGuard）。

        守卫只允许"拒绝"：返回 reason 字符串即拒绝该次调用；任何 guard 的拒绝
        都不可被后续 guard 或调用方覆盖（守卫没有 allow 结果）。返回解除函数。
        """
        self._guards.append(guard)

        def unregister() -> None:
            if guard in self._guards:
                self._guards.remove(guard)

        return unregister

    def _register_all(self) -> None:
        self._tools = {
            "read_file": {"function": self.read_file, "schema": self._schema("read_file", "读取文件内容。返回带行号的文本。", {"file_path": {"type": "string", "description": "文件的绝对路径"}, "offset": {"type": "integer", "default": 1}, "limit": {"type": "integer", "default": 200}}, ["file_path"])},
            "write_file": {"function": self.write_file, "schema": self._schema("write_file", "创建或覆盖写入文件。", {"file_path": {"type": "string", "description": "文件的绝对路径"}, "content": {"type": "string", "description": "要写入的内容"}}, ["file_path", "content"])},
            "edit_file": {"function": self.edit_file, "schema": self._schema("edit_file", "在文件中查找并替换指定文本段。old_str 需唯一匹配。", {"file_path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}}, ["file_path", "old_str", "new_str"])},
            "list_dir": {"function": self.list_dir, "schema": self._schema("list_dir", "列出目录中的文件和子目录。", {"path": {"type": "string", "default": "."}, "pattern": {"type": "string", "default": "*"}}, [])},
            "search_content": {"function": self.search_content, "schema": self._schema("search_content", "在文件中搜索匹配的文本内容（支持正则表达式）。", {"pattern": {"type": "string"}, "directory": {"type": "string", "default": "."}, "file_types": {"type": "string", "default": ""}, "case_sensitive": {"type": "boolean", "default": False}, "max_results": {"type": "integer", "default": 40}}, ["pattern"])},
            "delete_file": {"function": self.delete_file, "schema": self._schema("delete_file", "删除文件（删除前自动备份，可用 restore_file 恢复）。", {"file_path": {"type": "string"}}, ["file_path"])},
            "restore_file": {"function": self.restore_file, "schema": self._schema("restore_file", "从最近备份恢复被覆盖或删除的文件（回收站回滚）。", {"file_path": {"type": "string"}, "backup_path": {"type": "string", "description": "可选：指定备份路径；缺省恢复最近一次备份"}}, ["file_path"])},
            "run_command": {"function": self.run_command, "schema": self._schema("run_command", "命令执行当前被策略禁用，等待受控执行环境与审批。", {"command": {"type": "string"}, "working_dir": {"type": "string", "default": "."}, "timeout_seconds": {"type": "integer", "default": 60}}, ["command"])},
            "web_search": {"function": self.web_search, "schema": self._schema("web_search", "网络搜索当前被策略禁用，等待受控 egress 与审批。", {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, ["query"])},
            "web_fetch": {"function": self.web_fetch, "schema": self._schema("web_fetch", "网页抓取当前被策略禁用，等待受控 egress 与审批。", {"url": {"type": "string"}}, ["url"])},
            "ssh_run": {"function": self.ssh_run, "schema": self._schema("ssh_run", "在 SSH 白名单主机上执行远程命令（需 Owner 审批或 ssh 授权）。", {"host": {"type": "string", "description": "主机名或别名，必须在服务端 SSH 白名单中"}, "command": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 60}}, ["host", "command"])},
            "ssh_get": {"function": self.ssh_get, "schema": self._schema("ssh_get", "从 SSH 白名单主机下载文件到本地工作区（需 Owner 审批或 ssh 授权）。", {"host": {"type": "string"}, "remote_path": {"type": "string"}, "local_path": {"type": "string", "description": "本地目标路径（工作区内）"}, "timeout_seconds": {"type": "integer", "default": 60}}, ["host", "remote_path", "local_path"])},
            "ssh_put": {"function": self.ssh_put, "schema": self._schema("ssh_put", "上传工作区内文件到 SSH 白名单主机（需 Owner 审批或 ssh 授权）。", {"host": {"type": "string"}, "local_path": {"type": "string", "description": "本地源文件路径（工作区内）"}, "remote_path": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 60}}, ["host", "local_path", "remote_path"])},
        }

    @staticmethod
    def _schema(name: str, description: str, properties: dict, required: list) -> dict:
        return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required}}

    def get_all_schemas(self) -> list[dict]:
        return self.schemas_for(None)

    def schemas_for(self, execution_context: Optional[ExecutionContext]) -> list[dict]:
        return [
            tool["schema"]
            for name, tool in self._tools.items()
            if self.policy.decide(name, execution_context).decision in (PolicyDecision.ALLOW, PolicyDecision.REQUIRES_APPROVAL)
        ]

    def get_tool(self, name: str) -> Optional[Callable]:
        """Compatibility accessor. Returned callable still executes as no-context, read-only."""
        if name not in self._tools:
            return None

        async def guarded(**args):
            return await self.execute(name, args, None)
        return guarded

    def get_all_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def register_tool(self, name: str, function: Callable, schema: dict) -> None:
        self._tools[name] = {"function": function, "schema": schema}

    async def execute(self, name: str, args: dict, execution_context: Optional[ExecutionContext] = None) -> ToolResult:
        decision = self.policy.decide(name, execution_context)
        if decision.decision != PolicyDecision.ALLOW:
            grant = self._find_grant(name, execution_context)
            if grant:
                decision = ToolPolicyResult(PolicyDecision.ALLOW, "grant_allowed", decision.risk)
        if decision.decision == PolicyDecision.REQUIRES_APPROVAL:
            approval = self._resolve_approval(name, args, execution_context)
            self._audit(name, decision, execution_context, approval_id=(approval or {}).get("approval_id"))
            if approval and approval.get("granted"):
                decision = ToolPolicyResult(PolicyDecision.ALLOW, "approval_granted", decision.risk)
            elif approval and approval.get("policy") == "never":
                return ToolResult(
                    False,
                    f"会话审批策略为 never：该操作需要审批但当前会话禁止弹审批（[{name}] 已拒绝）。",
                    name,
                    {"decision": PolicyDecision.REQUIRES_APPROVAL.value, "reason": "approval_policy_never", "approval_id": None},
                )
            else:
                approval_id = (approval or {}).get("approval_id")
                return ToolResult(
                    False,
                    self._approval_message(name, args, approval_id),
                    name,
                    {"decision": PolicyDecision.REQUIRES_APPROVAL.value, "reason": decision.reason_code, "approval_id": approval_id},
                )
        else:
            self._audit(name, decision, execution_context)
        if decision.decision != PolicyDecision.ALLOW:
            return ToolResult(False, self._denial_message(name, decision.reason_code), name, {"decision": decision.decision.value, "reason": decision.reason_code})
        guard_reason = self._run_guards(name, args, execution_context)
        if guard_reason:
            return ToolResult(False, f"策略拒绝: {guard_reason}", name, {"decision": "deny", "reason": guard_reason})
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(False, f"未知工具: {name}", name, {"decision": "deny", "reason": "unknown_tool"})
        try:
            function = tool["function"]
            if "execution_context" in inspect.signature(function).parameters:
                result = await function(**args, execution_context=execution_context)
            else:
                result = await function(**args)
            result.metadata = {**result.metadata, "decision": decision.decision.value, "reason": decision.reason_code}
            result.metadata["presentation"] = self._presentation_meta(name, args, result)
            if result.success:
                self._validate_output_contract(name, result)
            if result.success and name in FILE_CHANGE_TOOLS:
                self._record_file_change(name, args, result, execution_context)
            return result
        except TypeError as error:
            return ToolResult(False, f"参数错误: {error}", name, {"decision": decision.decision.value, "reason": "invalid_arguments", "presentation": self._presentation_meta(name, args, ToolResult(False, "", name))})
        except Exception:
            logger.exception("Tool execution failed: %s", name)
            return ToolResult(False, "工具执行异常", name, {"decision": decision.decision.value, "reason": "execution_error", "presentation": self._presentation_meta(name, args, ToolResult(False, "", name))})

    def _presentation_meta(self, name: str, args: dict, result: ToolResult) -> dict:
        """组装工具展示意图（对齐 dsh 卡片模型），成功与失败路径一致注入。"""
        presentation = TOOL_PRESENTATION.get(name, {"card": "generic"})
        if "diff" in result.metadata and presentation.get("card") == "diff":
            presentation = {**presentation, "diffs": [{"path": args.get("file_path", ""), "diff": result.metadata["diff"]}]}
        if name in ("run_command", "ssh_run") and "exit_code" in result.metadata:
            presentation = {**presentation, "exit_code": result.metadata.get("exit_code")}
        return presentation

    def _run_guards(self, name: str, args: dict, execution_context: Optional[ExecutionContext]) -> Optional[str]:
        """按注册顺序运行单调守卫；任一守卫拒绝即返回拒绝原因（不可被覆盖）。"""
        for guard in self._guards:
            try:
                reason = guard(name, args, execution_context)
            except Exception:
                logger.exception("tool guard failed for %s", name)
                reason = "guard_internal_error"
            if reason:
                return reason
        return None

    def _find_grant(self, name: str, execution_context: Optional[ExecutionContext]) -> Optional[dict]:
        """查询当前账号在该空间的有效持久授权；命中则直接放行，无需临时审批。"""
        if execution_context is None:
            return None
        store = self.audit_store
        if store is None or not hasattr(store, "find_valid_grant"):
            return None
        capability = next((cap for cap, tools in GRANT_TOOLS.items() if name in tools), None)
        if not capability:
            return None
        return store.find_valid_grant(
            execution_context.request_context.principal.account_id,
            capability,
            execution_context.request_context.space_id or "",
        )

    def _resolve_approval(self, name: str, args: dict, execution_context: Optional[ExecutionContext]) -> Optional[dict]:
        """命中已批准授权则放行；否则复用或新建审批请求。返回 {'granted': bool, 'approval_id': str} 或 None（无审批渠道）。"""
        if execution_context is None:
            return None
        store = self.audit_store
        if store is None or not hasattr(store, "find_tool_approval"):
            return None
        request_context = execution_context.request_context
        # 会话级审批策略：never → 直接拒绝且不创建审批请求（对齐 dsh 的 per-session 策略）
        if execution_context.conversation_id and hasattr(store, "conversation_approval_policy"):
            try:
                policy = store.conversation_approval_policy(
                    request_context.principal.account_id,
                    request_context.space_id,
                    execution_context.conversation_id,
                )
            except Exception:
                policy = "ask"
            if policy == "never":
                return {"granted": False, "approval_id": None, "policy": "never"}
        fingerprint = hashlib.sha256(json.dumps({"tool": name, "args": args}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        existing = store.find_tool_approval(
            request_context.principal.account_id,
            request_context.space_id,
            request_context.principal.principal_id,
            name,
            fingerprint,
        )
        if existing:
            return {"granted": existing["status"] == "approved", "approval_id": existing["approval_id"]}
        args_summary = json.dumps(args, ensure_ascii=False, sort_keys=True)[:300]
        if name == "edit_file":
            args_summary = self._edit_diff_summary(args)
        created = store.create_tool_approval(
            request_context.principal.account_id,
            request_context.space_id,
            request_context.principal.principal_id,
            execution_context.agent_id,
            name,
            fingerprint,
            args_summary,
        )
        return {"granted": False, "approval_id": created["approval_id"]}

    def _edit_diff_summary(self, args: dict) -> str:
        """为 edit_file 审批请求生成 unified diff 摘要，供 Owner 预览改动。"""
        try:
            old_str = str(args.get("old_str", ""))
            new_str = str(args.get("new_str", ""))
            path = self._resolve_path(str(args.get("file_path", "")))
            original = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            if old_str not in original:
                return json.dumps(args, ensure_ascii=False, sort_keys=True)[:300]
            updated = original.replace(old_str, new_str, 1)
            diff = "\n".join(difflib.unified_diff(original.splitlines(), updated.splitlines(), lineterm="", n=3))
            return f"[edit_file] {path}\n{diff[:800]}" if diff else json.dumps(args, ensure_ascii=False, sort_keys=True)[:300]
        except (PermissionError, OSError, ValueError):
            return json.dumps(args, ensure_ascii=False, sort_keys=True)[:300]

    def _record_file_change(self, name: str, args: dict, result: ToolResult, execution_context: Optional[ExecutionContext]) -> None:
        """文件修改执行成功后，把变更记入待审查队列（不阻塞后续对话）。"""
        if execution_context is None:
            return
        store = self.audit_store
        if store is None or not hasattr(store, "create_file_change_review"):
            return
        request_context = execution_context.request_context
        diff = result.metadata.get("diff") or ""
        if not diff and name == "edit_file":
            diff = self._edit_diff_summary(args)
        elif not diff and name in ("write_file", "delete_file", "restore_file"):
            diff = f"[{name}] {result.output[:300]}"
        try:
            store.create_file_change_review(
                request_context.principal.account_id,
                request_context.space_id,
                request_context.principal.principal_id,
                execution_context.agent_id,
                name,
                str(args.get("file_path", "")),
                result.metadata.get("backup"),
                diff,
            )
        except Exception:
            logger.warning("Unable to record file change review for %s", name)

    def _validate_output_contract(self, name: str, result: ToolResult) -> None:
        """可选输出契约校验（对齐 dsh ToolOutputDefinition）。

        工具可声明 output schema（如 {"type": "object"}）；执行成功后若输出不是
        符合契约的 JSON，在 metadata["output_contract"] 标记失败（不改变 success，
        输出仍为文本契约默认通过）。
        """
        tool = self._tools.get(name)
        output = (tool or {}).get("output")
        if not output:
            return
        expected = output.get("type")
        if expected == "string":
            return
        try:
            parsed = json.loads(result.output)
        except json.JSONDecodeError:
            result.metadata["output_contract"] = f"expected {expected}, got non-JSON"
            return
        valid = (expected == "object" and isinstance(parsed, dict)) or (expected == "array" and isinstance(parsed, list)) or (expected in ("number", "boolean", "null") and isinstance(parsed, (int, float, bool, type(None))))
        if not valid:
            result.metadata["output_contract"] = f"expected {expected}, got {type(parsed).__name__}"

    @staticmethod
    def _approval_message(name: str, args: dict, approval_id: Optional[str]) -> str:
        summary = json.dumps(args, ensure_ascii=False, sort_keys=True)[:200]
        if approval_id:
            return f"该操作需要 Owner 审批：[{name}] 参数 {summary}。已提交审批请求（{approval_id}），等待批准后请告知用户重试。"
        return f"该操作需要 Owner 审批：[{name}] 参数 {summary}。当前会话缺少可用的审批渠道。"

    @staticmethod
    def _denial_message(name: str, reason: str) -> str:
        if name == "run_command":
            return "策略拒绝：本轮不执行任意 shell 命令；后续需要受控执行环境与审批。"
        if name in {"web_search", "web_fetch"}:
            return "策略拒绝：本轮网络访问已禁用；后续需要受控 egress 与审批。"
        return f"策略拒绝: {reason}"

    def _audit(self, name: str, decision: Any, context: Optional[ExecutionContext], approval_id: Optional[str] = None) -> None:
        if not self.audit_store:
            return
        request_context = context.request_context if context else None
        try:
            metadata = {"risk": decision.risk.value, "agent_id": context.agent_id if context else "unbound"}
            if approval_id:
                metadata["approval_id"] = approval_id
            self.audit_store.write_audit("tool_policy", request_context, resource_type="tool", resource_id=name, action="execute", decision=decision.decision.value, reason_code=decision.reason_code, metadata=metadata)
        except Exception:
            logger.exception("Unable to write tool policy audit event")

    def _resolve_path(self, file_path: str) -> Path:
        candidate = Path(file_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (Path(self.workspace) / candidate).resolve()
        if not any(self._is_within(resolved, Path(allowed)) for allowed in self.allowed_dirs):
            raise PermissionError("路径不在服务端允许目录范围内")
        self._ensure_not_sensitive(resolved)
        return resolved

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _ensure_not_sensitive(self, path: Path) -> None:
        parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        if parts.intersection(SENSITIVE_DIR_NAMES) or name == ".env" or name.startswith(".env.") or name == "idea_credential_recovery_secret" or path.suffix.lower() in SENSITIVE_SUFFIXES:
            raise PermissionError("敏感路径或文件类型不可访问")

    def _safe_child(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            return any(self._is_within(resolved, Path(root)) for root in self.allowed_dirs) and not self._ensure_not_sensitive(resolved)
        except (OSError, PermissionError):
            return False

    @staticmethod
    def _check_size(path: Path, limit: int = MAX_FILE_BYTES) -> None:
        if path.stat().st_size > limit:
            raise PermissionError(f"文件超过 {limit} bytes 限制")

    async def read_file(self, file_path: str, offset: int = 1, limit: int = 200) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            if not path.is_file():
                return ToolResult(False, f"文件不存在或不是普通文件: {path}", "read_file")
            self._check_size(path)
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
            start, end = max(0, offset - 1), min(len(lines), max(0, offset - 1) + max(1, min(limit, 2000)))
            output = "\n".join(f"{number:>6}| {line.rstrip()}" for number, line in enumerate(lines[start:end], start + 1))
            return ToolResult(True, f"# {path} (lines {start + 1}-{end} of {len(lines)})\n\n{output}", "read_file", {"total_lines": len(lines), "shown": end - start})
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "read_file")
        except OSError as error:
            return ToolResult(False, f"读取失败: {error}", "read_file")

    async def write_file(self, file_path: str, content: str) -> ToolResult:
        try:
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                return ToolResult(False, f"写入内容超过 {MAX_FILE_BYTES} bytes 限制", "write_file")
            path = self._resolve_path(file_path)
            backup = None
            if path.exists():
                self._check_size(path)
                backup = self._backup_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            metadata = {"size": path.stat().st_size}
            if backup:
                metadata["backup"] = backup
            return ToolResult(True, f"文件已写入: {path}", "write_file", metadata)
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "write_file")
        except OSError as error:
            return ToolResult(False, f"写入失败: {error}", "write_file")

    async def edit_file(self, file_path: str, old_str: str, new_str: str) -> ToolResult:
        try:
            path = self._resolve_path(file_path)
            if not path.is_file():
                return ToolResult(False, f"文件不存在: {path}", "edit_file")
            self._check_size(path)
            content = path.read_text(encoding="utf-8")
            if content.count(old_str) != 1:
                return ToolResult(False, "old_str 必须在文件中唯一匹配", "edit_file")
            updated = content.replace(old_str, new_str, 1)
            if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
                return ToolResult(False, f"编辑结果超过 {MAX_FILE_BYTES} bytes 限制", "edit_file")
            backup = self._backup_file(path)
            diff = "\n".join(difflib.unified_diff(content.splitlines(), updated.splitlines(), lineterm="", n=3))
            path.write_text(updated, encoding="utf-8")
            metadata = {"replaced_chars": len(old_str)}
            if backup:
                metadata["backup"] = backup
            if diff:
                metadata["diff"] = diff[:800]
            return ToolResult(True, f"文件已编辑: {path}", "edit_file", metadata)
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "edit_file")
        except OSError as error:
            return ToolResult(False, f"编辑失败: {error}", "edit_file")

    async def list_dir(self, path: str = ".", pattern: str = "*") -> ToolResult:
        try:
            directory = self._resolve_path(path)
            if not directory.is_dir():
                return ToolResult(False, f"目录不存在: {directory}", "list_dir")
            items = [item for item in directory.iterdir() if fnmatch.fnmatch(item.name, pattern) and self._safe_child(item)]
            items.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
            output = "\n".join(f"{'[DIR]' if item.is_dir() else '[FILE]'} {item.name}" for item in items[:500]) or "(空目录)"
            return ToolResult(True, f"# {directory}\n\n{output}", "list_dir", {"items": len(items)})
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "list_dir")
        except OSError as error:
            return ToolResult(False, f"列出目录失败: {error}", "list_dir")

    async def search_content(self, pattern: str, directory: str = ".", file_types: str = "", case_sensitive: bool = False, max_results: int = 40) -> ToolResult:
        try:
            root = self._resolve_path(directory)
            if not root.is_dir():
                return ToolResult(False, f"目录不存在: {root}", "search_content")
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
            extensions = {item.strip() for item in file_types.split(",") if item.strip()}
            results, limit = [], max(1, min(max_results, 200))
            for candidate in root.rglob("*"):
                if not candidate.is_file() or not self._safe_child(candidate) or (extensions and candidate.suffix not in extensions):
                    continue
                try:
                    self._check_size(candidate, MAX_SEARCH_FILE_BYTES)
                    with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                        for line_number, line in enumerate(handle, 1):
                            if regex.search(line):
                                results.append(f"{candidate}:{line_number}: {line.strip()[:120]}")
                                if len(results) >= limit:
                                    break
                except (OSError, PermissionError):
                    continue
                if len(results) >= limit:
                    break
            return ToolResult(True, f"找到 {len(results)} 条结果:\n\n" + "\n".join(results), "search_content", {"total": len(results)})
        except re.error as error:
            return ToolResult(False, f"正则表达式错误: {error}", "search_content")
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "search_content")

    async def delete_file(self, file_path: str) -> ToolResult:
        """仅在 Owner 批准授权放行后调用；删除前自动备份，可用 restore_file 恢复。"""
        try:
            path = self._resolve_path(file_path)
            if not path.is_file():
                return ToolResult(False, f"文件不存在或不是普通文件: {path}", "delete_file")
            backup = self._backup_file(path)
            path.unlink()
            metadata = {}
            if backup:
                metadata["backup"] = backup
            return ToolResult(True, f"文件已删除: {path}（已备份，可用 restore_file 恢复）", "delete_file", metadata)
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "delete_file")
        except OSError as error:
            return ToolResult(False, f"删除失败: {error}", "delete_file")

    async def restore_file(self, file_path: str, backup_path: str = None) -> ToolResult:
        """从最近备份恢复被覆盖或删除的文件（回收站回滚）。"""
        try:
            path = self._resolve_path(file_path)
            backup_root = (Path(self.workspace) / ".idea-assistant" / BACKUP_DIR_NAME).resolve()
            if backup_path:
                backup = (backup_root / Path(backup_path).name).resolve()
                if not self._is_within(backup, backup_root) or not backup.is_file():
                    return ToolResult(False, "备份文件不存在或无效", "restore_file")
            else:
                matches = sorted(backup_root.glob(f"*-{path.name}"), key=lambda item: item.name, reverse=True)
                if not matches:
                    return ToolResult(False, "没有可恢复的备份", "restore_file")
                backup = matches[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(backup.read_bytes())
            return ToolResult(True, f"文件已从备份恢复: {backup.name} → {path}", "restore_file", {"backup": str(backup)})
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "restore_file")
        except OSError as error:
            return ToolResult(False, f"恢复失败: {error}", "restore_file")

    def _backup_file(self, path: Path) -> Optional[str]:
        """覆盖/删除前把原文件备份到工作区回收站，返回相对备份路径。"""
        try:
            if not path.is_file():
                return None
            backup_root = Path(self.workspace) / ".idea-assistant" / BACKUP_DIR_NAME
            backup_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_path = backup_root / f"{stamp}-{path.name}"
            backup_path.write_bytes(path.read_bytes())
            return str(backup_path.relative_to(Path(self.workspace)))
        except OSError:
            logger.warning("Unable to create backup for %s", path)
            return None

    async def run_command(self, command: str, working_dir: str = ".", timeout_seconds: int = 60) -> ToolResult:
        """仅在 Owner 批准授权放行后执行；工作目录限定在工作区、环境最小化、输出与超时受限。

        命令通过 OS 级沙箱（bwrap / Windows 受限令牌）执行：沙箱模式与强制力如实返回，
        后端不可用时降级 partial 并叠加应用层防护。
        """
        try:
            if any(pattern.search(command) for pattern in DANGEROUS_COMMAND_PATTERNS):
                return ToolResult(False, "命令黑名单：包含破坏性/危险操作，即使审批也禁止执行。", "run_command", {"exit_code": -1})
            wd = self._resolve_path(working_dir)
            if not wd.is_dir():
                return ToolResult(False, f"目录不存在: {wd}", "run_command")
            timeout = max(1, min(int(timeout_seconds), 300))
            if os.name == "nt":
                full_command = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
            else:
                full_command = ["bash", "-c", command]
            policy = self.sandbox.resolve()
            wrapped = self.sandbox.wrap_command(full_command, policy)
            sandbox_info = {"sandbox_mode": policy.mode.value, "sandbox_enforcement": self.sandbox.backend.enforcement.value}
            # Windows 受限令牌后端：走受限令牌启动（真实 OS 级强制）
            if os.name == "nt" and self.sandbox.backend.name == "windows-restricted-token" and policy.mode is not SandboxMode.DANGER_FULL_ACCESS:
                exit_code, stdout, stderr, enforcement = await asyncio.to_thread(
                    restricted_exec_argv, wrapped, str(wd), self._minimal_env(), timeout
                )
                sandbox_info["sandbox_enforcement"] = enforcement.value
            else:
                process = await asyncio.create_subprocess_exec(
                    *wrapped,
                    cwd=str(wd),
                    env=self._minimal_env(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return ToolResult(False, f"命令执行超过 {timeout} 秒，已终止。", "run_command", {"exit_code": -1, **sandbox_info})
                exit_code = process.returncode
                stdout = stdout.decode("utf-8", errors="replace")
                stderr = stderr.decode("utf-8", errors="replace")
            output = stdout + stderr
            if len(output) > 100_000:
                output = output[:100_000] + "\n...（输出已截断）"
            return ToolResult(exit_code == 0, f"$ {command}\n\n{output}" if output else f"$ {command}\n\n(无输出)", "run_command", {"exit_code": exit_code, **sandbox_info})
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "run_command")
        except FileNotFoundError:
            return ToolResult(False, "命令解释器不可用", "run_command")
        except Exception:
            logger.exception("run_command failed")
            return ToolResult(False, "命令执行异常", "run_command")

    async def web_search(self, query: str, max_results: int = 5) -> ToolResult:
        """搜索后端尚未接入；受控 egress 落地前建议改用 web_fetch 抓取已知 URL。"""
        return ToolResult(False, "搜索后端尚未接入；可以改用 web_fetch 抓取已知 URL。", "web_search")

    async def web_fetch(self, url: str) -> ToolResult:
        """仅在 Owner 批准授权放行后执行；带基础 SSRF 防护（拒绝内网/环回/保留地址）。"""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return ToolResult(False, "仅支持 http/https URL", "web_fetch")
            if await self._is_blocked_address(parsed.hostname):
                return ToolResult(False, "目标地址属于内网/环回/保留地址，已阻止", "web_fetch")
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "IDEA-Harness/3.0"}) as client:
                response = await client.get(url)
            content = response.text[:50_000]
            return ToolResult(response.status_code < 400, f"# {url}\n{content}", "web_fetch", {"status_code": response.status_code})
        except httpx.HTTPError as error:
            return ToolResult(False, f"抓取失败: {error}", "web_fetch")
        except (ValueError, socket.gaierror):
            return ToolResult(False, "URL 无法解析", "web_fetch")

    async def ssh_run(self, host: str, command: str, timeout_seconds: int = 60) -> ToolResult:
        """仅在 Owner 批准授权放行后调用；只能连接服务端配置的 SSH 白名单主机。"""
        target = self._ssh_host_config(host)
        if target is None:
            return ToolResult(False, f"主机不在 SSH 白名单内: {host}", "ssh_run")
        if not _PARAMIKO_AVAILABLE:
            return ToolResult(False, "SSH 依赖(paramiko)未安装，无法连接远程主机。", "ssh_run")
        timeout = max(5, min(int(timeout_seconds), 300))
        try:
            code, output = await asyncio.to_thread(self._ssh_exec_sync, target, command, timeout)
            return ToolResult(code == 0, f"[ssh {host}] $ {command}\n\n{output}" if output else f"[ssh {host}] $ {command}\n\n(无输出)", "ssh_run", {"exit_code": code, "host": host})
        except Exception as error:
            logger.warning("ssh_run failed for %s: %s", host, error)
            return ToolResult(False, f"SSH 连接或执行失败: {error}", "ssh_run")

    async def ssh_get(self, host: str, remote_path: str, local_path: str, timeout_seconds: int = 60) -> ToolResult:
        """从 SSH 白名单主机下载文件到本地工作区；本地覆盖前自动备份。"""
        target = self._ssh_host_config(host)
        if target is None:
            return ToolResult(False, f"主机不在 SSH 白名单内: {host}", "ssh_get")
        if not _PARAMIKO_AVAILABLE:
            return ToolResult(False, "SSH 依赖(paramiko)未安装，无法连接远程主机。", "ssh_get")
        try:
            local = self._resolve_path(local_path)
            if local.is_dir():
                return ToolResult(False, "local_path 必须是文件路径（不含已存在目录）", "ssh_get")
            backup = self._backup_file(local) if local.exists() else None
            timeout = max(5, min(int(timeout_seconds), 300))
            await asyncio.to_thread(self._ssh_get_sync, target, remote_path, str(local), timeout)
            metadata = {"host": host, "size": local.stat().st_size}
            if backup:
                metadata["backup"] = backup
            return ToolResult(True, f"已下载: {remote_path} → {local}", "ssh_get", metadata)
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "ssh_get")
        except Exception as error:
            logger.warning("ssh_get failed for %s: %s", host, error)
            return ToolResult(False, f"SSH 下载失败: {error}", "ssh_get")

    async def ssh_put(self, host: str, local_path: str, remote_path: str, timeout_seconds: int = 60) -> ToolResult:
        """上传工作区内文件到 SSH 白名单主机；拒绝覆盖远程关键系统文件。"""
        target = self._ssh_host_config(host)
        if target is None:
            return ToolResult(False, f"主机不在 SSH 白名单内: {host}", "ssh_put")
        if not _PARAMIKO_AVAILABLE:
            return ToolResult(False, "SSH 依赖(paramiko)未安装，无法连接远程主机。", "ssh_put")
        try:
            local = self._resolve_path(local_path)
            if not local.is_file():
                return ToolResult(False, f"本地文件不存在: {local}", "ssh_put")
            self._check_size(local, MAX_SSH_FILE_BYTES)
            for pattern in SSH_BLOCKED_REMOTE_PATTERNS:
                if pattern.match(remote_path):
                    return ToolResult(False, f"远程路径禁止写入: {remote_path}", "ssh_put")
            timeout = max(5, min(int(timeout_seconds), 300))
            await asyncio.to_thread(self._ssh_put_sync, target, str(local), remote_path, timeout)
            return ToolResult(True, f"已上传: {local} → {remote_path} ({host})", "ssh_put", {"host": host, "size": local.stat().st_size})
        except PermissionError as error:
            return ToolResult(False, f"权限拒绝: {error}", "ssh_put")
        except Exception as error:
            logger.warning("ssh_put failed for %s: %s", host, error)
            return ToolResult(False, f"SSH 上传失败: {error}", "ssh_put")

    @staticmethod
    def _ssh_get_sync(config: dict, remote_path: str, local_path: str, timeout: int) -> None:
        """后台线程 SFTP 下载，带远程文件大小限制。"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=config["host"],
            port=int(config.get("port", 22)),
            username=config.get("user", "root"),
            key_filename=config.get("key_path"),
            password=config.get("password"),
            timeout=min(timeout, 30),
        )
        try:
            sftp = client.open_sftp()
            try:
                stat = sftp.stat(remote_path)
                if stat.st_size > MAX_SSH_FILE_BYTES:
                    raise ValueError(f"远程文件超过大小限制 {MAX_SSH_FILE_BYTES} bytes")
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                sftp.get(remote_path, local_path)
            finally:
                sftp.close()
        finally:
            client.close()

    @staticmethod
    def _ssh_put_sync(config: dict, local_path: str, remote_path: str, timeout: int) -> None:
        """后台线程 SFTP 上传。"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=config["host"],
            port=int(config.get("port", 22)),
            username=config.get("user", "root"),
            key_filename=config.get("key_path"),
            password=config.get("password"),
            timeout=min(timeout, 30),
        )
        try:
            sftp = client.open_sftp()
            try:
                sftp.put(local_path, remote_path)
            finally:
                sftp.close()
        finally:
            client.close()

    def _ssh_host_config(self, host: str) -> Optional[dict]:
        """在 SSH 白名单中按主机名或别名查找配置；password_env 在此解析为实际密码。"""
        for item in self.ssh_hosts:
            if item.get("host") != host and item.get("alias") != host:
                continue
            config = dict(item)
            password_env = config.pop("password_env", None)
            if config.get("key_path"):
                return config
            if password_env:
                config["password"] = os.getenv(password_env, "")
                if config["password"]:
                    return config
            return None
        return None

    @staticmethod
    def _ssh_exec_sync(config: dict, command: str, timeout: int) -> tuple[int, str]:
        """在后台线程中执行 paramiko 连接与命令（避免阻塞事件循环）。"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=config["host"],
                port=int(config.get("port", 22)),
                username=config.get("user", "root"),
                key_filename=config.get("key_path"),
                password=config.get("password"),
                timeout=min(timeout, 30),
            )
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            output = out + err
            if len(output) > 100_000:
                output = output[:100_000] + "\n...（输出已截断）"
            return exit_code, output
        finally:
            client.close()

    @staticmethod
    async def _is_blocked_address(hostname: str) -> bool:
        try:
            infos = await asyncio.get_event_loop().getaddrinfo(hostname, None)
        except socket.gaierror:
            return True
        for info in infos[:8]:
            try:
                address = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
                return True
        return False

    @staticmethod
    def _minimal_env() -> dict[str, str]:
        """子进程只继承最小系统环境，不把服务端密钥/凭据传给被执行的命令。"""
        allowlist = {
            "PATH", "PATHEXT", "COMSPEC", "SystemRoot", "WINDIR", "TEMP", "TMP",
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LANG", "LANGUAGE", "LC_ALL", "LANG",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "APPDATA", "LOCALAPPDATA",
            "ProgramData", "ProgramFiles", "CommonProgramFiles",
        }
        return {key: value for key, value in os.environ.items() if key in allowlist}
