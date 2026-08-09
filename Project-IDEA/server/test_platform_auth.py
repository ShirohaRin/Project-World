import importlib
import json
import os
import sys
import tempfile
import unittest
import gc
from pathlib import Path

from fastapi.testclient import TestClient


class PlatformApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["IDEA_AUTH_TOKEN"] = "test-platform-token"
        os.environ["IDEA_PLATFORM_DB_PATH"] = str(Path(cls.temp_dir.name) / "platform.db")
        os.environ["IDEA_AUTH_DEVELOPMENT_MODE"] = "true"
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        cls.headers = {"Authorization": "Bearer test-platform-token"}
        cls.member_email = "member-login@example.test"
        cls.owner_email = "owner-login@example.test"
        cls.member_password = os.urandom(24).hex()
        cls.owner_password = os.urandom(24).hex()
        cls.main.platform_store.seed_preconfigured_accounts([
            {"email": cls.member_email, "password": cls.member_password, "name": "Member Login", "is_owner": False},
            {"email": cls.owner_email, "password": cls.owner_password, "name": "Owner Login", "is_owner": True},
        ])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.main.memory_store.close()
        gc.collect()
        cls.temp_dir.cleanup()
        os.environ.pop("IDEA_AUTH_TOKEN", None)
        os.environ.pop("IDEA_PLATFORM_DB_PATH", None)
        os.environ.pop("IDEA_AUTH_DEVELOPMENT_MODE", None)
        sys.modules.pop("main", None)

    def test_platform_endpoints_require_a_token(self):
        response = self.client.get("/api/platform/me")
        self.assertEqual(response.status_code, 401)

        mcp_response = self.client.post("/mcp/memory", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(mcp_response.status_code, 401)

    def test_streamable_memory_mcp_initialize_list_and_call(self):
        created = self.client.post("/api/memories", headers=self.headers, json={"scope": "owner", "category": "acceptance", "content": "MCP protocol acceptance marker.", "confirmed": True})
        self.assertEqual(created.status_code, 200)
        memory_id = created.json()["id"]
        endpoint = "/mcp/memory/mcp"
        initialize = self.client.post(endpoint, headers=self.mcp_headers(), json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "idea-unittest", "version": "1.0"}}})
        self.assertEqual(initialize.status_code, 200)
        self.assertEqual(initialize.json()["id"], 1)
        self.assertIn("tools", initialize.json()["result"]["capabilities"])
        tools = self.client.post(endpoint, headers=self.mcp_headers(), json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(tools.status_code, 200)
        self.assertSetEqual({item["name"] for item in tools.json()["result"]["tools"]}, {"memory_search", "memory_get"})
        searched = self.client.post(endpoint, headers=self.mcp_headers(), json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "protocol acceptance", "limit": 5}}})
        self.assertEqual(searched.status_code, 200)
        result = searched.json()["result"]
        self.assertFalse(result.get("isError", False))
        self.assertTrue(any(item["id"] == memory_id for item in json.loads(result["content"][0]["text"])["memories"]))
        fetched = self.client.post(endpoint, headers=self.mcp_headers(), json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory_get", "arguments": {"memory_id": memory_id}}})
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(json.loads(fetched.json()["result"]["content"][0]["text"])["id"], memory_id)

    def test_trae_mcp_credentials_are_read_only_and_revocable(self):
        created = self.client.post(
            "/api/memories",
            headers=self.headers,
            json={"scope": "owner", "category": "acceptance", "content": "TRAE credential memory marker.", "confirmed": True},
        )
        self.assertEqual(created.status_code, 200)
        credential = self.client.post(
            "/api/platform/owner/mcp-credentials",
            headers=self.headers,
            json={"device_label": "TRAE test device"},
        )
        self.assertEqual(credential.status_code, 200)
        body = credential.json()
        self.assertTrue(body["token"].startswith("mcp_"))
        credential_headers = {
            "Authorization": f"Bearer {body['token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": "localhost:8000",
            "MCP-Protocol-Version": "2025-11-25",
            "X-Space-ID": "space-not-allowed",
        }
        endpoint = "/mcp/memory/mcp"
        tools = self.client.post(endpoint, headers=credential_headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertEqual(tools.status_code, 200)
        self.assertSetEqual({item["name"] for item in tools.json()["result"]["tools"]}, {"memory_search", "memory_get"})
        search = self.client.post(endpoint, headers=credential_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "TRAE credential", "limit": 5}}})
        self.assertEqual(search.status_code, 200)
        self.assertTrue(any(item["id"] == created.json()["id"] for item in json.loads(search.json()["result"]["content"][0]["text"])["memories"]))
        self.assertEqual(self.client.get("/api/memories", headers={"Authorization": f"Bearer {body['token']}"}).status_code, 401)
        self.assertEqual(self.client.post("/mcp", headers=credential_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}).status_code, 401)
        revoked = self.client.post(f"/api/platform/owner/mcp-credentials/{body['credential_id']}/revoke", headers=self.headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=credential_headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}).status_code, 401)

    def test_owner_agent_mcp_is_isolated_and_sessions_continue(self):
        memory_credential = self.client.post(
            "/api/platform/owner/mcp-credentials",
            headers=self.headers,
            json={"device_label": "memory credential"},
        ).json()
        idea_credential = self.client.post(
            "/api/platform/owner/mcp-credentials",
            headers=self.headers,
            json={"device_label": "IDEA credential A", "capability": "idea"},
        )
        self.assertEqual(idea_credential.status_code, 200)
        idea_credential_b = self.client.post(
            "/api/platform/owner/mcp-credentials",
            headers=self.headers,
            json={"device_label": "IDEA credential B", "capability": "idea"},
        )
        self.assertEqual(idea_credential_b.status_code, 200)
        endpoint = "/mcp/idea/mcp"
        memory_headers = {"Authorization": f"Bearer {memory_credential['token']}", "Content-Type": "application/json", "Accept": "application/json", "Host": "localhost:8000"}
        self.assertEqual(self.client.post(endpoint, headers=memory_headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).status_code, 401)
        idea_headers = {"Authorization": f"Bearer {idea_credential.json()['token']}", "Content-Type": "application/json", "Accept": "application/json", "Host": "localhost:8000"}
        tools = self.client.post(endpoint, headers=idea_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(tools.status_code, 200)
        self.assertSetEqual({item["name"] for item in tools.json()["result"]["tools"]}, {"idea_chat", "idea_session_get", "idea_task_status"})
        self.assertEqual(self.client.post("/mcp/memory/mcp", headers=idea_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}).status_code, 401)

        original_run = self.main.agent_runners["idea"].run

        async def fake_run(user_message, history=None, stream=False):
            return {"reply": "Owner agent reply", "tool_calls_log": [], "iterations": 1}

        self.main.agent_runners["idea"].run = fake_run
        try:
            chat = self.client.post(endpoint, headers=idea_headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "idea_chat", "arguments": {"message": "Continue this on desktop", "use_memory": False}}})
            self.assertEqual(chat.status_code, 200)
            payload = json.loads(chat.json()["result"]["content"][0]["text"])
            self.assertEqual(payload["agent_id"], "idea")
            conversation_id = payload["conversation_id"]
            second_headers = {"Authorization": f"Bearer {idea_credential_b.json()['token']}", "Content-Type": "application/json", "Accept": "application/json", "Host": "localhost:8000"}
            session = self.client.post(endpoint, headers=second_headers, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "idea_session_get", "arguments": {"conversation_id": conversation_id}}})
            self.assertEqual(session.status_code, 200)
            messages = json.loads(session.json()["result"]["content"][0]["text"])["messages"]
            self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        finally:
            self.main.agent_runners["idea"].run = original_run

        revoked = self.client.post(f"/api/platform/owner/mcp-credentials/{idea_credential.json()['credential_id']}/revoke", headers=self.headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=idea_headers, json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}}).status_code, 401)

    def reset_email_cooldown(self, email: str):
        with self.main.platform_store._connect() as connection:
            connection.execute("UPDATE email_verification_codes SET created_at = 0 WHERE email = ?", (email,))

    def mcp_headers(self):
        return {**self.headers, "Content-Type": "application/json", "Accept": "application/json", "Host": "localhost:8000", "MCP-Protocol-Version": "2025-11-25"}

    def test_owner_bootstrap_and_space_resolution(self):
        response = self.client.get("/api/platform/me", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "owner")

        spaces = self.client.get("/api/platform/spaces", headers=self.headers)
        self.assertEqual(spaces.status_code, 200)
        self.assertEqual(spaces.json()["spaces"][0]["space_id"], "space-project-world")

        denied = self.client.get(
            "/api/platform/me",
            headers={**self.headers, "X-Space-ID": "space-not-allowed"},
        )
        self.assertEqual(denied.status_code, 403)

    def test_conversations_and_tasks_are_scoped_to_space(self):
        created = self.client.post("/api/conversations/new", headers=self.headers, json={"agent_id": "idea"})
        self.assertEqual(created.status_code, 200)
        conversation_id = created.json()["id"]

        visible = self.client.get("/api/conversations", headers=self.headers)
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.json()["count"], 1)

        missing = self.client.get(
            f"/api/conversations/{conversation_id}",
            headers={**self.headers, "X-Space-ID": "space-not-allowed"},
        )
        self.assertEqual(missing.status_code, 403)

        task = self.client.post(
            "/api/tasks",
            headers=self.headers,
            json={"title": "Scoped task", "agent_id": "idea", "conversation_id": conversation_id, "status": "completed"},
        )
        self.assertEqual(task.status_code, 200)
        self.assertEqual(task.json()["status"], "pending")

        tasks = self.client.get("/api/tasks", headers=self.headers)
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json()["count"], 1)

    def test_conversations_tasks_and_events_survive_module_reload(self):
        created = self.client.post("/api/conversations/new", headers=self.headers, json={"agent_id": "idea"})
        self.assertEqual(created.status_code, 200)
        conversation_id = created.json()["id"]
        task = self.client.post("/api/tasks", headers=self.headers, json={"title": "Continue elsewhere", "agent_id": "idea", "conversation_id": conversation_id})
        self.assertEqual(task.status_code, 200)
        first_events = self.client.get("/api/sync/events", headers=self.headers)
        self.assertEqual(first_events.status_code, 200)
        self.assertGreaterEqual(len(first_events.json()["events"]), 2)
        cursor = first_events.json()["next_cursor"]
        self.assertEqual(self.client.get(f"/api/sync/events?after={cursor}", headers=self.headers).json()["events"], [])

        isolated_client = TestClient(self.main.app)
        isolated_client.close()
        self.main.memory_store.close()
        sys.modules.pop("main", None)
        reloaded_main = importlib.import_module("main")
        reloaded_client = TestClient(reloaded_main.app)

        restored = reloaded_client.get(f"/api/conversations/{conversation_id}", headers=self.headers)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["id"], conversation_id)
        tasks = reloaded_client.get("/api/tasks", headers=self.headers)
        self.assertTrue(any(item["id"] == task.json()["id"] for item in tasks.json()["tasks"]))
        reloaded_client.close()
        reloaded_main.memory_store.close()
        sys.modules.pop("main", None)
        type(self).main = importlib.import_module("main")

    def password_login(self, email: str, password: str, device_id: str):
        return self.client.post("/api/auth/password/login", headers={"X-Device-ID": device_id}, json={"email": email, "password": password})

    def test_password_login_only_allows_preconfigured_accounts_and_throttles_members(self):
        unknown = self.password_login("unknown-login@example.test", os.urandom(24).hex(), "unknown-device")
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(unknown.json()["detail"], "邮箱或密码错误")

        for _ in range(5):
            failed = self.password_login(self.member_email, os.urandom(24).hex(), "member-device")
            self.assertEqual(failed.status_code, 401)
        limited = self.password_login(self.member_email, os.urandom(24).hex(), "member-device")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"], "登录尝试过于频繁，请稍后再试")

    def test_owner_password_login_requires_device_approval_and_member_stays_assistant(self):
        for _ in range(6):
            self.assertEqual(self.password_login(self.owner_email, os.urandom(24).hex(), "owner-device-a").status_code, 401)
        owner_pending = self.password_login(self.owner_email, self.owner_password, "owner-device-a")
        self.assertEqual(owner_pending.status_code, 200)
        self.assertEqual(owner_pending.json()["route"], "idea_assistant")

        devices = self.client.get("/api/platform/owner/devices", headers=self.headers).json()["devices"]
        pending = next(device for device in devices if device["device_id"] == "owner-device-a")
        self.assertEqual(self.client.post(f"/api/platform/owner/devices/{pending['owner_device_id']}/approve", headers=self.headers).status_code, 200)
        owner_approved = self.password_login(self.owner_email, self.owner_password, "owner-device-a")
        self.assertEqual(owner_approved.status_code, 200)
        self.assertEqual(owner_approved.json()["route"], "owner_idea")

        member = self.password_login(self.member_email, self.member_password, "member-agent-device")
        self.assertEqual(member.status_code, 200)
        member_headers = {"Authorization": f"Bearer {member.json()['access_token']}", "X-Device-ID": "member-agent-device"}
        conversation = self.client.post("/api/conversations/new", headers=member_headers, json={"agent_id": "idea"})
        self.assertEqual(conversation.status_code, 200)
        self.assertEqual(conversation.json()["agent_id"], "idea_assistant")

    def test_email_login_endpoints_are_gone(self):
        self.assertEqual(self.client.post("/api/auth/email/send", json={"email": self.member_email}).status_code, 410)
        self.assertEqual(self.client.post("/api/auth/email/verify", json={"email": self.member_email, "code": "000000"}).status_code, 410)

    def test_long_term_memories_require_explicit_write_and_support_soft_delete(self):
        denied = self.client.get("/api/memories")
        self.assertEqual(denied.status_code, 401)

        unconfirmed = self.client.post(
            "/api/memories",
            headers=self.headers,
            json={"scope": "owner", "category": "preference", "content": "Do not save this."},
        )
        self.assertEqual(unconfirmed.status_code, 400)

        created = self.client.post(
            "/api/memories",
            headers=self.headers,
            json={"scope": "owner", "category": "preference", "content": "Use Chinese by default.", "confirmed": True},
        )
        self.assertEqual(created.status_code, 200)
        memory_id = created.json()["id"]

        listed = self.client.get("/api/memories?query=Chinese", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(item["id"] == memory_id for item in listed.json()["memories"]))

        updated = self.client.put(
            f"/api/memories/{memory_id}",
            headers=self.headers,
            json={"category": "preference", "content": "Use Simplified Chinese by default.", "expected_revision": 1},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertIn("Simplified", updated.json()["content"])
        self.assertEqual(updated.json()["revision"], 2)

        conflict = self.client.put(
            f"/api/memories/{memory_id}",
            headers=self.headers,
            json={"category": "preference", "content": "Stale update.", "expected_revision": 1},
        )
        self.assertEqual(conflict.status_code, 409)

        deleted = self.client.request("DELETE", f"/api/memories/{memory_id}", headers=self.headers, json={"expected_revision": 2})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(any(item["id"] == memory_id for item in self.client.get("/api/memories", headers=self.headers).json()["memories"]))

    def test_member_cannot_use_owner_memory_scope(self):
        login = self.password_login(self.member_email, self.member_password, "memory-member-device")
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Device-ID": "memory-member-device"}
        denied = self.client.post(
            "/api/memories",
            headers=headers,
            json={"scope": "owner", "category": "private", "content": "must not be written", "confirmed": True},
        )
        self.assertEqual(denied.status_code, 403)

    def test_shared_memory_honors_roles_revisions_and_syncs_to_members(self):
        viewer_email = "viewer-login@example.test"
        viewer_password = os.urandom(24).hex()
        self.main.platform_store.seed_preconfigured_accounts([
            {"email": viewer_email, "password": viewer_password, "name": "Viewer Login", "is_owner": False},
        ])
        login = self.password_login(viewer_email, viewer_password, "viewer-device")
        viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Space-ID": "space-project-world", "X-Device-ID": "viewer-device"}
        viewer_principal_id = login.json()["principal"]["principal_id"]
        self.main.platform_store.upsert_space_member("space-project-world", viewer_principal_id, "viewer")

        created = self.client.post(
            "/api/memories",
            headers=self.headers,
            json={"scope": "shared", "category": "project", "content": "Shared E2E marker.", "confirmed": True},
        )
        self.assertEqual(created.status_code, 200)
        memory = created.json()
        self.assertEqual(memory["revision"], 1)

        visible = self.client.get("/api/memories?query=E2E", headers=viewer_headers)
        self.assertTrue(any(item["id"] == memory["id"] for item in visible.json()["memories"]))
        self.assertTrue(any(event["aggregate_id"] == memory["id"] for event in self.client.get("/api/sync/events", headers=viewer_headers).json()["events"]))

        denied = self.client.put(
            f"/api/memories/{memory['id']}",
            headers=viewer_headers,
            json={"category": "project", "content": "Viewer overwrite.", "expected_revision": 1},
        )
        self.assertEqual(denied.status_code, 403)

        updated = self.client.put(
            f"/api/memories/{memory['id']}",
            headers=self.headers,
            json={"category": "project", "content": "Owner update.", "expected_revision": 1},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 2)


if __name__ == "__main__":
    unittest.main()
