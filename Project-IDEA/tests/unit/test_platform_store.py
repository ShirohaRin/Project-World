"""PlatformStore 数据层单元测试。

直接针对真实 sqlite 数据层验证：Owner 引导、工具审批（指纹复用/决策/过期）、
能力授权（空间隔离/过期/撤销）、文件变更审查、审计、长期记忆（版本守卫/软删除）。
"""

import time

import pytest

from platform_auth import PlatformStore, Principal, RequestContext


class TestOwnerBootstrap:
    def test_ensure_owner_creates_bootstrap_owner(self, platform_store):
        platform_store.ensure_owner("bootstrap-owner-token")
        principal = platform_store.authenticate("bootstrap-owner-token", None)
        assert principal is not None
        assert principal.role == "owner"
        assert principal.account_id == "account-owner"

    def test_authenticate_rejects_unknown_token(self, platform_store):
        platform_store.ensure_owner("bootstrap-owner-token")
        assert platform_store.authenticate("no-such-token", None) is None


class TestToolApprovalStore:
    def test_pending_approval_is_found_and_reused_for_fingerprint(self, platform_store):
        platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "run_command", "fp-1", "run ls")
        found = platform_store.find_tool_approval("acct-1", "space-1", "p-1", "run_command", "fp-1")
        assert found is not None and found["status"] == "pending"

    def test_different_fingerprint_does_not_match(self, platform_store):
        platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "run_command", "fp-1", "run ls")
        assert platform_store.find_tool_approval("acct-1", "space-1", "p-1", "run_command", "fp-other") is None

    def test_approved_approval_is_returned_by_find(self, platform_store):
        approval = platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "run_command", "fp-1", "run ls")
        platform_store.decide_tool_approval(approval["approval_id"], "acct-1", "space-1", "approved", "owner")
        found = platform_store.find_tool_approval("acct-1", "space-1", "p-1", "run_command", "fp-1")
        assert found is not None and found["status"] == "approved"

    def test_denied_approval_is_not_reused(self, platform_store):
        approval = platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "delete_file", "fp-2", "del x")
        platform_store.decide_tool_approval(approval["approval_id"], "acct-1", "space-1", "denied", "owner")
        assert platform_store.find_tool_approval("acct-1", "space-1", "p-1", "delete_file", "fp-2") is None

    def test_expired_approval_is_not_found(self, platform_store):
        approval = platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "web_search", "fp-3", "search x")
        with platform_store._connect() as connection:
            connection.execute(
                "UPDATE tool_approvals SET expires_at = ? WHERE approval_id = ?",
                (time.time() - 1, approval["approval_id"]),
            )
        assert platform_store.find_tool_approval("acct-1", "space-1", "p-1", "web_search", "fp-3") is None

    def test_invalid_decision_raises(self, platform_store):
        approval = platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "run_command", "fp-4", "run")
        with pytest.raises(ValueError):
            platform_store.decide_tool_approval(approval["approval_id"], "acct-1", "space-1", "maybe", "owner")

    def test_list_approvals_filters_by_status(self, platform_store):
        approval = platform_store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "web_fetch", "fp-5", "fetch url")
        platform_store.decide_tool_approval(approval["approval_id"], "acct-1", "space-1", "approved", "owner")
        pending = platform_store.list_tool_approvals("acct-1", "space-1", status="pending")
        approved = platform_store.list_tool_approvals("acct-1", "space-1", status="approved")
        assert len(pending) == 0 and len(approved) == 1


class TestCapabilityGrantStore:
    def test_workspace_scoped_grant_matches_only_that_workspace(self, platform_store):
        platform_store.create_capability_grant("acct-1", "owner", "file.write", workspace="space-1")
        assert platform_store.find_valid_grant("acct-1", "file.write", "space-1") is not None
        assert platform_store.find_valid_grant("acct-1", "file.write", "space-2") is None

    def test_wildcard_grant_matches_any_workspace(self, platform_store):
        platform_store.create_capability_grant("acct-1", "owner", "command")
        assert platform_store.find_valid_grant("acct-1", "command", "space-9") is not None

    def test_expired_grant_is_not_valid(self, platform_store):
        grant = platform_store.create_capability_grant("acct-1", "owner", "network", expires_in_days=1)
        with platform_store._connect() as connection:
            connection.execute(
                "UPDATE capability_grants SET expires_at = ? WHERE grant_id = ?",
                (time.time() - 1, grant["grant_id"]),
            )
        assert platform_store.find_valid_grant("acct-1", "network") is None

    def test_revoked_grant_is_not_valid(self, platform_store):
        grant = platform_store.create_capability_grant("acct-1", "owner", "ssh")
        platform_store.revoke_capability_grant(grant["grant_id"], "owner")
        assert platform_store.find_valid_grant("acct-1", "ssh") is None

    def test_invalid_capability_raises(self, platform_store):
        with pytest.raises(ValueError):
            platform_store.create_capability_grant("acct-1", "owner", "not-a-capability")


