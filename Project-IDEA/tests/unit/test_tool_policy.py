"""Tool policy and sandbox boundary tests (round 1: tool calling system hardening)."""

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

from platform_auth import Principal, RequestContext
from tool_runtime.permissions import ExecutionContext, PolicyDecision, ToolPolicy
from tool_runtime.registry import ToolRegistry, ToolResult


class RecordingAuditStore:
    def __init__(self):
        self.events = []

    def write_audit(self, event_type, context=None, **fields):
        self.events.append({"event_type": event_type, "context": context, **fields})


class InMemoryApprovalStore(RecordingAuditStore):
    """模拟 PlatformStore 的审批与授权接口，便于在内存中验证审批/授权闭环。"""

    def __init__(self):
        super().__init__()
        self.approvals = {}
        self.grants = {}
        self.reviews = {}
        self._seq = 0

    def create_file_change_review(self, account_id, space_id, principal_id, agent_id, tool_name, file_path, backup_path, diff_summary):
        self._seq += 1
        record = {
            "change_id": f"change-{self._seq}",
            "account_id": account_id,
            "space_id": space_id,
            "principal_id": principal_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "file_path": file_path,
            "backup_path": backup_path,
            "diff_summary": diff_summary,
            "status": "pending",
            "created_at": time.time(),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        self.reviews[record["change_id"]] = record
        return record

    def create_capability_grant(self, account_id, granted_by, capability, workspace="", constraints=None, expires_in_days=None):
        self._seq += 1
        record = {
            "grant_id": f"grant-{self._seq}",
            "account_id": account_id,
            "granted_by": granted_by,
            "capability": capability,
            "workspace": workspace or "",
            "constraints_json": "{}",
            "status": "active",
            "created_at": time.time(),
            "expires_at": (time.time() + expires_in_days * 86400) if expires_in_days else None,
            "revoked_at": None,
            "revoked_by": None,
        }
        self.grants[record["grant_id"]] = record
        return record

    def find_valid_grant(self, account_id, capability, workspace=""):
        now = time.time()
        matches = [
            record for record in self.grants.values()
            if record["account_id"] == account_id and record["capability"] == capability
            and record["status"] == "active" and (record["expires_at"] is None or record["expires_at"] > now)
            and (record["workspace"] == "" or record["workspace"] == workspace)
        ]
        return max(matches, key=lambda record: record["created_at"]) if matches else None

    def list_capability_grants(self, account_id=None, limit=100):
        records = [record for record in self.grants.values() if not account_id or record["account_id"] == account_id]
        return records[:limit]

    def revoke_capability_grant(self, grant_id, revoked_by):
        record = self.grants.get(grant_id)
        if not record:
            return None
        record["status"] = "revoked"
        record["revoked_at"] = time.time()
        record["revoked_by"] = revoked_by
        return dict(record)

    def create_tool_approval(self, account_id, space_id, principal_id, agent_id, tool_name, fingerprint, args_summary, ttl_seconds=600):
        self._seq += 1
        record = {
            "approval_id": f"approval-{self._seq}",
            "account_id": account_id,
            "space_id": space_id,
            "principal_id": principal_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "fingerprint": fingerprint,
            "args_summary": args_summary,
            "status": "pending",
            "requested_at": time.time(),
            "expires_at": time.time() + ttl_seconds,
            "decided_at": None,
            "decided_by": None,
        }
        self.approvals[record["approval_id"]] = record
        return record

    def find_tool_approval(self, account_id, space_id, principal_id, tool_name, fingerprint):
        matches = [
            record for record in self.approvals.values()
            if record["account_id"] == account_id and record["space_id"] == space_id
            and record["principal_id"] == principal_id and record["tool_name"] == tool_name
            and record["fingerprint"] == fingerprint and record["status"] != "denied"
            and record["expires_at"] > time.time()
        ]
        return max(matches, key=lambda record: record["requested_at"]) if matches else None

    def decide_tool_approval(self, approval_id, account_id, space_id, decision, decided_by):
        record = self.approvals.get(approval_id)
        if not record:
            return None
        record["status"] = decision
        record["decided_at"] = time.time()
        record["decided_by"] = decided_by
        return dict(record)


def run(coro):
    return asyncio.run(coro)


class ToolPolicyTests(unittest.TestCase):
    def test_read_tools_are_allowed_even_without_context(self):
        policy = ToolPolicy()
        for name in ("read_file", "list_dir", "search_content"):
            self.assertEqual(policy.decide(name, None).decision, PolicyDecision.ALLOW, name)

    def test_high_risk_tools_require_approval(self):
        policy = ToolPolicy()
        for name in ("run_command", "web_search", "web_fetch", "delete_file"):
            decision = policy.decide(name, None)
            self.assertEqual(decision.decision, PolicyDecision.REQUIRES_APPROVAL, name)
            self.assertEqual(decision.reason_code, "approval_required", name)

    def test_write_and_delegation_require_owner_context(self):
        policy = ToolPolicy()
        self.assertEqual(policy.decide("write_file", None).decision, PolicyDecision.DENY)
        self.assertEqual(policy.decide("write_file", None).reason_code, "execution_context_required")
        self.assertEqual(policy.decide("dispatch_to_agent", None).decision, PolicyDecision.DENY)

        owner_context = ExecutionContext(
            request_context=RequestContext("req-owner", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )
        self.assertEqual(policy.decide("write_file", owner_context).decision, PolicyDecision.ALLOW)
        self.assertEqual(policy.decide("edit_file", owner_context).decision, PolicyDecision.ALLOW)
        self.assertEqual(policy.decide("dispatch_to_agent", owner_context).decision, PolicyDecision.ALLOW)

    def test_unknown_tool_is_denied(self):
        self.assertEqual(ToolPolicy().decide("no_such_tool", None).decision, PolicyDecision.DENY)
        self.assertEqual(ToolPolicy().decide("no_such_tool", None).reason_code, "unknown_tool")


class ToolRegistryBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        cls.audit = RecordingAuditStore()
        cls.registry = ToolRegistry(workspace=str(cls.workspace), allowed_dirs=[str(cls.workspace)], audit_store=cls.audit)
        (cls.workspace / "sample.txt").write_text("hello world\nline two\n", encoding="utf-8")
        (cls.workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")
        (cls.workspace / "keys.pem").write_text("private key\n", encoding="utf-8")
        (cls.workspace / ".git").mkdir()
        (cls.workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        cls.owner_context = ExecutionContext(
            request_context=RequestContext("req-owner", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_read_within_workspace_succeeds(self):
        result = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, None))
        self.assertTrue(result.success, result.output)
        self.assertIn("hello world", result.output)

    def test_path_escape_is_rejected(self):
        result = run(self.registry.execute("read_file", {"file_path": str(Path(self.temp.name).parent / "outside.txt")}, None))
        self.assertFalse(result.success)
        self.assertIn("权限拒绝", result.output)

    def test_sensitive_paths_are_rejected(self):
        for name in (".env", "keys.pem", ".git/config"):
            result = run(self.registry.execute("read_file", {"file_path": str(self.workspace / name)}, None))
            self.assertFalse(result.success, name)
            self.assertIn("权限拒绝", result.output)

    def test_write_requires_owner_and_is_denied_without_context(self):
        target = str(self.workspace / "out.txt")
        denied = run(self.registry.execute("write_file", {"file_path": target, "content": "x"}, None))
        self.assertFalse(denied.success)
        self.assertEqual(denied.metadata.get("decision"), "deny")
        self.assertFalse(Path(target).exists())
        allowed = run(self.registry.execute("write_file", {"file_path": target, "content": "x"}, self.owner_context))
        self.assertTrue(allowed.success, allowed.output)
        self.assertTrue(Path(target).exists())

    def test_delete_command_and_network_require_approval_even_for_owner(self):
        for name in ("delete_file", "run_command", "web_search", "web_fetch"):
            result = run(self.registry.execute(name, {}, self.owner_context))
            self.assertFalse(result.success, name)
            self.assertEqual(result.metadata.get("decision"), "requires_approval", name)

    def test_schemas_include_approval_gated_tools(self):
        names = {schema["name"] for schema in self.registry.schemas_for(None)}
        self.assertIn("read_file", names)
        self.assertNotIn("write_file", names)
        self.assertIn("run_command", names)
        self.assertIn("web_fetch", names)

    def test_get_tool_returns_read_only_guarded_callable(self):
        guarded = self.registry.get_tool("write_file")
        result = run(guarded(file_path=str(self.workspace / "hack.txt"), content="x"))
        self.assertFalse(result.success)
        self.assertEqual(result.metadata.get("decision"), "deny")
        self.assertFalse(Path(self.workspace / "hack.txt").exists())

    def test_executions_are_audited(self):
        run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, self.owner_context))
        run(self.registry.execute("write_file", {"file_path": str(self.workspace / "out2.txt"), "content": "y"}, None))
        types = {event["event_type"] for event in self.audit.events}
        self.assertIn("tool_policy", types)
        self.assertTrue(any(event["decision"] == "allow" for event in self.audit.events))
        self.assertTrue(any(event["decision"] == "deny" for event in self.audit.events))


class ToolApprovalFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        (cls.workspace / "victim.txt").write_text("to delete", encoding="utf-8")
        (cls.workspace / "kept.txt").write_text("keep me", encoding="utf-8")
        cls.store = InMemoryApprovalStore()
        cls.registry = ToolRegistry(workspace=str(cls.workspace), allowed_dirs=[str(cls.workspace)], audit_store=cls.store)
        cls.owner_context = ExecutionContext(
            request_context=RequestContext("req-approval", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_delete_requires_approval_then_runs_after_approval(self):
        target = str(self.workspace / "victim.txt")
        first = run(self.registry.execute("delete_file", {"file_path": target}, self.owner_context))
        self.assertFalse(first.success)
        self.assertEqual(first.metadata["decision"], "requires_approval")
        approval_id = first.metadata["approval_id"]
        self.assertTrue(approval_id)
        self.assertEqual(self.store.approvals[approval_id]["status"], "pending")
        self.assertIn("victim.txt", self.store.approvals[approval_id]["args_summary"])

        second = run(self.registry.execute("delete_file", {"file_path": target}, self.owner_context))
        self.assertEqual(second.metadata["approval_id"], approval_id)
        self.assertEqual(len(self.store.approvals), 1, "相同操作不应重复创建审批请求")

        self.store.decide_tool_approval(approval_id, "account-owner", "space-1", "approved", "principal-owner")
        third = run(self.registry.execute("delete_file", {"file_path": target}, self.owner_context))
        self.assertTrue(third.success, third.output)
        self.assertFalse(Path(target).exists())

    def test_denied_approval_keeps_blocking(self):
        target = str(self.workspace / "kept.txt")
        first = run(self.registry.execute("delete_file", {"file_path": target}, self.owner_context))
        approval_id = first.metadata["approval_id"]
        self.store.decide_tool_approval(approval_id, "account-owner", "space-1", "denied", "principal-owner")

        second = run(self.registry.execute("delete_file", {"file_path": target}, self.owner_context))
        self.assertEqual(second.metadata["decision"], "requires_approval")
        self.assertNotEqual(second.metadata["approval_id"], approval_id)
        self.assertTrue(Path(target).exists())

    def test_different_arguments_create_separate_approval_requests(self):
        store = InMemoryApprovalStore()
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        run(registry.execute("delete_file", {"file_path": str(self.workspace / "victim.txt")}, self.owner_context))
        run(registry.execute("delete_file", {"file_path": str(self.workspace / "kept.txt")}, self.owner_context))
        self.assertEqual(len(store.approvals), 2)

    def test_run_command_executes_after_approval_with_workspace_boundary(self):
        store = InMemoryApprovalStore()
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        first = run(registry.execute("run_command", {"command": "echo approval-ok", "working_dir": "."}, self.owner_context))
        self.assertEqual(first.metadata["decision"], "requires_approval")
        store.decide_tool_approval(first.metadata["approval_id"], "account-owner", "space-1", "approved", "principal-owner")
        second = run(registry.execute("run_command", {"command": "echo approval-ok", "working_dir": "."}, self.owner_context))
        # 审批放行后应真正进入命令执行后端（$ 回显由函数体生成；是否执行成功取决于本机解释器可用性）
        self.assertIn("$ echo approval-ok", second.output)

        outside = run(registry.run_command("echo nope", str(Path(self.temp.name).parent)))
        self.assertFalse(outside.success)
        self.assertIn("权限拒绝", outside.output)

    def test_web_fetch_blocks_loopback_addresses(self):
        self.assertTrue(run(ToolRegistry._is_blocked_address("127.0.0.1")))
        self.assertTrue(run(ToolRegistry._is_blocked_address("localhost")))

    def test_web_fetch_rejects_non_http_urls_even_after_approval(self):
        store = InMemoryApprovalStore()
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        first = run(registry.execute("web_fetch", {"url": "file:///etc/passwd"}, self.owner_context))
        store.decide_tool_approval(first.metadata["approval_id"], "account-owner", "space-1", "approved", "principal-owner")
        second = run(registry.execute("web_fetch", {"url": "file:///etc/passwd"}, self.owner_context))
        self.assertFalse(second.success)
        self.assertIn("仅支持 http/https", second.output)


class RollbackFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        cls.owner_context = ExecutionContext(
            request_context=RequestContext("req-rb", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.store = InMemoryApprovalStore()
        self.store.create_capability_grant("account-owner", "principal-owner", "file.delete", workspace="space-1")
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=self.store)

    def test_edit_backs_up_and_restore_recovers_original(self):
        target = self.workspace / "edit-me.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        edited = run(self.registry.execute("edit_file", {"file_path": str(target), "old_str": "alpha", "new_str": "omega"}, self.owner_context))
        self.assertTrue(edited.success, edited.output)
        self.assertIn("backup", edited.metadata)
        self.assertEqual(target.read_text(encoding="utf-8"), "omega\nbeta\n")
        self.assertEqual(len(self.store.reviews), 1, "文件修改后应自动记入待审查队列")
        review = list(self.store.reviews.values())[0]
        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["tool_name"], "edit_file")
        self.assertIn("+omega", review["diff_summary"])
        self.assertIn("-alpha", review["diff_summary"])

        restored = run(self.registry.execute("restore_file", {"file_path": str(target)}, self.owner_context))
        self.assertTrue(restored.success, restored.output)
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nbeta\n")

    def test_delete_backs_up_and_restore_recovers(self):
        target = self.workspace / "delete-me.txt"
        target.write_text("keep this", encoding="utf-8")
        deleted = run(self.registry.execute("delete_file", {"file_path": str(target)}, self.owner_context))
        self.assertTrue(deleted.success, deleted.output)
        self.assertFalse(target.exists())
        self.assertIn("backup", deleted.metadata)

        restored = run(self.registry.execute("restore_file", {"file_path": str(target)}, self.owner_context))
        self.assertTrue(restored.success, restored.output)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep this")

    def test_restore_without_backup_is_rejected(self):
        target = self.workspace / "never-touched.txt"
        target.write_text("fresh", encoding="utf-8")
        restored = run(self.registry.execute("restore_file", {"file_path": str(target)}, self.owner_context))
        self.assertFalse(restored.success)
        self.assertIn("没有可恢复的备份", restored.output)

    def test_restore_rejects_backup_path_escape(self):
        target = self.workspace / "escape.txt"
        target.write_text("x", encoding="utf-8")
        restored = run(self.registry.execute("restore_file", {"file_path": str(target), "backup_path": "../../etc/passwd"}, self.owner_context))
        self.assertFalse(restored.success)
        self.assertIn("备份文件不存在或无效", restored.output)

    def test_backup_directory_is_sensitive_to_read_tool(self):
        backup_dir = self.workspace / ".idea-assistant"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "secret.bak").write_text("sensitive", encoding="utf-8")
        result = run(self.registry.execute("read_file", {"file_path": str(backup_dir / "secret.bak")}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("权限拒绝", result.output)


class CommandAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.store = InMemoryApprovalStore()
        self.store.create_capability_grant("account-owner", "principal-owner", "command", workspace="space-1")
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=self.store)
        self.owner_context = ExecutionContext(
            request_context=RequestContext("req-cmd", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )

    def test_dangerous_commands_are_permanently_blocked_even_with_grant(self):
        for command in ("rm -rf /", "git push origin main", "git reset --hard HEAD", "rmdir /s C:\\Windows", "shutdown /s", "format c:"):
            result = run(self.registry.execute("run_command", {"command": command, "working_dir": "."}, self.owner_context))
            self.assertFalse(result.success, command)
            self.assertIn("命令黑名单", result.output, command)

    def test_safe_commands_still_execute(self):
        result = run(self.registry.execute("run_command", {"command": "echo hello", "working_dir": "."}, self.owner_context))
        self.assertEqual(result.metadata["decision"], "allow")
        self.assertIn("$ echo hello", result.output)


class SshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        cls.hosts = [
            {"host": "prod-1.example.com", "alias": "prod", "port": 22, "user": "deploy", "key_path": "/keys/id_ed25519"},
            {"host": "backup.example.com", "alias": "backup", "user": "root", "password_env": "IDEA_SSH_PW_BACKUP"},
        ]
        cls.owner_context = ExecutionContext(
            request_context=RequestContext("req-ssh", Principal("p-owner", "account-owner", "owner", "token-owner"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.store = InMemoryApprovalStore()
        self.store.create_capability_grant("account-owner", "principal-owner", "ssh", workspace="space-1")
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=self.store, ssh_hosts=self.hosts)

    def test_unknown_host_is_rejected(self):
        result = run(self.registry.execute("ssh_run", {"host": "evil.example.com", "command": "id"}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("白名单", result.output)

    def test_host_config_resolution(self):
        config = self.registry._ssh_host_config("prod")
        self.assertIsNotNone(config)
        self.assertEqual(config["host"], "prod-1.example.com")
        self.assertEqual(config["key_path"], "/keys/id_ed25519")
        self.assertNotIn("password", config)
        self.assertIsNone(self.registry._ssh_host_config("unknown"))

    def test_password_host_requires_env_password(self):
        old = os.environ.get("IDEA_SSH_PW_BACKUP")
        os.environ.pop("IDEA_SSH_PW_BACKUP", None)
        try:
            self.assertIsNone(self.registry._ssh_host_config("backup"))
        finally:
            if old is not None:
                os.environ["IDEA_SSH_PW_BACKUP"] = old

    def test_ssh_run_connection_failure_is_reported_not_crash(self):
        result = run(self.registry.execute("ssh_run", {"host": "prod", "command": "echo ok"}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("SSH", result.output)

    def test_ssh_transfer_rejects_unknown_host(self):
        result = run(self.registry.execute("ssh_get", {"host": "evil.example.com", "remote_path": "/etc/hosts", "local_path": "hosts.txt"}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("白名单", result.output)
        result = run(self.registry.execute("ssh_put", {"host": "evil.example.com", "local_path": "hosts.txt", "remote_path": "/tmp/hosts"}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("白名单", result.output)

    def test_ssh_put_rejects_blocked_remote_paths(self):
        (self.workspace / "payload.txt").write_text("x", encoding="utf-8")
        for remote in ("/etc/passwd", "/etc/shadow", "/root/.ssh/authorized_keys", "/home/deploy/.ssh/id_ed25519"):
            result = run(self.registry.execute("ssh_put", {"host": "prod", "local_path": str(self.workspace / "payload.txt"), "remote_path": remote}, self.owner_context))
            self.assertFalse(result.success, remote)
            self.assertIn("远程路径禁止写入", result.output)

    def test_ssh_put_rejects_missing_or_outside_local_file(self):
        missing = run(self.registry.execute("ssh_put", {"host": "prod", "local_path": str(self.workspace / "nope.txt"), "remote_path": "/tmp/nope.txt"}, self.owner_context))
        self.assertFalse(missing.success)
        self.assertIn("本地文件不存在", missing.output)
        outside = run(self.registry.execute("ssh_put", {"host": "prod", "local_path": str(Path(self.temp.name).parent / "outside.txt"), "remote_path": "/tmp/x.txt"}, self.owner_context))
        self.assertFalse(outside.success)
        self.assertIn("权限拒绝", outside.output)

    def test_ssh_get_rejects_local_path_outside_workspace(self):
        result = run(self.registry.execute("ssh_get", {"host": "prod", "remote_path": "/etc/hosts", "local_path": str(Path(self.temp.name).parent / "x.txt")}, self.owner_context))
        self.assertFalse(result.success)
        self.assertIn("权限拒绝", result.output)

    def test_ssh_transfer_without_grant_requires_approval(self):
        store = InMemoryApprovalStore()
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store, ssh_hosts=self.hosts)
        result = run(registry.execute("ssh_get", {"host": "prod", "remote_path": "/etc/hosts", "local_path": "x.txt"}, self.owner_context))
        self.assertEqual(result.metadata["decision"], "requires_approval")


class GrantFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        (cls.workspace / "gfile.txt").write_text("grant target", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.store = InMemoryApprovalStore()
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=self.store)
        self.member_context = ExecutionContext(
            request_context=RequestContext("req-member", Principal("p-member", "account-member", "user", "token-member"), "dev-1", "space-project-world"),
            agent_id="idea",
            is_owner=False,
        )

    def test_grant_bypasses_approval_for_gated_tool(self):
        target = str(self.workspace / "gfile.txt")
        blocked = run(self.registry.execute("delete_file", {"file_path": target}, self.member_context))
        self.assertEqual(blocked.metadata["decision"], "requires_approval")

        self.store.create_capability_grant("account-member", "principal-owner", "file.delete", workspace="space-project-world")
        allowed = run(self.registry.execute("delete_file", {"file_path": target}, self.member_context))
        self.assertTrue(allowed.success, allowed.output)
        self.assertEqual(allowed.metadata["reason"], "grant_allowed")
        self.assertFalse(Path(target).exists())

    def test_command_grant_executes_without_approval(self):
        self.store.create_capability_grant("account-member", "principal-owner", "command", workspace="space-project-world")
        result = run(self.registry.execute("run_command", {"command": "echo grant-run", "working_dir": "."}, self.member_context))
        self.assertEqual(result.metadata["decision"], "allow")
        self.assertIn("$ echo grant-run", result.output)

    def test_revoked_grant_falls_back_to_approval(self):
        (self.workspace / "g2.txt").write_text("second", encoding="utf-8")
        target = str(self.workspace / "g2.txt")
        grant = self.store.create_capability_grant("account-member", "principal-owner", "file.delete", workspace="space-project-world")
        self.assertTrue(run(self.registry.execute("delete_file", {"file_path": target}, self.member_context)).success)
        self.store.revoke_capability_grant(grant["grant_id"], "principal-owner")
        result = run(self.registry.execute("delete_file", {"file_path": target}, self.member_context))
        self.assertEqual(result.metadata["decision"], "requires_approval")

    def test_grant_scoped_to_other_workspace_does_not_apply(self):
        (self.workspace / "g3.txt").write_text("third", encoding="utf-8")
        target = str(self.workspace / "g3.txt")
        self.store.create_capability_grant("account-member", "principal-owner", "file.delete", workspace="space-other")
        result = run(self.registry.execute("delete_file", {"file_path": target}, self.member_context))
        self.assertEqual(result.metadata["decision"], "requires_approval")

    def test_expired_grant_does_not_apply(self):
        (self.workspace / "g4.txt").write_text("fourth", encoding="utf-8")
        target = str(self.workspace / "g4.txt")
        grant = self.store.create_capability_grant("account-member", "principal-owner", "file.delete", workspace="space-project-world")
        grant["expires_at"] = time.time() - 10
        result = run(self.registry.execute("delete_file", {"file_path": target}, self.member_context))
        self.assertEqual(result.metadata["decision"], "requires_approval")

    def test_grant_does_not_override_sensitive_path_boundary(self):
        target = str(self.workspace / ".env")
        self.store.create_capability_grant("account-member", "principal-owner", "file.read", workspace="space-project-world")
        result = run(self.registry.execute("read_file", {"file_path": target}, self.member_context))
        self.assertFalse(result.success)
        self.assertIn("权限拒绝", result.output)


class GuardMonotonicityTests(unittest.TestCase):
    """单调守卫：只能拒绝、拒绝不可被覆盖、可解除（对齐 dsh ToolGuard）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "sample.txt").write_text("guard target", encoding="utf-8")
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=RecordingAuditStore())

    def tearDown(self):
        self.temp.cleanup()

    def test_guard_rejects_and_denial_cannot_be_overridden(self):
        self.registry.register_guard(lambda name, args, ctx: "blocked-by-first" if name == "read_file" else None)
        # 后注册的 guard 试图放行无效：任何 guard 拒绝即拒绝（无 allow 结果）
        self.registry.register_guard(lambda name, args, ctx: None)
        result = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, None))
        self.assertFalse(result.success)
        self.assertEqual(result.metadata["reason"], "blocked-by-first")

    def test_guard_only_denies_target_tool(self):
        self.registry.register_guard(lambda name, args, ctx: "no-reads" if name == "read_file" else None)
        allowed = run(self.registry.execute("list_dir", {"path": "."}, None))
        self.assertTrue(allowed.success, allowed.output)

    def test_unregister_restores_allow(self):
        unregister = self.registry.register_guard(lambda name, args, ctx: "no-reads" if name == "read_file" else None)
        denied = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, None))
        self.assertFalse(denied.success)
        unregister()
        allowed = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, None))
        self.assertTrue(allowed.success, allowed.output)

    def test_throwing_guard_fails_closed(self):
        def broken_guard(name, args, ctx):
            raise RuntimeError("boom")

        self.registry.register_guard(broken_guard)
        result = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "sample.txt")}, None))
        self.assertFalse(result.success)
        self.assertEqual(result.metadata["reason"], "guard_internal_error")


class PolicyAwareStore(InMemoryApprovalStore):
    """带会话级审批策略的内存 store（模拟 PlatformStore.conversation_approval_policy）。"""

    def __init__(self, policy="ask"):
        super().__init__()
        self._policy = policy

    def conversation_approval_policy(self, account_id, space_id, conversation_id):
        return self._policy


class NeverApprovalPolicyTests(unittest.TestCase):
    """会话级 ask/never 审批策略：never 会话不弹审批、不创建审批请求。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _context(self, conversation_id):
        return ExecutionContext(
            request_context=RequestContext("req-1", Principal("p-1", "account-1", "member", "token-1"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=False,
            conversation_id=conversation_id,
        )

    def test_never_policy_rejects_without_creating_approval(self):
        store = PolicyAwareStore(policy="never")
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        result = run(registry.execute("run_command", {"command": "echo hi", "working_dir": "."}, self._context("conv-1")))
        self.assertEqual(result.metadata["decision"], "requires_approval")
        self.assertEqual(result.metadata["reason"], "approval_policy_never")
        self.assertEqual(len(store.approvals), 0, "never 会话不应创建审批请求")

    def test_ask_policy_creates_approval(self):
        store = PolicyAwareStore(policy="ask")
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        result = run(registry.execute("run_command", {"command": "echo hi", "working_dir": "."}, self._context("conv-1")))
        self.assertEqual(result.metadata["decision"], "requires_approval")
        self.assertEqual(result.metadata["reason"], "approval_required")
        self.assertEqual(len(store.approvals), 1)

    def test_never_without_conversation_falls_back_to_ask(self):
        store = PolicyAwareStore(policy="never")
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        result = run(registry.execute("run_command", {"command": "echo hi", "working_dir": "."}, self._context(None)))
        self.assertEqual(result.metadata["reason"], "approval_required")
        self.assertEqual(len(store.approvals), 1)


class OutputContractTests(unittest.TestCase):
    """工具输出契约：声明 output schema 后执行成功即校验（对齐 dsh ToolOutputDefinition）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ToolRegistry(workspace=self.temp.name, allowed_dirs=[self.temp.name], audit_store=RecordingAuditStore())
        self.registry._tools["json_probe"] = {
            "function": self.registry.read_file,
            "schema": self.registry._schema("json_probe", "probe", {}, []),
            "output": {"type": "object"},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_string_output_matches_string_contract(self):
        self.registry._tools["read_file"]["output"] = {"type": "string"}
        target = Path(self.temp.name) / "s.txt"
        target.write_text("x", encoding="utf-8")
        result = run(self.registry.execute("read_file", {"file_path": str(target)}, None))
        self.assertTrue(result.success)
        self.assertNotIn("output_contract", result.metadata)

    def test_non_json_output_fails_object_contract(self):
        result = ToolResult(True, "not-json", "json_probe")
        self.registry._validate_output_contract("json_probe", result)
        self.assertIn("output_contract", result.metadata)
        self.assertIn("expected object", result.metadata["output_contract"])

    def test_valid_json_object_passes_contract(self):
        result = ToolResult(True, '{"ok": true}', "json_probe")
        self.registry._validate_output_contract("json_probe", result)
        self.assertNotIn("output_contract", result.metadata)

    def test_json_array_fails_object_contract(self):
        result = ToolResult(True, "[1, 2, 3]", "json_probe")
        self.registry._validate_output_contract("json_probe", result)
        self.assertIn("output_contract", result.metadata)
        self.assertIn("got list", result.metadata["output_contract"])


class PresentationIntentTests(unittest.TestCase):
    """工具展示意图：工具结果携带 card 类型与结构化展示数据（对齐 dsh 卡片模型）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "p.txt").write_text("before", encoding="utf-8")
        self.registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=RecordingAuditStore())

    def tearDown(self):
        self.temp.cleanup()

    def test_read_file_carries_read_card(self):
        result = run(self.registry.execute("read_file", {"file_path": str(self.workspace / "p.txt")}, None))
        self.assertTrue(result.success)
        self.assertEqual(result.metadata["presentation"]["card"], "read")

    def test_edit_file_carries_diff_card_with_diffs(self):
        result = run(self.registry.execute("edit_file", {"file_path": str(self.workspace / "p.txt"), "old_str": "before", "new_str": "after"}, None))
        self.assertFalse(result.success)  # 需要 Owner 上下文
        # Owner 上下文下成功并携带 diff 卡片
        owner = ExecutionContext(
            request_context=RequestContext("req-o", Principal("p-o", "acct-o", "owner", "t-o"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )
        allowed = run(self.registry.execute("edit_file", {"file_path": str(self.workspace / "p.txt"), "old_str": "before", "new_str": "after"}, owner))
        self.assertTrue(allowed.success, allowed.output)
        presentation = allowed.metadata["presentation"]
        self.assertEqual(presentation["card"], "diff")
        self.assertEqual(presentation["diffs"][0]["path"], str(self.workspace / "p.txt"))
        self.assertIn("-before", presentation["diffs"][0]["diff"])

    def test_run_command_carries_terminal_card(self):
        store = InMemoryApprovalStore()
        store.create_capability_grant("acct-o", "owner", "command", workspace="space-1")
        registry = ToolRegistry(workspace=str(self.workspace), allowed_dirs=[str(self.workspace)], audit_store=store)
        owner = ExecutionContext(
            request_context=RequestContext("req-o", Principal("p-o", "acct-o", "owner", "t-o"), "dev-1", "space-1"),
            agent_id="idea",
            is_owner=True,
        )
        result = run(registry.execute("run_command", {"command": "echo card", "working_dir": "."}, owner))
        self.assertEqual(result.metadata["presentation"]["card"], "terminal")


if __name__ == "__main__":
    unittest.main()
