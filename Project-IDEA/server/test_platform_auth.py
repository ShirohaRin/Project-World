import importlib
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
        cls.headers = {"Authorization": "Bearer test-platform-token"}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.main.memory_store.conn.close()
        gc.collect()
        cls.temp_dir.cleanup()
        os.environ.pop("IDEA_AUTH_TOKEN", None)
        os.environ.pop("IDEA_PLATFORM_DB_PATH", None)
        os.environ.pop("IDEA_AUTH_DEVELOPMENT_MODE", None)
        sys.modules.pop("main", None)

    def test_platform_endpoints_require_a_token(self):
        response = self.client.get("/api/platform/me")
        self.assertEqual(response.status_code, 401)

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
        self.main.memory_store.conn.close()
        sys.modules.pop("main", None)
        reloaded_main = importlib.import_module("main")
        reloaded_client = TestClient(reloaded_main.app)

        restored = reloaded_client.get(f"/api/conversations/{conversation_id}", headers=self.headers)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["id"], conversation_id)
        tasks = reloaded_client.get("/api/tasks", headers=self.headers)
        self.assertTrue(any(item["id"] == task.json()["id"] for item in tasks.json()["tasks"]))
        reloaded_client.close()
        reloaded_main.memory_store.conn.close()
        sys.modules.pop("main", None)
        self.main = importlib.import_module("main")

    def test_email_login_routes_members_to_idea_assistant_and_owner_links_to_idea(self):
        email = "nao@example.com"
        sent = self.client.post("/api/auth/email/send", json={"email": email})
        self.assertEqual(sent.status_code, 200)
        code = sent.json()["development_code"]

        login = self.client.post("/api/auth/email/verify", headers={"X-Device-ID": "test-device"}, json={"email": email, "code": code})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["route"], "idea_assistant")
        member_headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Device-ID": "test-device"}

        member_conversation = self.client.post("/api/conversations/new", headers=member_headers, json={"agent_id": "idea"})
        self.assertEqual(member_conversation.status_code, 200)
        self.assertEqual(member_conversation.json()["agent_id"], "idea_assistant")

        linked = self.client.post("/api/platform/owner/link-email", headers=self.headers, json={"email": email})
        self.assertEqual(linked.status_code, 200)

        refreshed = self.client.post("/api/auth/refresh", headers={"X-Device-ID": "test-device"}, json={"refresh_token": login.json()["refresh_token"]})
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["route"], "owner_idea")
        owner_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}", "X-Device-ID": "test-device"}
        owner_conversation = self.client.post("/api/conversations/new", headers=owner_headers, json={"agent_id": "idea_assistant"})
        self.assertEqual(owner_conversation.status_code, 200)
        self.assertEqual(owner_conversation.json()["agent_id"], "idea")

    def test_email_codes_cannot_be_reused(self):
        sent = self.client.post("/api/auth/email/send", json={"email": "member@example.com"})
        code = sent.json()["development_code"]
        first = self.client.post("/api/auth/email/verify", json={"email": "member@example.com", "code": code})
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/auth/email/verify", json={"email": "member@example.com", "code": code})
        self.assertEqual(second.status_code, 400)

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
            json={"category": "preference", "content": "Use Simplified Chinese by default."},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertIn("Simplified", updated.json()["content"])

        deleted = self.client.delete(f"/api/memories/{memory_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(any(item["id"] == memory_id for item in self.client.get("/api/memories", headers=self.headers).json()["memories"]))

    def test_member_cannot_use_owner_memory_scope(self):
        sent = self.client.post("/api/auth/email/send", json={"email": "memory-member@example.com"})
        login = self.client.post("/api/auth/email/verify", json={"email": "memory-member@example.com", "code": sent.json()["development_code"]})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        denied = self.client.post(
            "/api/memories",
            headers=headers,
            json={"scope": "owner", "category": "private", "content": "must not be written", "confirmed": True},
        )
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