class TestFileChangeReviewStore:
    def test_pending_review_created_and_listed(self, platform_store):
        created = platform_store.create_file_change_review("acct-1", "space-1", "p-1", "idea", "edit_file", "/tmp/a.txt", "/backup/a.txt", "+1 -1")
        assert created["status"] == "pending"
        pending = platform_store.list_file_change_reviews("acct-1", "space-1", status="pending")
        assert len(pending) == 1 and pending[0]["change_id"] == created["change_id"]

    def test_review_accepted_updates_status(self, platform_store):
        created = platform_store.create_file_change_review("acct-1", "space-1", "p-1", "idea", "write_file", "/tmp/a.txt", None, "added")
        reviewed = platform_store.review_file_change(created["change_id"], "acct-1", "space-1", "accepted", "owner")
        assert reviewed["status"] == "accepted"
        assert platform_store.list_file_change_reviews("acct-1", "space-1", status="pending") == []

    def test_review_reverted_updates_status(self, platform_store):
        created = platform_store.create_file_change_review("acct-1", "space-1", "p-1", "idea", "delete_file", "/tmp/a.txt", "/backup/a.txt", "-5")
        reviewed = platform_store.review_file_change(created["change_id"], "acct-1", "space-1", "reverted", "owner")
        assert reviewed["status"] == "reverted"

    def test_review_scoped_to_account_and_space(self, platform_store):
        created = platform_store.create_file_change_review("acct-1", "space-1", "p-1", "idea", "edit_file", "/tmp/a.txt", None, "+1")
        assert platform_store.get_file_change_review(created["change_id"], "acct-2", "space-1") is None

    def test_invalid_review_decision_raises(self, platform_store):
        created = platform_store.create_file_change_review("acct-1", "space-1", "p-1", "idea", "edit_file", "/tmp/a.txt", None, "+1")
        with pytest.raises(ValueError):
            platform_store.review_file_change(created["change_id"], "acct-1", "space-1", "maybe", "owner")


