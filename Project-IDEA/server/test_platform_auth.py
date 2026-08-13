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
        os.environ["IDEA_CREDENTIAL_RECOVERY_KEY"] = "zUThLxmtRLhE_sOOxR5pnH0FMU1fRm1E9QzkhBEnLOg="
        cls._previous_rag_service_token = os.environ.get("RAG_IDEA_SERVICE_TOKEN")
        os.environ["RAG_IDEA_SERVICE_TOKEN"] = "test-rag-service-token"
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
        if cls._previous_rag_service_token is None:
            os.environ.pop("RAG_IDEA_SERVICE_TOKEN", None)
        else:
            os.environ["RAG_IDEA_SERVICE_TOKEN"] = cls._previous_rag_service_token
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

    def test_owner_automated_device_credentials_are_owner_only_and_one_time(self):
        member = self.password_login(self.member_email, self.member_password, "credential-member-device").json()
        member_headers = {"Authorization": f"Bearer {member['access_token']}", "X-Device-ID": "credential-member-device"}
        self.assertEqual(self.client.get("/api/platform/owner/credentials", headers=member_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/platform/owner/credentials/issue", headers=member_headers, json={"device_label": "blocked", "capability": "idea"}).status_code, 403)

        issued = self.client.post("/api/platform/owner/credentials/issue", headers=self.headers, json={"device_label": "automatic IDEA", "capability": "idea", "expires_in_days": 7})
        self.assertEqual(issued.status_code, 200)
        body = issued.json()
        self.assertTrue(body["token"].startswith("mcp_"))
        listed = self.client.get("/api/platform/owner/credentials", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(item["credential_id"] == body["credential_id"] for item in listed.json()["credentials"]))
        self.assertNotIn("token", json.dumps(listed.json()))
        recovered = self.client.get(f"/api/platform/owner/credentials/{body['credential_id']}/token", headers=self.headers)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["token"], body["token"])
        self.assertEqual(self.client.post(f"/api/platform/owner/credentials/{body['credential_id']}/revoke", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post(f"/api/platform/owner/credentials/{body['credential_id']}/revoke", headers=self.headers).status_code, 404)

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
        self.assertSetEqual({item["name"] for item in tools.json()["result"]["tools"]}, {"idea_chat", "idea_memory_save", "idea_session_get", "idea_task_status"})
        self.assertEqual(self.client.post("/mcp/memory/mcp", headers=idea_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}).status_code, 401)

        original_run = self.main.agent_runners["idea"].run

        async def fake_run(user_message, history=None, stream=False, execution_context=None):
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
            saved = self.client.post(endpoint, headers=idea_headers, json={"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "idea_memory_save", "arguments": {"category": "continuity", "content": "Owner MCP memory write marker."}}})
            self.assertEqual(saved.status_code, 200)
            saved_memory = json.loads(saved.json()["result"]["content"][0]["text"])
            self.assertEqual(saved_memory["namespace"], "owner/owner-shiroha-nao")
            self.assertIn(saved_memory["id"], {item["id"] for item in self.main.platform_store.list_memories("account-owner", "space-project-world", ["owner/owner-shiroha-nao"])})
        finally:
            self.main.agent_runners["idea"].run = original_run

        revoked = self.client.post(f"/api/platform/owner/mcp-credentials/{idea_credential.json()['credential_id']}/revoke", headers=self.headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=idea_headers, json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}}).status_code, 401)

    def reset_email_cooldown(self, email: str):
        with self.main.platform_store._connect() as connection:
            connection.execute("UPDATE email_verification_codes SET created_at = 0 WHERE email = ?", (email,))
            connection.execute("DELETE FROM auth_login_attempts WHERE email = ?", (email,))

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

    def test_memory_context_falls_back_within_authorized_namespaces(self):
        device_id = "memory-context-owner-device"
        pending_login = self.password_login(self.owner_email, self.owner_password, device_id)
        self.assertEqual(pending_login.status_code, 200)
        devices = self.client.get("/api/platform/owner/devices", headers=self.headers).json()["devices"]
        pending = next(device for device in devices if device["device_id"] == device_id)
        self.assertEqual(self.client.post(f"/api/platform/owner/devices/{pending['owner_device_id']}/approve", headers=self.headers).status_code, 200)

        owner_login = self.password_login(self.owner_email, self.owner_password, device_id)
        self.assertEqual(owner_login.status_code, 200)
        self.assertEqual(owner_login.json()["route"], "owner_idea")
        owner_principal = self.main.platform_store.authenticate(owner_login.json()["access_token"], device_id)
        owner_context = self.main.RequestContext("memory-context-owner", owner_principal, device_id, "space-project-world")
        self.main.platform_store.create_memory(
            owner_principal.account_id,
            owner_context.space_id,
            self.main._memory_namespaces(owner_context)["owner"],
            "project",
            "Owner work continuity fallback marker.",
            owner_principal.principal_id,
        )

        owner_memory_context = self.main._memory_context(owner_context, "你记得自己曾经的工作内容吗")
        self.assertIn("Owner work continuity fallback marker.", owner_memory_context)

        member_device_id = "memory-context-member-device"
        member_login = self.password_login(self.member_email, self.member_password, member_device_id)
        self.assertEqual(member_login.status_code, 200)
        member_principal = self.main.platform_store.authenticate(member_login.json()["access_token"], member_device_id)
        member_context = self.main.RequestContext("memory-context-member", member_principal, member_device_id, "space-project-world")
        member_memory_context = self.main._memory_context(member_context, "你记得自己曾经的工作内容吗")
        self.assertNotIn("Owner work continuity fallback marker.", member_memory_context)

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
    def test_daily_activity_delete_and_cleanup(self):
        chat_endpoint = next(route.endpoint for route in self.client.app.routes if getattr(route, "path", None) == "/api/assistant/chat")
        active_runners = chat_endpoint.__globals__["agent_runners"]
        original_run = active_runners["idea"].run

        async def fake_run(user_message, history=None, stream=False, llm_model_config=None, execution_context=None):
            return {"reply": "Handled.", "tool_calls_log": [{"name": "read_file", "success": True, "args": {"path": "secret"}, "result": "secret"}], "iterations": 1}

        active_runners["idea"].run = fake_run
        try:
            response = self.client.post("/api/assistant/chat", headers=self.headers, json={"agent_id": "idea", "message": "private original message"})
            self.assertEqual(response.status_code, 200)
            activities = self.client.get("/api/platform/daily-memories", headers=self.headers).json()["memories"]
            activity = next(item for item in activities if item["category"] == "activity [verified/short]")
            self.assertNotIn("private original message", activity["content"])
            self.assertEqual(self.client.request("DELETE", f"/api/platform/daily-memories/{activity['id']}", headers=self.headers, json={"expected_revision": activity["revision"] + 1}).status_code, 409)
            self.assertEqual(self.client.request("DELETE", f"/api/platform/daily-memories/{activity['id']}", headers=self.headers, json={"expected_revision": activity["revision"]}).status_code, 200)
            self.assertEqual(self.client.request("DELETE", f"/api/platform/daily-memories/{activity['id']}", headers=self.headers, json={"expected_revision": activity["revision"]}).status_code, 404)
        finally:
            active_runners["idea"].run = original_run

        async def no_tool_run(user_message, history=None, stream=False, llm_model_config=None, execution_context=None):
            return {"reply": "No tools.", "tool_calls_log": [], "iterations": 1}

        active_runners["idea"].run = no_tool_run
        try:
            before = self.client.get("/api/platform/daily-memories", headers=self.headers).json()["count"]
            self.assertEqual(self.client.post("/api/assistant/chat", headers=self.headers, json={"agent_id": "idea", "message": "ordinary chat"}).status_code, 200)
            self.assertEqual(self.client.get("/api/platform/daily-memories", headers=self.headers).json()["count"], before)
        finally:
            active_runners["idea"].run = original_run

        short = self.client.post("/api/platform/daily-memories", headers=self.headers, json={"category": "old short", "content": "remove me", "confidence": "verified", "retention": "short"}).json()
        long = self.client.post("/api/platform/daily-memories", headers=self.headers, json={"category": "old long", "content": "keep me", "confidence": "verified", "retention": "long"}).json()
        with self.main.platform_store._connect() as connection:
            connection.execute("UPDATE long_term_memories SET created_at = 0 WHERE memory_id IN (?, ?)", (short["id"], long["id"]))
        cleanup = self.client.post("/api/platform/daily-memories/cleanup", headers=self.headers)
        self.assertEqual(cleanup.status_code, 200)
        self.assertGreaterEqual(cleanup.json()["deleted_count"], 1)
        remaining = {item["id"] for item in self.client.get("/api/platform/daily-memories", headers=self.headers).json()["memories"]}
        self.assertNotIn(short["id"], remaining)
        self.assertIn(long["id"], remaining)

    def test_rag_authorize(self):
        endpoint = "/api/platform/rag/authorize"
        service_token = os.environ["RAG_IDEA_SERVICE_TOKEN"]
        missing = {"project_id": "project-missing", "permission": "documents.read"}
        os.environ.pop("RAG_IDEA_SERVICE_TOKEN")
        try:
            self.assertEqual(self.client.post(endpoint, headers=self.headers, json=missing).status_code, 401)
        finally:
            os.environ["RAG_IDEA_SERVICE_TOKEN"] = service_token
        self.assertEqual(self.client.post(endpoint, headers={**self.headers, "X-RAG-Service-Token": "bad"}, json=missing).status_code, 401)

        memory_token = self.client.post("/api/platform/owner/mcp-credentials", headers=self.headers, json={"device_label": "RAG memory"}).json()["token"]
        self.assertEqual(self.client.post(endpoint, headers={"Authorization": f"Bearer {memory_token}", "X-RAG-Service-Token": service_token}, json=missing).status_code, 401)

        owner_login = self.password_login(self.owner_email, self.owner_password, "rag-owner")
        device = next(item for item in self.client.get("/api/platform/owner/devices", headers=self.headers).json()["devices"] if item["device_id"] == "rag-owner")
        self.assertEqual(self.client.post(f"/api/platform/owner/devices/{device['owner_device_id']}/approve", headers=self.headers).status_code, 200)
        owner = self.password_login(self.owner_email, self.owner_password, "rag-owner").json()
        owner_headers = {"Authorization": f"Bearer {owner['access_token']}", "X-Device-ID": "rag-owner", "X-RAG-Service-Token": service_token}
        credential = self.client.post("/api/platform/owner/mcp-credentials", headers=owner_headers, json={"device_label": "RAG IDEA", "capability": "idea"}).json()
        credential_headers = {"Authorization": f"Bearer {credential['token']}", "X-RAG-Service-Token": service_token}

        projects = [self.main.platform_store.create_project(name, project_type, "principal-owner") for name, project_type in (("r", "research"), ("n", "novel"), ("g", "general"))]
        for project in projects:
            for permission in ("documents.read", "documents.write", "documents.delete", "index.rebuild", "rag.search"):
                self.assertEqual(self.client.post(endpoint, headers=credential_headers, json={"project_id": project["project_id"], "permission": permission}).status_code, 200)

        self.assertEqual(self.client.post(f"/api/platform/owner/mcp-credentials/{credential['credential_id']}/revoke", headers=owner_headers).status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=credential_headers, json={"project_id": projects[0]["project_id"], "permission": "documents.read"}).status_code, 401)

        member = self.password_login(self.member_email, self.member_password, "rag-member").json()
        principal = member["principal"]
        self.main.platform_store.upsert_space_member("space-project-world", principal["principal_id"], "viewer")
        self.main.platform_store.set_account_role(principal["account_id"], "researcher")
        self.main.platform_store.set_project_member(projects[0]["project_id"], principal["principal_id"], ["documents.read"])
        self.main.platform_store.set_project_member(projects[1]["project_id"], principal["principal_id"], ["documents.read"])
        member_headers = {"Authorization": f"Bearer {member['access_token']}", "X-Device-ID": "rag-member", "X-RAG-Service-Token": service_token}
        self.assertEqual(self.client.post(endpoint, headers=member_headers, json={"project_id": projects[0]["project_id"], "permission": "documents.read"}).status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=member_headers, json={"project_id": projects[1]["project_id"], "permission": "documents.read"}).status_code, 200)
        self.assertEqual(self.client.post(endpoint, headers=member_headers, json={"project_id": projects[2]["project_id"], "permission": "documents.read"}).status_code, 403)
        self.assertEqual(self.client.post(endpoint, headers=member_headers, json=missing).status_code, 404)

    def test_mcp_direct_tool_call_is_permanently_closed(self):
        self.assertEqual(self.client.post("/mcp/tools/call", headers=self.headers, json={"name": "read_file", "arguments": {}}).status_code, 410)
        self.assertEqual(self.client.post("/mcp/tools/list", headers=self.headers).status_code, 200)

    def test_tool_approval_api_flow(self):
        approval = self.main.platform_store.create_tool_approval("account-owner", "space-project-world", "principal-owner", "idea", "delete_file", "fp-delete-test", '{"file_path": "secret.txt"}')
        self.assertEqual(approval["status"], "pending")

        member = self.password_login(self.member_email, self.member_password, "approval-member-device").json()
        member_headers = {"Authorization": f"Bearer {member['access_token']}", "X-Device-ID": "approval-member-device"}
        self.assertEqual(self.client.get("/api/platform/approvals", headers=member_headers).status_code, 403)

        listing = self.client.get("/api/platform/approvals", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(any(item["approval_id"] == approval["approval_id"] for item in listing.json()["pending"]))

        approved = self.client.post(f"/api/platform/approvals/{approval['approval_id']}/approve", headers=self.headers)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(self.client.post(f"/api/platform/approvals/{approval['approval_id']}/approve", headers=self.headers).json()["status"], "approved")

        denied_approval = self.main.platform_store.create_tool_approval("account-owner", "space-project-world", "principal-owner", "idea", "run_command", "fp-command-test", '{"command": "whoami"}')
        self.assertEqual(self.client.post(f"/api/platform/approvals/{denied_approval['approval_id']}/deny", headers=self.headers).json()["status"], "denied")
        self.assertEqual(self.client.post("/api/platform/approvals/approval-missing/approve", headers=self.headers).status_code, 404)

        sync_events = self.client.get("/api/sync/events", headers=self.headers).json()["events"]
        self.assertTrue(any(event["event_type"] == "approval.requested" for event in sync_events))
        self.assertTrue(any(event["event_type"] == "approval.decided" for event in sync_events))

    def test_capability_grant_api_flow(self):
        member_login = self.password_login(self.member_email, self.member_password, "grant-member-device")
        member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}", "X-Device-ID": "grant-member-device"}
        member_account_id = member_login.json()["principal"]["account_id"]

        self.assertEqual(self.client.get("/api/platform/grants", headers=member_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/platform/grants", headers=member_headers, json={"account_id": member_account_id, "capability": "command"}).status_code, 403)

        created = self.client.post("/api/platform/grants", headers=self.headers, json={"account_id": member_account_id, "capability": "command", "workspace": "space-project-world", "expires_in_days": 7})
        self.assertEqual(created.status_code, 200)
        grant = created.json()
        self.assertEqual(grant["status"], "active")
        self.assertEqual(grant["capability"], "command")
        self.assertTrue(grant["expires_at"])

        self.assertEqual(self.client.post("/api/platform/grants", headers=self.headers, json={"account_id": member_account_id, "capability": "no-such-capability"}).status_code, 400)

        listing = self.client.get("/api/platform/grants", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(any(item["grant_id"] == grant["grant_id"] for item in listing.json()["grants"]))
        self.assertTrue(any(item["grant_id"] == grant["grant_id"] for item in self.main.platform_store.list_capability_grants(member_account_id)))

        revoked = self.client.post(f"/api/platform/grants/{grant['grant_id']}/revoke", headers=self.headers)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["status"], "revoked")
        self.assertEqual(self.client.post(f"/api/platform/grants/{grant['grant_id']}/revoke", headers=self.headers).json()["status"], "revoked")
        self.assertIsNone(self.main.platform_store.find_valid_grant(member_account_id, "command", "space-project-world"))

    def test_file_change_review_accept_and_revert_flow(self):
        root = Path(self.main.BASE_DIR).parent
        backup_dir = root / ".idea-assistant" / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = root / "fc-review-target.txt"
        backup = backup_dir / "fc-review-backup.txt"
        target.write_text("modified", encoding="utf-8")
        backup.write_text("original", encoding="utf-8")
        backup_rel = str(backup.relative_to(root)).replace("\\", "/")
        try:
            review = self.main.platform_store.create_file_change_review("account-owner", "space-project-world", "principal-owner", "idea", "edit_file", str(target), backup_rel, "--- a\n+++ b")
            member = self.password_login(self.member_email, self.member_password, "fc-member-device").json()
            member_headers = {"Authorization": f"Bearer {member['access_token']}", "X-Device-ID": "fc-member-device"}
            self.assertEqual(self.client.get("/api/platform/file-changes", headers=member_headers).status_code, 403)

            listing = self.client.get("/api/platform/file-changes", headers=self.headers)
            self.assertEqual(listing.status_code, 200)
            self.assertTrue(any(item["change_id"] == review["change_id"] for item in listing.json()["changes"]))

            accepted = self.client.post(f"/api/platform/file-changes/{review['change_id']}/accept", headers=self.headers)
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["status"], "accepted")
            self.assertEqual(self.client.post(f"/api/platform/file-changes/{review['change_id']}/revert", headers=self.headers).status_code, 409)

            review2 = self.main.platform_store.create_file_change_review("account-owner", "space-project-world", "principal-owner", "idea", "edit_file", str(target), backup_rel, "diff")
            reverted = self.client.post(f"/api/platform/file-changes/{review2['change_id']}/revert", headers=self.headers)
            self.assertEqual(reverted.status_code, 200)
            self.assertEqual(reverted.json()["status"], "reverted")
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(self.client.post("/api/platform/file-changes/change-missing/revert", headers=self.headers).status_code, 404)
        finally:
            target.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)

    def test_platform_auth_phase_one_roles_daily_projects_and_memory_scopes(self):
        self.reset_email_cooldown(self.member_email)
        member_login = self.password_login(self.member_email, self.member_password, "phase-one-member-device")
        self.assertEqual(member_login.status_code, 200)
        member_headers = {
            "Authorization": f"Bearer {member_login.json()['access_token']}",
            "X-Device-ID": "phase-one-member-device",
            "X-Space-ID": "space-project-world",
        }
        member_principal_id = member_login.json()["principal"]["principal_id"]
        member_account_id = member_login.json()["principal"]["account_id"]
        self.main.platform_store.upsert_space_member("space-project-world", member_principal_id, "viewer")

        for endpoint in (f"/api/platform/account-role?account_id={member_account_id}", "/api/platform/projects", "/api/platform/daily-memories"):
            self.assertEqual(self.client.get(endpoint, headers=member_headers).status_code, 403)
        self.assertEqual(self.client.put("/api/platform/account-role", headers=member_headers, json={"account_id": member_account_id, "work_role": "researcher"}).status_code, 403)

        owner_login = self.password_login(self.owner_email, self.owner_password, "phase-one-owner-device")
        self.assertEqual(owner_login.status_code, 200)
        devices = self.client.get("/api/platform/owner/devices", headers=self.headers).json()["devices"]
        pending = next(device for device in devices if device["device_id"] == "phase-one-owner-device")
        self.assertEqual(self.client.post(f"/api/platform/owner/devices/{pending['owner_device_id']}/approve", headers=self.headers).status_code, 200)
        owner_login = self.password_login(self.owner_email, self.owner_password, "phase-one-owner-device")
        owner_headers = {"Authorization": f"Bearer {owner_login.json()['access_token']}", "X-Device-ID": "phase-one-owner-device"}

        daily = self.client.post("/api/platform/daily-memories", headers=owner_headers, json={"category": "progress", "content": "Approved owner daily marker.", "confidence": "verified", "retention": "long"})
        self.assertEqual(daily.status_code, 200)
        self.assertTrue(any(item["id"] == daily.json()["id"] for item in self.client.get("/api/platform/daily-memories", headers=owner_headers).json()["memories"]))
        self.assertEqual(self.client.get("/api/platform/daily-memories", headers=member_headers).status_code, 403)

        self.assertEqual(self.client.put("/api/platform/account-role", headers=owner_headers, json={"account_id": member_account_id, "work_role": "owner"}).status_code, 403)
        self.assertEqual(self.client.put("/api/platform/account-role", headers=owner_headers, json={"account_id": owner_login.json()["principal"]["account_id"], "work_role": "user"}).status_code, 403)
        project = self.client.post("/api/platform/projects", headers=owner_headers, json={"name": "Phase one project", "project_type": "research"})
        self.assertEqual(project.status_code, 200)
        project_id = project.json()["project_id"]
        permissions = ["documents.read", "documents.write", "documents.delete", "rag.search"]
        granted = self.client.put(f"/api/platform/projects/{project_id}/members", headers=owner_headers, json={"principal_id": member_principal_id, "permissions": permissions})
        self.assertEqual(granted.status_code, 200)
        self.assertEqual(granted.json()["permissions"], permissions)
        self.assertTrue(all(self.main.platform_store.project_permission_allowed(project_id, member_principal_id, permission) for permission in permissions))

        other_email, other_password = "phase-one-other@example.test", os.urandom(24).hex()
        self.main.platform_store.seed_preconfigured_accounts([{"email": other_email, "password": other_password, "name": "Phase One Other", "is_owner": False}])
        other_login = self.password_login(other_email, other_password, "phase-one-other-device")
        other_principal_id = other_login.json()["principal"]["principal_id"]
        self.assertFalse(self.main.platform_store.project_permission_allowed(project_id, other_principal_id, "documents.read"))

        legacy = self.client.post("/api/memories", headers=self.headers, json={"scope": "shared", "category": "legacy", "content": "Legacy shared marker.", "confirmed": True})
        self.assertEqual(legacy.status_code, 200)
        self.assertTrue(any(item["id"] == legacy.json()["id"] for item in self.client.get("/api/memories?query=Legacy shared", headers=self.headers).json()["memories"]))
        scoped = self.client.post("/api/memories", headers=self.headers, json={"scope": "project", "category": "project", "content": "Project scope marker.", "confirmed": True})
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(scoped.json()["namespace"], "project/space-project-world")


if __name__ == "__main__":
    unittest.main()