class TestConversationStore:
    def test_recent_message_snippets_groups_latest_per_conversation(self, platform_store):
        store = platform_store
        cid1 = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        cid2 = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        store.append_message("acct-1", "space-1", cid1, "user", "a1")
        store.append_message("acct-1", "space-1", cid1, "assistant", "a2")
        store.append_message("acct-1", "space-1", cid1, "user", "a3")
        store.append_message("acct-1", "space-1", cid2, "user", "b1")
        store.append_message("acct-1", "space-1", cid2, "assistant", "b2")
        store.append_message("acct-1", "space-1", cid2, "user", "b3")

        rows = store.recent_message_snippets("acct-1", "space-1", per_conversation=2)
        by_cid: dict[str, list[str]] = {}
        for row in rows:
            by_cid.setdefault(row["conversation_id"], []).append(row["content"])

        # 每会话最多 2 条且是最新的 2 条（最早的 a1/b1 被排除）
        assert sorted(by_cid[cid1]) == ["a2", "a3"]
        assert sorted(by_cid[cid2]) == ["b2", "b3"]
        # rn=1 标记该会话最新一条
        newest = {row["conversation_id"]: row for row in rows if row["rn"] == 1}
        assert newest[cid1]["content"] == "a3"
        assert newest[cid2]["content"] == "b3"

    def test_recent_message_snippets_scope_respected(self, platform_store):
        store = platform_store
        cid = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        store.append_message("acct-1", "space-1", cid, "user", "hello")
        assert store.recent_message_snippets("acct-2", "space-1") == []
        assert store.recent_message_snippets("acct-1", "space-2") == []

    def test_session_events_are_append_only_and_derive_messages(self, platform_store):
        store = platform_store
        cid = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        store.append_message("acct-1", "space-1", cid, "user", "q1", {"model_key": "gpt"})
        store.append_message("acct-1", "space-1", cid, "assistant", "a1")
        store.append_message("acct-1", "space-1", cid, "user", "q2")

        # 事件日志 append-only 且完整可回溯
        with store._connect() as connection:
            types = [row["event_type"] for row in connection.execute(
                "SELECT event_type FROM session_events WHERE conversation_id = ? ORDER BY event_id", (cid,)
            ).fetchall()]
        assert types == ["conversation.created", "user/message", "assistant/message", "user/message"]

        # 派生消息与写入一致（metadata 展开在顶层）
        messages = store.list_messages("acct-1", "space-1", cid)
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert [m["content"] for m in messages] == ["q1", "a1", "q2"]
        assert messages[0]["model_key"] == "gpt"

        # limit 取最近 N 条并按时间升序
        tail = store.list_messages("acct-1", "space-1", cid, limit=2)
        assert [m["content"] for m in tail] == ["a1", "q2"]

        # 会话计数走事件日志
        assert store.list_conversations("acct-1", "space-1")[0]["message_count"] == 3

    def test_legacy_messages_are_migrated_into_events(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        store1 = PlatformStore(str(db_path))
        cid = store1.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        # 模拟旧版本遗留数据：直接写 conversation_messages 表
        with store1._connect() as connection:
            connection.execute("INSERT INTO conversation_messages(message_id, conversation_id, role, content, metadata_json, created_at) VALUES ('m-1', ?, 'user', 'legacy q', '{}', 1000.0)", (cid,))
            connection.execute("INSERT INTO conversation_messages(message_id, conversation_id, role, content, metadata_json, created_at) VALUES ('m-2', ?, 'assistant', 'legacy a', '{}', 1001.0)", (cid,))
        # 重新打开 → 自动迁移进事件日志
        store2 = PlatformStore(str(db_path))
        messages = store2.list_messages("acct-1", "space-1", cid)
        assert [m["content"] for m in messages] == ["legacy q", "legacy a"]
        with store2._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM session_events WHERE conversation_id = ?", (cid,)).fetchone()[0]
        assert count == 3  # conversation.created + 2 条迁移消息

    def test_reset_and_delete_append_events(self, platform_store):
        store = platform_store
        cid = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        store.append_message("acct-1", "space-1", cid, "user", "hi")
        store.reset_conversation("acct-1", "space-1", cid)
        with store._connect() as connection:
            last = connection.execute("SELECT event_type FROM session_events WHERE conversation_id = ? ORDER BY event_id DESC LIMIT 1", (cid,)).fetchone()["event_type"]
        assert last == "conversation.reset"
        # reset 后不可再读（get_conversation 要求 active）
        with pytest.raises(LookupError):
            store.list_messages("acct-1", "space-1", cid)

        cid2 = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        store.delete_conversation("acct-1", "space-1", cid2)
        with store._connect() as connection:
            last = connection.execute("SELECT event_type FROM session_events WHERE conversation_id = ? ORDER BY event_id DESC LIMIT 1", (cid2,)).fetchone()["event_type"]
        assert last == "conversation.deleted"


class TestAgentRunStore:
    def test_run_lifecycle_records_safe_snapshot_and_sync_events(self, platform_store):
        store = platform_store
        conversation_id = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        snapshot = store.create_runtime_snapshot(
            "acct-1",
            "space-1",
            conversation_id,
            {
                "agent_id": "idea",
                "model_key": "gpt",
                "prompt_version": "idea.v1",
                "prompt_hash": "a" * 64,
                "history_message_count": 3,
                "context_block_count": 1,
                "context_block_tokens": 20,
                "memory_count": 1,
                "memory_tokens": 10,
                "secret": "must not be stored",
                "context_blocks": [{"content": "private file content"}],
            },
        )
        assert "secret" not in snapshot["payload"]
        assert "context_blocks" not in snapshot["payload"]
        assert snapshot["payload"]["prompt_version"] == "idea.v1"
        assert snapshot["payload"]["prompt_hash"] == "a" * 64

        run = store.create_agent_run(
            "acct-1",
            "space-1",
            conversation_id,
            "idea",
            snapshot["id"],
            "gpt",
            prompt_version="idea.v1",
            prompt_hash="a" * 64,
        )
        assert run["prompt_version"] == "idea.v1"
        assert run["prompt_hash"] == "a" * 64
        completed = store.complete_agent_run("acct-1", "space-1", run["id"], "Done.", 2, [{"name": "read_file", "success": True, "args": {"path": "private"}}])
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["tool_calls"] == [{"name": "read_file", "success": True}]
        assert completed["summary"] == "Done."

        events = store.list_sync_events("acct-1", "space-1", 0, 100)
        assert [event["event_type"] for event in events if event["aggregate_id"] == run["id"]] == ["agent_run.created", "agent_run.completed"]
        assert [event["type"] for event in store.list_agent_run_events("acct-1", "space-1", run["id"])] == ["run.started", "tools.completed", "run.completed"]

    def test_failed_run_and_scope_isolation(self, platform_store):
        store = platform_store
        conversation_id = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        snapshot = store.create_runtime_snapshot("acct-1", "space-1", conversation_id, {})
        run = store.create_agent_run("acct-1", "space-1", conversation_id, "idea", snapshot["id"], "gpt")
        failed = store.fail_agent_run("acct-1", "space-1", run["id"], "ProviderError\nsecret details")
        assert failed is not None and failed["status"] == "failed"
        assert failed["error"] == "ProviderError secret details"
        assert store.get_agent_run("acct-2", "space-1", run["id"]) is None
        assert store.list_agent_runs("acct-2", "space-1") == []

    def test_runtime_snapshot_counts_active_runs_tasks_and_approvals(self, platform_store):
        store = platform_store
        conversation_id = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        snapshot = store.create_runtime_snapshot("acct-1", "space-1", conversation_id, {})
        store.create_agent_run("acct-1", "space-1", conversation_id, "idea", snapshot["id"], "gpt")
        store.create_task("acct-1", "space-1", "idea", "Continue", "", conversation_id)
        store.create_tool_approval("acct-1", "space-1", "p-1", "idea", "run_command", "run-fingerprint", "run")
        runtime = store.runtime_snapshot("acct-1", "space-1")
        assert runtime["task_counts"] == {"active": 1, "pending": 1}
        assert runtime["pending_approvals"] == 1


class TestAgentAndRuntimeRegistry:
    def test_registry_omits_disabled_agents(self, platform_store):
        store = platform_store
        with store._connect() as connection:
            connection.execute("UPDATE agent_registry SET status = 'disabled' WHERE agent_id = 'pwa'")
        assert all(agent["agent_id"] != "pwa" for agent in store.list_agent_registry())

    def test_registry_returns_latest_active_version(self, platform_store):
        store = platform_store
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO agent_registry(agent_id, version, display_name, model_policy_json, tool_policy_json, memory_scopes_json, delegation_policy_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("pwa", 2, "PWA v2", '["gpt"]', '["file.read"]', '["shared"]', '[]', time.time()),
            )
        agents = {agent["agent_id"]: agent for agent in store.list_agent_registry()}
        assert agents["pwa"]["version"] == 2
        assert agents["pwa"]["models"] == ["gpt"]

    def test_runtime_registration_is_scoped_upsert_and_sanitized(self, platform_store):
        store = platform_store
        first = store.register_device_runtime(
            "acct-1", "space-1", "desktop-a", "desktop",
            {"workspace": True, "terminal": True, "secret": "never persisted"},
        )
        second = store.register_device_runtime(
            "acct-1", "space-1", "desktop-a", "desktop",
            {"browser": True, "plugins": True},
        )
        assert first["id"] == second["id"]
        assert second["capabilities"] == {
            "workspace": False, "terminal": False, "local_models": False,
            "gpu": False, "browser": True, "computer": False,
            "mcp": False, "plugins": True,
        }
        assert store.list_device_runtimes("acct-2", "space-1") == []

    def test_runtime_heartbeat_requires_existing_runtime_and_marks_it_online(self, platform_store):
        store = platform_store
        assert store.heartbeat_device_runtime("acct-1", "space-1", "desktop-a", "desktop") is None
        created = store.register_device_runtime("acct-1", "space-1", "desktop-a", "desktop", {})
        with store._connect() as connection:
            connection.execute("UPDATE device_runtimes SET status = 'offline', last_seen_at = 0 WHERE runtime_id = ?", (created["id"],))
        heartbeat = store.heartbeat_device_runtime("acct-1", "space-1", "desktop-a", "desktop")
        assert heartbeat is not None and heartbeat["status"] == "online"
        assert store.runtime_snapshot("acct-1", "space-1")["device_runtimes"][0]["id"] == created["id"]

    def test_handoff_lifecycle_enforces_ownership_and_state(self, platform_store):
        store = platform_store
        conversation_id = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        snapshot = store.create_runtime_snapshot("acct-1", "space-1", conversation_id, {})
        target = store.register_device_runtime("acct-1", "space-1", "desktop-a", "desktop", {})
        handoff = store.create_task_handoff(
            "acct-1", "space-1", conversation_id, "idea", snapshot["id"],
            "cloud_to_local", target_runtime_id=target["id"],
        )
        assert handoff["status"] == "pending"
        with pytest.raises(ValueError):
            store.transition_task_handoff("acct-1", "space-1", handoff["id"], "accepted", "other-runtime", "desktop-a")
        with pytest.raises(ValueError):
            store.transition_task_handoff("acct-1", "space-1", handoff["id"], "accepted", target["id"])
        accepted = store.transition_task_handoff("acct-1", "space-1", handoff["id"], "accepted", target["id"], "desktop-a")
        assert accepted is not None and accepted["status"] == "accepted"
        running = store.transition_task_handoff("acct-1", "space-1", handoff["id"], "running", target["id"], "desktop-a")
        assert running is not None and running["status"] == "running"
        completed = store.transition_task_handoff("acct-1", "space-1", handoff["id"], "completed", target["id"], "desktop-a")
        assert completed is not None and completed["status"] == "completed"
        assert store.get_task_handoff("acct-2", "space-1", handoff["id"]) is None

    def test_stale_pending_and_accepted_handoffs_are_cancelled_but_running_is_preserved(self, platform_store):
        store = platform_store
        conversation_id = store.create_conversation("acct-1", "space-1", "idea")["conversation_id"]
        target = store.register_device_runtime("acct-1", "space-1", "desktop-a", "desktop", {})
        now = time.time()
        handoffs = []
        for status in ("pending", "accepted", "running"):
            snapshot = store.create_runtime_snapshot("acct-1", "space-1", conversation_id, {})
            handoff = store.create_task_handoff("acct-1", "space-1", conversation_id, "idea", snapshot["id"], "cloud_to_local", target_runtime_id=target["id"])
            if status != "pending":
                store.transition_task_handoff("acct-1", "space-1", handoff["id"], "accepted", target["id"], "desktop-a")
            if status == "running":
                store.transition_task_handoff("acct-1", "space-1", handoff["id"], "running", target["id"], "desktop-a")
            handoffs.append(handoff["id"])
        with store._connect() as connection:
            connection.execute("UPDATE task_handoffs SET created_at = ?, accepted_at = ?, started_at = ?", (now - 3600, now - 3600, now - 3600))
        assert store.expire_stale_task_handoffs("acct-1", "space-1", now) == 2
        assert store.get_task_handoff("acct-1", "space-1", handoffs[0])["error"] == "handoff_expired_pending"
        assert store.get_task_handoff("acct-1", "space-1", handoffs[1])["error"] == "handoff_expired_accepted"
        assert store.get_task_handoff("acct-1", "space-1", handoffs[2])["status"] == "running"


class TestScheduledJobs:
    def test_job_lifecycle_create_list_due_update_delete(self, platform_store):
        store = platform_store
        job = store.create_scheduled_job("acct-1", "space-1", "idea", "list_dir", {"path": "."}, 60)
        assert job["status"] == "active"
        assert store.list_scheduled_jobs("acct-1", "space-1")[0]["job_id"] == job["id"]
        # 未到期不在 due 列表
        assert store.due_scheduled_jobs(now=time.time() - 1000) == []
        # 到期后出现
        due = store.due_scheduled_jobs(now=time.time() + 120)
        assert len(due) == 1 and due[0]["job_id"] == job["id"]
        # 回写执行结果并推进下次运行
        store.update_scheduled_job_run(job["id"], "success", "ok", time.time() + 120)
        refreshed = store.list_scheduled_jobs("acct-1", "space-1")[0]
        assert refreshed["last_status"] == "success" and refreshed["last_output"] == "ok"
        assert store.delete_scheduled_job("acct-1", "space-1", job["id"])
        assert store.list_scheduled_jobs("acct-1", "space-1") == []

    def test_interval_must_be_at_least_30_seconds(self, platform_store):
        with pytest.raises(ValueError):
            platform_store.create_scheduled_job("acct-1", "space-1", "idea", "list_dir", {}, 10)

    def test_job_scoped_to_account_and_space(self, platform_store):
        store = platform_store
        store.create_scheduled_job("acct-1", "space-1", "idea", "list_dir", {}, 60)
        assert store.list_scheduled_jobs("acct-2", "space-1") == []


class TestJobSchedulerExec:
    def test_scheduler_runs_due_job_and_writes_result(self, platform_store, tmp_path):
        import asyncio

        from jobs import JobScheduler
        from tool_runtime.registry import ToolRegistry

        registry = ToolRegistry(workspace=str(tmp_path), allowed_dirs=[str(tmp_path)], audit_store=platform_store)
        store = platform_store
        store.create_scheduled_job("acct-1", "space-1", "idea", "list_dir", {"path": "."}, 60)
        # 把作业改为立即到期，走真实 due 扫描路径
        with store._connect() as connection:
            connection.execute("UPDATE scheduled_jobs SET next_run_at = 0 WHERE account_id = 'acct-1'")
        due = store.due_scheduled_jobs(now=time.time())
        assert len(due) == 1 and due[0]["tool_name"] == "list_dir"

        scheduler = JobScheduler(store, registry)
        asyncio.run(scheduler._run_job(due[0]))

        refreshed = store.list_scheduled_jobs("acct-1", "space-1")[0]
        assert refreshed["last_status"] == "success"
        assert refreshed["next_run_at"] > 0


class TestAuditStore:
    def test_audit_write_and_list_with_context(self, platform_store):
        context = RequestContext("req-1", Principal("p-1", "acct-1", "member", "t-1"), "dev-1", "space-1")
        platform_store.write_audit("tool_policy", context, decision="deny", reason_code="unknown_tool", tool_name="hack")
        platform_store.write_audit("tool_policy", context, decision="allow")
        events = platform_store.list_audit("acct-1")
        assert len(events) == 2
        assert {event["decision"] for event in events} == {"deny", "allow"}
        assert all(event["principal_id"] == "p-1" for event in events)

    def test_audit_is_scoped_to_account(self, platform_store):
        platform_store.write_audit("tool_policy", account_id="acct-1", decision="deny")
        assert len(platform_store.list_audit("acct-2")) == 0


class TestMemoryStore:
    def test_memory_create_list_and_revision_guard(self, platform_store):
        store = platform_store
        created = store.create_memory("acct-1", "space-1", "personal", "note", "hello", "p-1")
        assert created["revision"] == 1
        assert len(store.list_memories("acct-1", "space-1", ["personal"])) == 1

        # 期望版本号错误 → 冲突，返回当前实际版本
        updated, error = store.update_memory("acct-1", "space-1", created["id"], ["personal"], "note", "hello world", expected_revision=2, principal_id="p-1")
        assert updated is None and error == 1

        # 正确版本号 → 更新成功，版本 +1
        updated, error = store.update_memory("acct-1", "space-1", created["id"], ["personal"], "note", "hello world", expected_revision=1, principal_id="p-1")
        assert updated is not None and updated["revision"] == 2 and error is None

    def test_memory_soft_delete(self, platform_store):
        store = platform_store
        created = store.create_memory("acct-1", "space-1", "personal", "note", "temp", "p-1")
        ok, error = store.delete_memory("acct-1", "space-1", created["id"], ["personal"], expected_revision=1, principal_id="p-1")
        assert ok is True and error is None
        assert store.list_memories("acct-1", "space-1", ["personal"]) == []

    def test_memory_query_filter(self, platform_store):
        store = platform_store
        store.create_memory("acct-1", "space-1", "personal", "note", "分子生物学笔记", "p-1")
        store.create_memory("acct-1", "space-1", "personal", "note", "买菜清单", "p-1")
        hits = store.list_memories("acct-1", "space-1", ["personal"], query="分子")
        assert len(hits) == 1 and "分子" in hits[0]["content"]
