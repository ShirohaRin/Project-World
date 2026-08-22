import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    principal_id: str
    account_id: str
    role: str
    token_id: str
    session_id: Optional[str] = None
    device_id: Optional[str] = None
    owner_device_id: Optional[str] = None
    owner_device_status: Optional[str] = None
    mcp_credential_id: Optional[str] = None


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    principal: Principal
    device_id: Optional[str] = None
    space_id: Optional[str] = None


RUNTIME_OFFLINE_TIMEOUT_SECONDS = 90
HANDOFF_PENDING_TIMEOUT_SECONDS = 15 * 60
HANDOFF_ACCEPTED_TIMEOUT_SECONDS = 5 * 60


class PlatformStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        recovery_key = os.environ.get("IDEA_CREDENTIAL_RECOVERY_KEY", "").encode("ascii")
        try:
            self.credential_cipher = Fernet(recovery_key) if recovery_key else None
        except (ValueError, TypeError) as error:
            raise RuntimeError("IDEA_CREDENTIAL_RECOVERY_KEY 无效") from error
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _encrypt_recoverable_credential(self, token: str) -> str | None:
        return self.credential_cipher.encrypt(token.encode("utf-8")).decode("ascii") if self.credential_cipher else None

    def _decrypt_recoverable_credential(self, encrypted_token: str | None) -> str | None:
        if not encrypted_token or not self.credential_cipher:
            return None
        try:
            return self.credential_cipher.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            return None

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self):
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS principals (
                    principal_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS access_tokens (
                    token_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at REAL,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    FOREIGN KEY(principal_id) REFERENCES principals(principal_id)
                );
                CREATE TABLE IF NOT EXISTS spaces (
                    space_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    space_type TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS space_members (
                    space_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(space_id, principal_id),
                    FOREIGN KEY(space_id) REFERENCES spaces(space_id),
                    FOREIGN KEY(principal_id) REFERENCES principals(principal_id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    request_id TEXT,
                    principal_id TEXT,
                    account_id TEXT,
                    device_id TEXT,
                    space_id TEXT,
                    resource_type TEXT,
                    resource_id TEXT,
                    action TEXT,
                    decision TEXT,
                    reason_code TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_occurred_at ON audit_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_events(account_id, occurred_at);
                CREATE TABLE IF NOT EXISTS email_verification_codes (
                    verification_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at REAL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_verification_lookup ON email_verification_codes(email, purpose, created_at DESC);
                CREATE TABLE IF NOT EXISTS password_credentials (
                    account_id TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    is_seeded INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS auth_login_attempts (
                    email TEXT NOT NULL,
                    attempted_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_email_time ON auth_login_attempts(email, attempted_at);
                CREATE TABLE IF NOT EXISTS account_sessions (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    device_id TEXT,
                    refresh_token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    revoked_at REAL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(principal_id) REFERENCES principals(principal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_account ON account_sessions(account_id, status);
                CREATE TABLE IF NOT EXISTS owner_principals (
                    owner_principal_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owner_account_links (
                    owner_principal_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    linked_at REAL NOT NULL,
                    revoked_at REAL,
                    PRIMARY KEY(owner_principal_id, account_id),
                    FOREIGN KEY(owner_principal_id) REFERENCES owner_principals(owner_principal_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS owner_devices (
                    owner_device_id TEXT PRIMARY KEY,
                    owner_principal_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at REAL NOT NULL,
                    approved_at REAL,
                    approved_by_principal_id TEXT,
                    revoked_at REAL,
                    revoked_by_principal_id TEXT,
                    last_seen_at REAL,
                    UNIQUE(owner_principal_id, account_id, device_id)
                );
                CREATE INDEX IF NOT EXISTS idx_owner_devices_lookup ON owner_devices(owner_principal_id, account_id, device_id, status);
                CREATE TABLE IF NOT EXISTS mcp_device_credentials (
                    credential_id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL UNIQUE,
                    encrypted_token TEXT,
                    capability TEXT NOT NULL DEFAULT 'memory',
                    credential_kind TEXT NOT NULL DEFAULT 'mcp',
                    owner_principal_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    device_label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at REAL,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    revoked_at REAL,
                    revoked_by_principal_id TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                    FOREIGN KEY(principal_id) REFERENCES principals(principal_id),
                    FOREIGN KEY(space_id) REFERENCES spaces(space_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_credentials_lookup ON mcp_device_credentials(secret_hash, status);
                CREATE INDEX IF NOT EXISTS idx_mcp_credentials_owner ON mcp_device_credentials(owner_principal_id, status);
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_scope ON conversations(account_id, space_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON conversation_messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    conversation_id TEXT,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_scope ON tasks(account_id, space_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS agent_registry (
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    model_policy_json TEXT NOT NULL,
                    tool_policy_json TEXT NOT NULL,
                    memory_scopes_json TEXT NOT NULL,
                    delegation_policy_json TEXT NOT NULL,
                    prompt_version TEXT NOT NULL DEFAULT 'idea.v1',
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
                    created_at REAL NOT NULL,
                    PRIMARY KEY(agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS device_runtimes (
                    runtime_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    runtime_kind TEXT NOT NULL CHECK(runtime_kind IN ('desktop', 'owner_desktop', 'cloud')),
                    capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('online', 'offline')),
                    registered_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    UNIQUE(account_id, space_id, device_id, runtime_kind)
                );
                CREATE INDEX IF NOT EXISTS idx_device_runtimes_scope ON device_runtimes(account_id, space_id, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS task_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    task_id TEXT,
                    conversation_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    source_runtime_id TEXT,
                    target_runtime_id TEXT,
                    direction TEXT NOT NULL CHECK(direction IN ('local_to_cloud', 'cloud_to_local')),
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'running', 'completed', 'failed', 'cancelled')),
                    created_at REAL NOT NULL,
                    accepted_at REAL,
                    started_at REAL,
                    finished_at REAL,
                    error_message TEXT,
                    execution_manifest_json TEXT,
                    manifest_hash TEXT,
                    accepted_by_device_id TEXT,
                    approval_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_task_handoffs_scope ON task_handoffs(account_id, space_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS runtime_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_snapshots_scope ON runtime_snapshots(account_id, space_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    task_id TEXT,
                    agent_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
                    model_key TEXT NOT NULL,
                    prompt_version TEXT,
                    prompt_hash TEXT,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    iterations INTEGER,
                    tool_calls_json TEXT NOT NULL DEFAULT '[]',
                    summary TEXT,
                    error_message TEXT,
                    FOREIGN KEY(snapshot_id) REFERENCES runtime_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_scope ON agent_runs(account_id, space_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation ON agent_runs(conversation_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, created_at);
                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sync_events_scope ON sync_events(account_id, space_id, event_id);
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    memory_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_scope ON long_term_memories(account_id, space_id, namespace, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS account_profiles (
                    account_id TEXT PRIMARY KEY,
                    work_role TEXT NOT NULL DEFAULT 'user' CHECK(work_role IN ('owner', 'researcher', 'novelist', 'user')),
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    project_type TEXT NOT NULL CHECK(project_type IN ('research', 'novel', 'general')),
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(created_by) REFERENCES principals(principal_id)
                );
                CREATE TABLE IF NOT EXISTS project_members (
                    project_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    permissions_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(project_id, principal_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(principal_id) REFERENCES principals(principal_id)
                );
                CREATE TABLE IF NOT EXISTS tool_approvals (
                    approval_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'idea',
                    tool_name TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    args_summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'denied', 'expired')),
                    requested_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    decided_at REAL,
                    decided_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tool_approvals_lookup ON tool_approvals(status, space_id, requested_at);
                CREATE TABLE IF NOT EXISTS capability_grants (
                    grant_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    granted_by TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    workspace TEXT NOT NULL DEFAULT '',
                    constraints_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked')),
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL,
                    revoked_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_capability_grants_lookup ON capability_grants(account_id, capability, status);
                CREATE TABLE IF NOT EXISTS file_change_reviews (
                    change_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'idea',
                    tool_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    backup_path TEXT,
                    diff_summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'reverted')),
                    created_at REAL NOT NULL,
                    reviewed_at REAL,
                    reviewed_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_file_change_reviews_lookup ON file_change_reviews(account_id, space_id, status, created_at DESC);
                -- 会话事件溯源：append-only 事件日志是会话的唯一事实源，消息列表由事件派生。
                CREATE TABLE IF NOT EXISTS session_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_session_events_conversation ON session_events(conversation_id, event_id);
                -- 后台定时作业（jobs/schedule）：Owner 编排、scheduler 扫描到期执行。
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL DEFAULT '{}',
                    interval_seconds REAL NOT NULL,
                    next_run_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_status TEXT,
                    last_output TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due ON scheduled_jobs(status, next_run_at);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)").fetchall()}
            if "email" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email) WHERE email IS NOT NULL")
            memory_columns = {row["name"] for row in connection.execute("PRAGMA table_info(long_term_memories)").fetchall()}
            if "revision" not in memory_columns:
                connection.execute("ALTER TABLE long_term_memories ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            connection.execute("UPDATE long_term_memories SET namespace = 'shared/' || space_id WHERE namespace = 'space/' || space_id")
            session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(account_sessions)").fetchall()}
            if "owner_device_id" not in session_columns:
                connection.execute("ALTER TABLE account_sessions ADD COLUMN owner_device_id TEXT")
            token_columns = {row["name"] for row in connection.execute("PRAGMA table_info(access_tokens)").fetchall()}
            if "session_id" not in token_columns:
                connection.execute("ALTER TABLE access_tokens ADD COLUMN session_id TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_access_tokens_session ON access_tokens(session_id, status)")
            credential_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_device_credentials)").fetchall()}
            if "capability" not in credential_columns:
                connection.execute("ALTER TABLE mcp_device_credentials ADD COLUMN capability TEXT NOT NULL DEFAULT 'memory'")
            conversation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)").fetchall()}
            if "approval_policy" not in conversation_columns:
                connection.execute("ALTER TABLE conversations ADD COLUMN approval_policy TEXT NOT NULL DEFAULT 'ask'")
            if "credential_kind" not in credential_columns:
                connection.execute("ALTER TABLE mcp_device_credentials ADD COLUMN credential_kind TEXT NOT NULL DEFAULT 'mcp'")
            if "encrypted_token" not in credential_columns:
                connection.execute("ALTER TABLE mcp_device_credentials ADD COLUMN encrypted_token TEXT")
            handoff_columns = {row["name"] for row in connection.execute("PRAGMA table_info(task_handoffs)").fetchall()}
            if "execution_manifest_json" not in handoff_columns:
                connection.execute("ALTER TABLE task_handoffs ADD COLUMN execution_manifest_json TEXT")
            if "manifest_hash" not in handoff_columns:
                connection.execute("ALTER TABLE task_handoffs ADD COLUMN manifest_hash TEXT")
            if "accepted_by_device_id" not in handoff_columns:
                connection.execute("ALTER TABLE task_handoffs ADD COLUMN accepted_by_device_id TEXT")
            if "approval_id" not in handoff_columns:
                connection.execute("ALTER TABLE task_handoffs ADD COLUMN approval_id TEXT")
            agent_registry_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_registry)").fetchall()}
            if "prompt_version" not in agent_registry_columns:
                connection.execute("ALTER TABLE agent_registry ADD COLUMN prompt_version TEXT NOT NULL DEFAULT 'idea.v1'")
            agent_run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()}
            if "prompt_version" not in agent_run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN prompt_version TEXT")
            if "prompt_hash" not in agent_run_columns:
                connection.execute("ALTER TABLE agent_runs ADD COLUMN prompt_hash TEXT")
            linked_devices = connection.execute("SELECT l.owner_principal_id, s.account_id, s.device_id FROM owner_account_links l JOIN account_sessions s ON s.account_id = l.account_id WHERE l.owner_principal_id = 'owner-shiroha-nao' AND l.status = 'active' AND s.device_id IS NOT NULL").fetchall()
            for device in linked_devices:
                exists = connection.execute("SELECT 1 FROM owner_devices WHERE owner_principal_id = ? AND account_id = ? AND device_id = ?", (device["owner_principal_id"], device["account_id"], device["device_id"])).fetchone()
                if not exists:
                    connection.execute("INSERT INTO owner_devices(owner_device_id, owner_principal_id, account_id, device_id, requested_at) VALUES (?, ?, ?, ?, ?)", (f"owner-device-{uuid.uuid4().hex}", device["owner_principal_id"], device["account_id"], device["device_id"], time.time()))
            owner_account = connection.execute("SELECT account_id FROM accounts WHERE account_id = 'account-owner'").fetchone()
            if owner_account:
                connection.execute("INSERT INTO owner_principals(owner_principal_id, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id) DO NOTHING", ("owner-shiroha-nao", "白羽奈绪", time.time()))
                connection.execute("INSERT INTO owner_account_links(owner_principal_id, account_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id, account_id) DO UPDATE SET status = 'active', revoked_at = NULL", ("owner-shiroha-nao", owner_account["account_id"], time.time()))
            now = time.time()
            connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) SELECT a.account_id, CASE WHEN a.account_id = 'account-owner' OR EXISTS(SELECT 1 FROM owner_account_links l WHERE l.owner_principal_id = 'owner-shiroha-nao' AND l.account_id = a.account_id AND l.status = 'active') THEN 'owner' ELSE 'user' END, ? FROM accounts a WHERE NOT EXISTS(SELECT 1 FROM account_profiles p WHERE p.account_id = a.account_id)", (now,))
            connection.execute("UPDATE account_profiles SET work_role = 'owner', updated_at = ? WHERE account_id = 'account-owner' OR EXISTS(SELECT 1 FROM owner_account_links l WHERE l.owner_principal_id = 'owner-shiroha-nao' AND l.account_id = account_profiles.account_id AND l.status = 'active')", (now,))
            self._seed_agent_registry(connection, now)
            self._migrate_messages_to_events(connection)

    @staticmethod
    def _seed_agent_registry(connection, now: float) -> None:
        agents = [
            ("idea", "IDEA", ["gpt", "deepseek-v4-flash"], ["file.read", "file.write", "command", "network", "delegate"], ["personal", "shared", "owner"], ["pwa", "researcher", "agent_producer"], "idea.v1"),
            ("pwa", "PWA", ["gpt", "deepseek-v4-flash"], ["file.read", "file.write", "command", "network"], ["personal", "shared"], [], "pwa.v1"),
            ("researcher", "Researcher", ["gpt", "deepseek-v4-flash"], ["file.read", "file.write", "command", "network"], ["personal", "shared"], [], "researcher.v1"),
            ("agent_producer", "AgentProducer", ["gpt", "deepseek-v4-flash"], ["file.read", "file.write", "command"], ["personal", "shared"], [], "agent_producer.v1"),
            ("idea_assistant", "IDEA Assistant", ["gpt", "deepseek-v4-flash"], ["file.read"], ["personal", "shared", "project"], [], "idea_assistant.v1"),
        ]
        for agent_id, display_name, models, tools, memory_scopes, delegates, prompt_version in agents:
            connection.execute(
                "INSERT INTO agent_registry(agent_id, version, display_name, model_policy_json, tool_policy_json, memory_scopes_json, delegation_policy_json, prompt_version, created_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(agent_id, version) DO UPDATE SET prompt_version = excluded.prompt_version",
                (agent_id, display_name, json.dumps(models), json.dumps(tools), json.dumps(memory_scopes), json.dumps(delegates), prompt_version, now),
            )

    @staticmethod
    def _append_session_event(connection, conversation_id: str, event_type: str, payload: dict, created_at: Optional[float] = None) -> None:
        """追加一条会话事件日志（append-only，event_id 自增即事件序号）。"""
        connection.execute(
            "INSERT INTO session_events(conversation_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, event_type, json.dumps(payload, ensure_ascii=False), created_at if created_at is not None else time.time()),
        )

    @staticmethod
    def _migrate_messages_to_events(connection) -> None:
        """一次性迁移：把历史 conversation_messages 复制为 session_events（逐条幂等）。

        按 message_id 判断是否已迁移，因此会话事件日志里已存在 conversation.created
        等事件时仍会补齐缺失的消息事件；重复执行不会产生重复事件。
        迁移后读写全部走事件日志，conversation_messages 表保留但不再写入。
        """
        rows = connection.execute("SELECT * FROM conversation_messages ORDER BY created_at").fetchall()
        for row in rows:
            migrated = connection.execute(
                "SELECT 1 FROM session_events WHERE json_extract(payload_json, '$.message_id') = ?",
                (row["message_id"],),
            ).fetchone()
            if migrated:
                continue
            payload = {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
            }
            connection.execute(
                "INSERT INTO session_events(conversation_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (row["conversation_id"], f"{row['role']}/message", json.dumps(payload, ensure_ascii=False), row["created_at"]),
            )

    def ensure_owner(self, bootstrap_token: str):
        if not bootstrap_token:
            return
        now = time.time()
        with self._connect() as connection:
            account = connection.execute("SELECT account_id FROM accounts LIMIT 1").fetchone()
            if account:
                owner = connection.execute("SELECT owner_principal_id FROM owner_principals WHERE owner_principal_id = ?", ("owner-shiroha-nao",)).fetchone()
                if not owner:
                    connection.execute("INSERT INTO owner_principals(owner_principal_id, display_name, created_at) VALUES (?, ?, ?)", ("owner-shiroha-nao", "白羽奈绪", now))
                bootstrap_account = connection.execute("SELECT account_id FROM principals WHERE principal_id = ?", ("principal-owner",)).fetchone()
                if bootstrap_account:
                    connection.execute(
                        "INSERT INTO owner_account_links(owner_principal_id, account_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id, account_id) DO UPDATE SET status = 'active', revoked_at = NULL",
                        ("owner-shiroha-nao", bootstrap_account["account_id"], now),
                    )
                    connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) VALUES (?, 'owner', ?) ON CONFLICT(account_id) DO UPDATE SET work_role = 'owner', updated_at = excluded.updated_at", (bootstrap_account["account_id"], now))
                connection.execute(
                    "UPDATE access_tokens SET token_hash = ?, status = 'active', expires_at = NULL WHERE token_id = ? AND principal_id = ?",
                    (self.hash_token(bootstrap_token), "token-owner", "principal-owner"),
                )
                return
            account_id = "account-owner"
            principal_id = "principal-owner"
            space_id = "space-project-world"
            connection.execute(
                "INSERT INTO accounts(account_id, name, created_at) VALUES (?, ?, ?)",
                (account_id, "Project World Owner", now),
            )
            connection.execute(
                "INSERT INTO principals(principal_id, account_id, role, display_name, created_at) VALUES (?, ?, ?, ?, ?)",
                (principal_id, account_id, "owner", "Project World Owner", now),
            )
            connection.execute(
                "INSERT INTO access_tokens(token_id, principal_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
                ("token-owner", principal_id, self.hash_token(bootstrap_token), now),
            )
            connection.execute(
                "INSERT INTO spaces(space_id, name, space_type, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (space_id, "Project World", "project", principal_id, now),
            )
            connection.execute(
                "INSERT INTO space_members(space_id, principal_id, role, created_at) VALUES (?, ?, ?, ?)",
                (space_id, principal_id, "owner", now),
            )
            connection.execute(
                "INSERT INTO owner_principals(owner_principal_id, display_name, created_at) VALUES (?, ?, ?)",
                ("owner-shiroha-nao", "白羽奈绪", now),
            )
            connection.execute(
                "INSERT INTO owner_account_links(owner_principal_id, account_id, linked_at) VALUES (?, ?, ?)",
                ("owner-shiroha-nao", account_id, now),
            )
            connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) VALUES (?, 'owner', ?)", (account_id, now))

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate(self, token: str, device_id: Optional[str]) -> Optional[Principal]:
        token_hash = self.hash_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.principal_id, p.account_id, p.role, t.token_id, t.session_id,
                       s.device_id, s.owner_device_id, od.status AS owner_device_status
                FROM access_tokens t
                JOIN principals p ON p.principal_id = t.principal_id
                JOIN accounts a ON a.account_id = p.account_id
                LEFT JOIN account_sessions s ON s.session_id = t.session_id
                LEFT JOIN owner_devices od ON od.owner_device_id = s.owner_device_id
                WHERE t.token_hash = ? AND t.status = 'active'
                  AND p.status = 'active' AND a.status = 'active'
                  AND (t.expires_at IS NULL OR t.expires_at > ?)
                  AND (t.session_id IS NULL OR (s.status = 'active' AND s.expires_at > ?))
                """,
                (token_hash, time.time(), time.time()),
            ).fetchone()
            if not row:
                return None
            if row["session_id"] and (not device_id or row["device_id"] != device_id):
                return None
            connection.execute("UPDATE access_tokens SET last_used_at = ? WHERE token_id = ?", (time.time(), row["token_id"]))
            if row["owner_device_id"]:
                connection.execute("UPDATE owner_devices SET last_seen_at = ? WHERE owner_device_id = ?", (time.time(), row["owner_device_id"]))
            return Principal(row["principal_id"], row["account_id"], row["role"], row["token_id"], row["session_id"], row["device_id"], row["owner_device_id"], row["owner_device_status"])

    def create_mcp_credential(self, principal: Principal, space_id: str, device_label: str, capability: str = "memory", expires_at: Optional[float] = None, credential_kind: str = "mcp") -> dict:
        if not self.is_owner_controller(principal):
            raise PermissionError("需要已批准的私有设备")
        if not isinstance(device_label, str) or not (1 <= len(device_label.strip()) <= 80):
            raise ValueError("device_label 必须为 1 到 80 个字符")
        if capability not in {"memory", "idea"}:
            raise ValueError("capability 必须为 memory 或 idea")
        if credential_kind not in {"mcp", "automated_device"}:
            raise ValueError("credential_kind 无效")
        if credential_kind == "automated_device" and not self.credential_cipher:
            raise ValueError("自动设备凭据托管尚未配置")
        owner_principal_id = self.owner_scope_id(principal)
        if not owner_principal_id or not self.resolve_space(principal.principal_id, space_id):
            raise PermissionError("无权访问指定空间")
        credential_id = f"mcp-{uuid.uuid4().hex}"
        secret = secrets.token_urlsafe(32)
        token = f"mcp_{credential_id}.{secret}"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO mcp_device_credentials(credential_id, secret_hash, encrypted_token, capability, credential_kind, owner_principal_id, account_id, principal_id, space_id, device_label, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (credential_id, self.hash_token(token), self._encrypt_recoverable_credential(token) if credential_kind == "automated_device" else None, capability, credential_kind, owner_principal_id, principal.account_id, principal.principal_id, space_id, device_label.strip(), expires_at, now),
            )
        return {"credential_id": credential_id, "capability": capability, "device_label": device_label.strip(), "space_id": space_id, "expires_at": expires_at, "token": token}

    def authenticate_mcp_credential(self, token: str, capability: str) -> tuple[Optional[Principal], Optional[str]]:
        if not token.startswith("mcp_"):
            return None, None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.credential_id, c.space_id, p.principal_id, p.account_id, p.role
                FROM mcp_device_credentials c
                JOIN principals p ON p.principal_id = c.principal_id
                JOIN accounts a ON a.account_id = c.account_id
                JOIN owner_principals o ON o.owner_principal_id = c.owner_principal_id
                JOIN space_members m ON m.space_id = c.space_id AND m.principal_id = c.principal_id
                WHERE c.secret_hash = ? AND c.capability = ? AND c.status = 'active'
                  AND (c.expires_at IS NULL OR c.expires_at > ?)
                  AND p.status = 'active' AND a.status = 'active' AND o.status = 'active'
                """,
                (self.hash_token(token), capability, time.time()),
            ).fetchone()
            if not row:
                return None, None
            connection.execute("UPDATE mcp_device_credentials SET last_used_at = ? WHERE credential_id = ?", (time.time(), row["credential_id"]))
            return Principal(row["principal_id"], row["account_id"], row["role"], row["credential_id"], owner_device_status="approved", mcp_credential_id=row["credential_id"]), row["space_id"]

    def authenticate_rag_owner_mcp_credential(self, token: str) -> tuple[Optional[Principal], Optional[str]]:
        if not token.startswith("mcp_"):
            return None, None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.credential_id, c.space_id, p.principal_id, p.account_id, p.role
                FROM mcp_device_credentials c
                JOIN principals p ON p.principal_id = c.principal_id
                JOIN accounts a ON a.account_id = c.account_id
                JOIN owner_principals o ON o.owner_principal_id = c.owner_principal_id
                JOIN owner_account_links l ON l.owner_principal_id = c.owner_principal_id AND l.account_id = c.account_id AND l.status = 'active'
                JOIN space_members m ON m.space_id = c.space_id AND m.principal_id = c.principal_id
                WHERE c.secret_hash = ? AND c.capability = 'idea' AND c.status = 'active'
                  AND (c.expires_at IS NULL OR c.expires_at > ?)
                  AND p.status = 'active' AND a.status = 'active' AND o.status = 'active'
                  AND EXISTS (
                      SELECT 1 FROM owner_devices d
                      WHERE d.owner_principal_id = c.owner_principal_id AND d.account_id = c.account_id AND d.status = 'approved'
                  )
                """,
                (self.hash_token(token), time.time()),
            ).fetchone()
            if not row:
                return None, None
            connection.execute("UPDATE mcp_device_credentials SET last_used_at = ? WHERE credential_id = ?", (time.time(), row["credential_id"]))
            return Principal(row["principal_id"], row["account_id"], row["role"], row["credential_id"], owner_device_status="approved", mcp_credential_id=row["credential_id"]), row["space_id"]

    def list_automated_device_credentials(self, principal: Principal) -> list[dict]:
        owner_principal_id = self.owner_scope_id(principal)
        if not owner_principal_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT credential_id, capability, device_label, space_id, status, expires_at, created_at, last_used_at, revoked_at FROM mcp_device_credentials WHERE owner_principal_id = ? AND credential_kind = 'automated_device' ORDER BY created_at DESC",
                (owner_principal_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_mcp_credentials(self, principal: Principal) -> list[dict]:
        owner_principal_id = self.owner_scope_id(principal)
        if not owner_principal_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT credential_id, capability, device_label, space_id, status, expires_at, created_at, last_used_at, revoked_at FROM mcp_device_credentials WHERE owner_principal_id = ? ORDER BY created_at DESC",
                (owner_principal_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def recover_automated_device_credential(self, principal: Principal, credential_id: str) -> Optional[str]:
        if not self.is_owner_controller(principal):
            return None
        owner_principal_id = self.owner_scope_id(principal)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT encrypted_token FROM mcp_device_credentials WHERE credential_id = ? AND owner_principal_id = ? AND credential_kind = 'automated_device' AND status = 'active'",
                (credential_id, owner_principal_id),
            ).fetchone()
        return self._decrypt_recoverable_credential(row["encrypted_token"] if row else None)

    def revoke_automated_device_credential(self, principal: Principal, credential_id: str) -> bool:
        if not self.is_owner_controller(principal):
            return False
        owner_principal_id = self.owner_scope_id(principal)
        with self._connect() as connection:
            return connection.execute(
                "UPDATE mcp_device_credentials SET status = 'revoked', revoked_at = ?, revoked_by_principal_id = ? WHERE credential_id = ? AND owner_principal_id = ? AND credential_kind = 'automated_device' AND status = 'active'",
                (time.time(), principal.principal_id, credential_id, owner_principal_id),
            ).rowcount == 1

    def revoke_mcp_credential(self, principal: Principal, credential_id: str) -> bool:
        if not self.is_owner_controller(principal):
            return False
        owner_principal_id = self.owner_scope_id(principal)
        with self._connect() as connection:
            return connection.execute(
                "UPDATE mcp_device_credentials SET status = 'revoked', revoked_at = ?, revoked_by_principal_id = ? WHERE credential_id = ? AND owner_principal_id = ? AND status = 'active'",
                (time.time(), principal.principal_id, credential_id, owner_principal_id),
            ).rowcount == 1

    def list_spaces(self, principal_id: str):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.space_id, s.name, s.space_type, m.role
                FROM spaces s JOIN space_members m ON m.space_id = s.space_id
                WHERE m.principal_id = ? ORDER BY s.created_at
                """,
                (principal_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def resolve_space(self, principal_id: str, requested_space_id: Optional[str]) -> Optional[str]:
        with self._connect() as connection:
            if requested_space_id:
                row = connection.execute(
                    "SELECT space_id FROM space_members WHERE space_id = ? AND principal_id = ?",
                    (requested_space_id, principal_id),
                ).fetchone()
                return row["space_id"] if row else None
            row = connection.execute(
                "SELECT space_id FROM space_members WHERE principal_id = ? ORDER BY created_at LIMIT 1",
                (principal_id,),
            ).fetchone()
            return row["space_id"] if row else None

    def is_owner(self, principal_id: str, space_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM space_members WHERE space_id = ? AND principal_id = ? AND role = 'owner'",
                (space_id, principal_id),
            ).fetchone()
            return row is not None

    def get_space_member_role(self, principal_id: str, space_id: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute("SELECT role FROM space_members WHERE space_id = ? AND principal_id = ?", (space_id, principal_id)).fetchone()
            return row["role"] if row else None

    def upsert_space_member(self, space_id: str, principal_id: str, role: str) -> None:
        if role not in {"owner", "editor", "viewer"}:
            raise ValueError("空间角色无效")
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM spaces WHERE space_id = ?", (space_id,)).fetchone():
                raise LookupError("空间不存在")
            if not connection.execute("SELECT 1 FROM principals WHERE principal_id = ?", (principal_id,)).fetchone():
                raise LookupError("成员不存在")
            connection.execute("INSERT INTO space_members(space_id, principal_id, role, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(space_id, principal_id) DO UPDATE SET role = excluded.role", (space_id, principal_id, role, time.time()))

    def memory_write_allowed(self, principal_id: str, space_id: str, namespace: str) -> bool:
        return not (namespace.startswith("shared/") or namespace.startswith("project/")) or self.get_space_member_role(principal_id, space_id) in {"owner", "editor"}

    def is_owner_account(self, account_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM owner_account_links l JOIN owner_principals o ON o.owner_principal_id = l.owner_principal_id WHERE l.owner_principal_id = ? AND l.account_id = ? AND l.status = 'active' AND o.status = 'active'",
                ("owner-shiroha-nao", account_id),
            ).fetchone()
            return row is not None

    def get_account_role(self, account_id: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute("SELECT work_role FROM account_profiles WHERE account_id = ?", (account_id,)).fetchone()
            return row["work_role"] if row else None

    def set_account_role(self, account_id: str, work_role: str) -> None:
        if work_role not in {"owner", "researcher", "novelist", "user"}:
            raise ValueError("工作角色无效")
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)).fetchone():
                raise LookupError("账户不存在")
            is_owner = connection.execute("SELECT 1 FROM owner_account_links l JOIN owner_principals o ON o.owner_principal_id = l.owner_principal_id WHERE l.owner_principal_id = ? AND l.account_id = ? AND l.status = 'active' AND o.status = 'active'", ("owner-shiroha-nao", account_id)).fetchone() is not None
            if work_role == "owner" and not is_owner:
                raise PermissionError("只有白羽奈绪 Owner 关联账户可以设为 owner")
            if is_owner and work_role != "owner":
                raise PermissionError("白羽奈绪 Owner 账户不允许降级")
            connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) VALUES (?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET work_role = excluded.work_role, updated_at = excluded.updated_at", (account_id, work_role, time.time()))

    def create_project(self, name: str, project_type: str, created_by: str, status: str = "active") -> dict:
        if project_type not in {"research", "novel", "general"} or not status:
            raise ValueError("项目字段无效")
        project_id, now = f"project-{uuid.uuid4().hex}", time.time()
        with self._connect() as connection:
            connection.execute("INSERT INTO projects(project_id, name, project_type, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", (project_id, name, project_type, status, created_by, now))
        return {"project_id": project_id, "name": name, "project_type": project_type, "status": status, "created_by": created_by, "created_at": now}

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()]

    def set_project_member(self, project_id: str, principal_id: str, permissions: list[str]) -> dict:
        allowed = {"documents.read", "documents.write", "documents.delete", "rag.search", "index.rebuild"}
        if not isinstance(permissions, list) or any(permission not in allowed for permission in permissions) or len(set(permissions)) != len(permissions):
            raise ValueError("项目权限无效")
        now = time.time()
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
                raise LookupError("项目不存在")
            if not connection.execute("SELECT 1 FROM principals WHERE principal_id = ?", (principal_id,)).fetchone():
                raise LookupError("成员不存在")
            connection.execute("INSERT INTO project_members(project_id, principal_id, permissions_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, principal_id) DO UPDATE SET permissions_json = excluded.permissions_json, updated_at = excluded.updated_at", (project_id, principal_id, json.dumps(permissions), now, now))
        return {"project_id": project_id, "principal_id": principal_id, "permissions": permissions, "updated_at": now}

    def list_project_members(self, project_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT project_id, principal_id, permissions_json, created_at, updated_at FROM project_members WHERE project_id = ? ORDER BY created_at", (project_id,)).fetchall()
            return [{**dict(row), "permissions": json.loads(row["permissions_json"])} for row in rows]

    def project_permission_allowed(self, project_id: str, principal_id: str, permission: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT permissions_json FROM project_members WHERE project_id = ? AND principal_id = ?", (project_id, principal_id)).fetchone()
            return bool(row and permission in json.loads(row["permissions_json"]))

    def write_audit(self, event_type: str, context: Optional[RequestContext] = None, **fields):
        metadata = fields.pop("metadata", {})
        values = {
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "occurred_at": time.time(),
            "request_id": context.request_id if context else fields.pop("request_id", None),
            "principal_id": context.principal.principal_id if context else fields.pop("principal_id", None),
            "account_id": context.principal.account_id if context else fields.pop("account_id", None),
            "device_id": context.device_id if context else fields.pop("device_id", None),
            "space_id": context.space_id if context else fields.pop("space_id", None),
            "resource_type": fields.pop("resource_type", None),
            "resource_id": fields.pop("resource_id", None),
            "action": fields.pop("action", None),
            "decision": fields.pop("decision", None),
            "reason_code": fields.pop("reason_code", None),
            "metadata_json": json.dumps({**metadata, **fields}, ensure_ascii=False)[:4000],
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_id, event_type, occurred_at, request_id, principal_id, account_id,
                    device_id, space_id, resource_type, resource_id, action, decision,
                    reason_code, metadata_json
                ) VALUES (:event_id, :event_type, :occurred_at, :request_id, :principal_id, :account_id,
                    :device_id, :space_id, :resource_type, :resource_id, :action, :decision,
                    :reason_code, :metadata_json)
                """,
                values,
            )

    def list_audit(self, account_id: str, limit: int = 100):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE account_id = ? ORDER BY occurred_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_conversation(self, account_id: str, space_id: str, agent_id: str, conversation_id: Optional[str] = None) -> dict:
        conversation_id = conversation_id or uuid.uuid4().hex
        now = time.time()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
            if row:
                if row["account_id"] != account_id or row["space_id"] != space_id:
                    raise LookupError("会话不存在")
                if row["agent_id"] != agent_id:
                    raise ValueError("该会话已属于另一智能体")
                return dict(row)
            connection.execute("INSERT INTO conversations(conversation_id, account_id, space_id, agent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, account_id, space_id, agent_id, now, now))
            self._append_session_event(connection, conversation_id, "conversation.created", {"agent_id": agent_id, "created_at": now}, created_at=now)
            self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "conversation.created", {"agent_id": agent_id, "created_at": now})
            return {"conversation_id": conversation_id, "account_id": account_id, "space_id": space_id, "agent_id": agent_id, "status": "active", "created_at": now, "updated_at": now}

    def get_conversation(self, account_id: str, space_id: str, conversation_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'", (conversation_id, account_id, space_id)).fetchone()
            return dict(row) if row else None

    def list_conversations(self, account_id: str, space_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM session_events e WHERE e.conversation_id = c.conversation_id AND e.event_type IN ('user/message', 'assistant/message')) AS message_count "
                "FROM conversations c WHERE c.account_id = ? AND c.space_id = ? AND c.status = 'active' ORDER BY c.updated_at DESC",
                (account_id, space_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_active_conversations(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM conversations WHERE status = 'active'").fetchone()[0]

    def conversation_approval_policy(self, account_id: str, space_id: str, conversation_id: str) -> str:
        """会话级审批策略：'ask'（默认，弹审批）或 'never'（任何审批一律拒绝，访客/无人值守场景）。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT approval_policy FROM conversations WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'",
                (conversation_id, account_id, space_id),
            ).fetchone()
        policy = row["approval_policy"] if row else "ask"
        return policy if policy in ("ask", "never") else "ask"

    def set_conversation_approval_policy(self, account_id: str, space_id: str, conversation_id: str, policy: str) -> bool:
        if policy not in ("ask", "never"):
            raise ValueError("审批策略无效")
        now = time.time()
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE conversations SET approval_policy = ?, updated_at = ? WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'",
                (policy, now, conversation_id, account_id, space_id),
            ).rowcount
            if changed:
                self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "approval.policy", {"policy": policy})
            return bool(changed)

    def list_messages(self, account_id: str, space_id: str, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        if not self.get_conversation(account_id, space_id, conversation_id):
            raise LookupError("会话不存在")
        message_types = "('user/message', 'assistant/message')"
        if limit:
            query = (
                "SELECT payload_json, created_at FROM ("
                f"SELECT payload_json, created_at, event_id FROM session_events WHERE conversation_id = ? AND event_type IN {message_types} ORDER BY event_id DESC LIMIT ?"
                ") ORDER BY event_id"
            )
            params: tuple = (conversation_id, limit)
        else:
            query = (
                f"SELECT payload_json, created_at FROM session_events WHERE conversation_id = ? AND event_type IN {message_types} ORDER BY event_id"
            )
            params = (conversation_id,)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        messages = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            messages.append({"id": payload["message_id"], "role": payload["role"], "content": payload["content"], "timestamp": row["created_at"], **payload.get("metadata", {})})
        return messages

    def recent_message_snippets(self, account_id: str, space_id: str, per_conversation: int = 2) -> list[dict]:
        """批量取每个活跃会话最近的若干条消息（单条窗口函数查询，避免 N+1）。

        返回行按会话分组（每会话最多 per_conversation 条，按事件序号降序，rn=1 为最新），
        并携带会话的 updated_at 供上层按活跃度排序。数据访问层一次连接完成全部读取。
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT conversation_id, updated_at, payload_json, rn FROM (
                    SELECT c.conversation_id, c.updated_at, e.payload_json,
                           ROW_NUMBER() OVER (PARTITION BY e.conversation_id ORDER BY e.event_id DESC) AS rn
                    FROM conversations c
                    JOIN session_events e ON e.conversation_id = c.conversation_id
                    WHERE c.account_id = ? AND c.space_id = ? AND c.status = 'active'
                      AND e.event_type IN ('user/message', 'assistant/message')
                ) WHERE rn <= ?
                """,
                (account_id, space_id, per_conversation),
            ).fetchall()
        return [
            {
                "conversation_id": row["conversation_id"],
                "updated_at": row["updated_at"],
                "content": json.loads(row["payload_json"]).get("content", ""),
                "rn": row["rn"],
            }
            for row in rows
        ]

    def append_message(self, account_id: str, space_id: str, conversation_id: str, role: str, content: str, metadata: Optional[dict] = None) -> dict:
        now = time.time()
        if not self.get_conversation(account_id, space_id, conversation_id):
            raise LookupError("会话不存在")
        message_id = uuid.uuid4().hex
        with self._connect() as connection:
            self._append_session_event(
                connection,
                conversation_id,
                f"{role}/message",
                {"message_id": message_id, "role": role, "content": content, "metadata": metadata or {}},
                created_at=now,
            )
            connection.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
            self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "message.appended", {"message_id": message_id, "role": role, "created_at": now})
        return {"id": message_id, "role": role, "content": content, "timestamp": now, **(metadata or {})}

    def reset_conversation(self, account_id: str, space_id: str, conversation_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute("UPDATE conversations SET status = 'reset', updated_at = ? WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'", (time.time(), conversation_id, account_id, space_id)).rowcount
            if changed:
                self._append_session_event(connection, conversation_id, "conversation.reset", {}, created_at=time.time())
                self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "conversation.reset", {})
            return bool(changed)

    def delete_conversation(self, account_id: str, space_id: str, conversation_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute("UPDATE conversations SET status = 'deleted', updated_at = ? WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'", (time.time(), conversation_id, account_id, space_id)).rowcount
            if changed:
                self._append_session_event(connection, conversation_id, "conversation.deleted", {}, created_at=time.time())
                connection.execute("UPDATE tasks SET status = 'deleted', updated_at = ? WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status != 'deleted'", (time.time(), conversation_id, account_id, space_id))
                self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "conversation.deleted", {})
            return bool(changed)

    def create_task(self, account_id: str, space_id: str, agent_id: str, title: str, description: str, conversation_id: Optional[str]) -> dict:
        task_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            connection.execute("INSERT INTO tasks(task_id, account_id, space_id, conversation_id, agent_id, title, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (task_id, account_id, space_id, conversation_id, agent_id, title, description, now, now))
            self._append_sync_event(connection, account_id, space_id, "task", task_id, "task.created", {"title": title, "status": "pending", "created_at": now})
        return {"id": task_id, "title": title, "description": description, "agent_id": agent_id, "conversation_id": conversation_id, "status": "pending", "account_id": account_id, "space_id": space_id, "created_at": now, "updated_at": now}

    def list_tasks(self, account_id: str, space_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks WHERE account_id = ? AND space_id = ? AND status != 'deleted' ORDER BY created_at DESC", (account_id, space_id)).fetchall()
            return [{"id": row["task_id"], **{key: row[key] for key in row.keys() if key != "task_id"}} for row in rows]

    def list_agent_registry(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agent_registry WHERE status = 'active' ORDER BY agent_id, version DESC").fetchall()
        latest: dict[str, dict] = {}
        for row in rows:
            if row["agent_id"] in latest:
                continue
            latest[row["agent_id"]] = {
                "agent_id": row["agent_id"], "version": row["version"], "display_name": row["display_name"],
                "models": json.loads(row["model_policy_json"]), "tools": json.loads(row["tool_policy_json"]),
                "memory_scopes": json.loads(row["memory_scopes_json"]), "delegates": json.loads(row["delegation_policy_json"]), "prompt_version": row["prompt_version"],
            }
        return list(latest.values())

    def register_device_runtime(self, account_id: str, space_id: str, device_id: str, runtime_kind: str, capabilities: dict) -> dict:
        if runtime_kind not in {"desktop", "owner_desktop", "cloud"}:
            raise ValueError("runtime_kind 无效")
        now = time.time()
        safe_capabilities = {
            "workspace": bool(capabilities.get("workspace", False)),
            "terminal": bool(capabilities.get("terminal", False)),
            "local_models": bool(capabilities.get("local_models", False)),
            "gpu": bool(capabilities.get("gpu", False)),
            "browser": bool(capabilities.get("browser", False)),
            "computer": bool(capabilities.get("computer", False)),
            "mcp": bool(capabilities.get("mcp", False)),
            "plugins": bool(capabilities.get("plugins", False)),
        }
        with self._connect() as connection:
            existing = connection.execute("SELECT runtime_id FROM device_runtimes WHERE account_id = ? AND space_id = ? AND device_id = ? AND runtime_kind = ?", (account_id, space_id, device_id, runtime_kind)).fetchone()
            runtime_id = existing["runtime_id"] if existing else uuid.uuid4().hex
            connection.execute(
                "INSERT INTO device_runtimes(runtime_id, account_id, space_id, device_id, runtime_kind, capabilities_json, status, registered_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 'online', ?, ?) ON CONFLICT(account_id, space_id, device_id, runtime_kind) DO UPDATE SET capabilities_json = excluded.capabilities_json, status = 'online', last_seen_at = excluded.last_seen_at",
                (runtime_id, account_id, space_id, device_id, runtime_kind, json.dumps(safe_capabilities), now, now),
            )
            self._append_sync_event(connection, account_id, space_id, "runtime", runtime_id, "runtime.registered", {"runtime_id": runtime_id, "kind": runtime_kind, "status": "online"})
        return {"id": runtime_id, "device_id": device_id, "kind": runtime_kind, "capabilities": safe_capabilities, "status": "online", "last_seen_at": now}

    def heartbeat_device_runtime(self, account_id: str, space_id: str, device_id: str, runtime_kind: str) -> Optional[dict]:
        if runtime_kind not in {"desktop", "owner_desktop", "cloud"}:
            raise ValueError("runtime_kind 无效")
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT runtime_id, capabilities_json, registered_at FROM device_runtimes WHERE account_id = ? AND space_id = ? AND device_id = ? AND runtime_kind = ?",
                (account_id, space_id, device_id, runtime_kind),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE device_runtimes SET status = 'online', last_seen_at = ? WHERE runtime_id = ?",
                (now, row["runtime_id"]),
            )
        return {
            "id": row["runtime_id"],
            "device_id": device_id,
            "kind": runtime_kind,
            "capabilities": json.loads(row["capabilities_json"]),
            "status": "online",
            "registered_at": row["registered_at"],
            "last_seen_at": now,
        }

    def list_device_runtimes(self, account_id: str, space_id: str) -> list[dict]:
        offline_before = time.time() - RUNTIME_OFFLINE_TIMEOUT_SECONDS
        with self._connect() as connection:
            connection.execute(
                "UPDATE device_runtimes SET status = 'offline' WHERE account_id = ? AND space_id = ? AND status = 'online' AND last_seen_at < ?",
                (account_id, space_id, offline_before),
            )
            rows = connection.execute("SELECT * FROM device_runtimes WHERE account_id = ? AND space_id = ? ORDER BY last_seen_at DESC", (account_id, space_id)).fetchall()
        return [{"id": row["runtime_id"], "device_id": row["device_id"], "kind": row["runtime_kind"], "capabilities": json.loads(row["capabilities_json"]), "status": row["status"], "registered_at": row["registered_at"], "last_seen_at": row["last_seen_at"]} for row in rows]

    def create_task_handoff(self, account_id: str, space_id: str, conversation_id: str, agent_id: str, snapshot_id: str, direction: str, task_id: Optional[str] = None, source_runtime_id: Optional[str] = None, target_runtime_id: Optional[str] = None, execution_manifest: Optional[dict] = None, approval_id: Optional[str] = None) -> dict:
        if direction not in {"local_to_cloud", "cloud_to_local"}:
            raise ValueError("handoff direction 无效")
        if direction == "cloud_to_local" and not target_runtime_id:
            raise ValueError("cloud_to_local 必须指定目标 Runtime")
        manifest_json = json.dumps(execution_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if execution_manifest else None
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest() if manifest_json else None
        handoff_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            snapshot = connection.execute("SELECT 1 FROM runtime_snapshots WHERE snapshot_id = ? AND account_id = ? AND space_id = ? AND conversation_id = ?", (snapshot_id, account_id, space_id, conversation_id)).fetchone()
            if not snapshot:
                raise LookupError("Runtime Snapshot 不存在")
            for runtime_id in (source_runtime_id, target_runtime_id):
                if runtime_id and not connection.execute("SELECT 1 FROM device_runtimes WHERE runtime_id = ? AND account_id = ? AND space_id = ?", (runtime_id, account_id, space_id)).fetchone():
                    raise LookupError("Runtime 不存在")
            if approval_id:
                approval = connection.execute("SELECT 1 FROM tool_approvals WHERE approval_id = ? AND account_id = ? AND space_id = ? AND tool_name = 'handoff.run_file' AND fingerprint = ? AND status = 'approved' AND expires_at > ?", (approval_id, account_id, space_id, manifest_hash, now)).fetchone()
                if not approval:
                    raise ValueError("交接执行审批不可用")
            connection.execute("INSERT INTO task_handoffs(handoff_id, account_id, space_id, task_id, conversation_id, agent_id, snapshot_id, source_runtime_id, target_runtime_id, direction, status, created_at, execution_manifest_json, manifest_hash, approval_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)", (handoff_id, account_id, space_id, task_id, conversation_id, agent_id, snapshot_id, source_runtime_id, target_runtime_id, direction, now, manifest_json, manifest_hash, approval_id))
            self._append_sync_event(connection, account_id, space_id, "task_handoff", handoff_id, "handoff.created", {"handoff_id": handoff_id, "status": "pending", "direction": direction, "conversation_id": conversation_id})
        return self.get_task_handoff(account_id, space_id, handoff_id)  # type: ignore[return-value]

    def transition_task_handoff(self, account_id: str, space_id: str, handoff_id: str, status: str, runtime_id: Optional[str] = None, device_id: Optional[str] = None, error_message: str = "") -> Optional[dict]:
        transitions = {"accepted": {"pending"}, "running": {"accepted"}, "completed": {"running"}, "failed": {"accepted", "running"}, "cancelled": {"pending", "accepted", "running"}}
        if status not in transitions:
            raise ValueError("handoff status 无效")
        now = time.time()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM task_handoffs WHERE handoff_id = ? AND account_id = ? AND space_id = ?", (handoff_id, account_id, space_id)).fetchone()
            if not row or row["status"] not in transitions[status]:
                return None
            if row["direction"] == "cloud_to_local":
                if not runtime_id or runtime_id != row["target_runtime_id"] or not device_id:
                    raise ValueError("本地交接必须由目标 Runtime 领取")
                if row["accepted_by_device_id"] and row["accepted_by_device_id"] != device_id:
                    raise ValueError("交接已由其他设备领取")
            elif runtime_id and runtime_id != row["target_runtime_id"]:
                raise ValueError("Runtime 无权接管此交接")
            fields, values = ["status = ?"], [status]
            if status == "accepted":
                fields.extend(["accepted_at = ?", "accepted_by_device_id = ?"])
                values.extend([now, device_id])
            if status == "running": fields.append("started_at = ?"); values.append(now)
            if status in {"completed", "failed", "cancelled"}: fields.append("finished_at = ?"); values.append(now)
            if status == "failed": fields.append("error_message = ?"); values.append(error_message.replace("\n", " ")[:500])
            values.extend([handoff_id, account_id, space_id, row["status"]])
            changed = connection.execute(f"UPDATE task_handoffs SET {', '.join(fields)} WHERE handoff_id = ? AND account_id = ? AND space_id = ? AND status = ?", values).rowcount
            if not changed:
                return None
            self._append_sync_event(connection, account_id, space_id, "task_handoff", handoff_id, f"handoff.{status}", {"handoff_id": handoff_id, "status": status, "finished_at": now if status in {"completed", "failed", "cancelled"} else None})
        return self.get_task_handoff(account_id, space_id, handoff_id)

    def get_task_handoff(self, account_id: str, space_id: str, handoff_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM task_handoffs WHERE handoff_id = ? AND account_id = ? AND space_id = ?", (handoff_id, account_id, space_id)).fetchone()
        return self._task_handoff_record(row) if row else None

    def list_task_handoffs(self, account_id: str, space_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM task_handoffs WHERE account_id = ? AND space_id = ? ORDER BY created_at DESC LIMIT ?", (account_id, space_id, limit)).fetchall()
        return [self._task_handoff_record(row) for row in rows]

    def expire_stale_task_handoffs(self, account_id: str, space_id: str, now: Optional[float] = None) -> int:
        now = now or time.time()
        cancelled = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT handoff_id, status FROM task_handoffs WHERE account_id = ? AND space_id = ? AND ((status = 'pending' AND created_at < ?) OR (status = 'accepted' AND accepted_at < ?))",
                (account_id, space_id, now - HANDOFF_PENDING_TIMEOUT_SECONDS, now - HANDOFF_ACCEPTED_TIMEOUT_SECONDS),
            ).fetchall()
            for row in rows:
                reason = "handoff_expired_pending" if row["status"] == "pending" else "handoff_expired_accepted"
                changed = connection.execute(
                    "UPDATE task_handoffs SET status = 'cancelled', finished_at = ?, error_message = ? WHERE handoff_id = ? AND account_id = ? AND space_id = ? AND status = ?",
                    (now, reason, row["handoff_id"], account_id, space_id, row["status"]),
                ).rowcount
                if changed:
                    cancelled += 1
                    self._append_sync_event(connection, account_id, space_id, "task_handoff", row["handoff_id"], "handoff.cancelled", {"handoff_id": row["handoff_id"], "status": "cancelled", "reason": reason, "finished_at": now})
        return cancelled

    def list_pending_handoffs_for_runtime(self, account_id: str, space_id: str, runtime_id: str, limit: int = 20) -> list[dict]:
        self.expire_stale_task_handoffs(account_id, space_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_handoffs WHERE account_id = ? AND space_id = ? "
                "AND target_runtime_id = ? AND direction = 'cloud_to_local' AND status = 'pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (account_id, space_id, runtime_id, limit),
            ).fetchall()
        return [self._task_handoff_record(row) for row in rows]

    def list_active_handoffs_for_runtime(self, account_id: str, space_id: str, runtime_id: str, limit: int = 20) -> list[dict]:
        self.expire_stale_task_handoffs(account_id, space_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_handoffs WHERE account_id = ? AND space_id = ? "
                "AND target_runtime_id = ? AND direction = 'cloud_to_local' "
                "AND status IN ('accepted', 'running') ORDER BY created_at ASC LIMIT ?",
                (account_id, space_id, runtime_id, limit),
            ).fetchall()
        return [self._task_handoff_record(row) for row in rows]

    def get_task_handoff_execution(self, account_id: str, space_id: str, handoff_id: str, runtime_id: str, device_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT h.*, r.device_id FROM task_handoffs h JOIN device_runtimes r ON r.runtime_id = h.target_runtime_id WHERE h.handoff_id = ? AND h.account_id = ? AND h.space_id = ? AND h.target_runtime_id = ?", (handoff_id, account_id, space_id, runtime_id)).fetchone()
        if not row or row["direction"] != "cloud_to_local" or row["device_id"] != device_id or row["status"] not in {"pending", "accepted"}:
            return None
        if row["approval_id"]:
            with self._connect() as connection:
                approved = connection.execute("SELECT 1 FROM tool_approvals WHERE approval_id = ? AND account_id = ? AND space_id = ? AND status = 'approved' AND expires_at > ?", (row["approval_id"], account_id, space_id, time.time())).fetchone()
            if not approved:
                return None
        if not row["execution_manifest_json"] or not row["manifest_hash"]:
            return None
        return {"handoff_id": row["handoff_id"], "manifest": json.loads(row["execution_manifest_json"]), "manifest_hash": row["manifest_hash"]}

    @staticmethod
    def _task_handoff_record(row) -> dict:
        return {"id": row["handoff_id"], "task_id": row["task_id"], "conversation_id": row["conversation_id"], "agent_id": row["agent_id"], "snapshot_id": row["snapshot_id"], "source_runtime_id": row["source_runtime_id"], "target_runtime_id": row["target_runtime_id"], "direction": row["direction"], "status": row["status"], "created_at": row["created_at"], "accepted_at": row["accepted_at"], "started_at": row["started_at"], "finished_at": row["finished_at"], "error": row["error_message"], "has_execution_manifest": bool(row["execution_manifest_json"]), "manifest_hash": row["manifest_hash"], "approval_id": row["approval_id"]}

    def create_runtime_snapshot(self, account_id: str, space_id: str, conversation_id: str, payload: dict) -> dict:
        """Persist a display-safe execution envelope, never request content or secrets."""
        now = time.time()
        snapshot_id = uuid.uuid4().hex
        safe_payload = {
            "agent_id": str(payload.get("agent_id", ""))[:80],
            "model_key": str(payload.get("model_key", ""))[:80],
            "prompt_version": str(payload.get("prompt_version", ""))[:80],
            "prompt_hash": str(payload.get("prompt_hash", ""))[:128],
            "history_message_count": max(0, int(payload.get("history_message_count", 0))),
            "context_block_count": max(0, int(payload.get("context_block_count", 0))),
            "context_block_tokens": max(0, int(payload.get("context_block_tokens", 0))),
            "memory_count": max(0, int(payload.get("memory_count", 0))),
            "memory_tokens": max(0, int(payload.get("memory_tokens", 0))),
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_snapshots(snapshot_id, account_id, space_id, conversation_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, account_id, space_id, conversation_id, json.dumps(safe_payload, ensure_ascii=False), now),
            )
        return {"id": snapshot_id, "conversation_id": conversation_id, "payload": safe_payload, "created_at": now}

    def create_agent_run(self, account_id: str, space_id: str, conversation_id: str, agent_id: str, snapshot_id: str, model_key: str, task_id: Optional[str] = None, prompt_version: str = "", prompt_hash: str = "") -> dict:
        run_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_runs(run_id, account_id, space_id, conversation_id, task_id, agent_id, snapshot_id, status, model_key, prompt_version, prompt_hash, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (run_id, account_id, space_id, conversation_id, task_id, agent_id, snapshot_id, model_key, prompt_version[:80], prompt_hash[:128], now),
            )
            self._append_run_event(connection, account_id, space_id, run_id, "run.started", "Agent Run 已创建", now)
            self._append_sync_event(connection, account_id, space_id, "agent_run", run_id, "agent_run.created", {"run_id": run_id, "conversation_id": conversation_id, "agent_id": agent_id, "status": "running", "created_at": now})
            self._append_sync_event(connection, account_id, space_id, "runtime", space_id, "runtime.updated", {"run_id": run_id, "status": "running", "created_at": now})
        return self.get_agent_run(account_id, space_id, run_id)  # type: ignore[return-value]

    def complete_agent_run(self, account_id: str, space_id: str, run_id: str, summary: str, iterations: int, tool_calls: list[dict]) -> Optional[dict]:
        now = time.time()
        safe_tool_calls = [{"name": str(item.get("name", ""))[:120], "success": bool(item.get("success", False))} for item in tool_calls]
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE agent_runs SET status = 'completed', finished_at = ?, iterations = ?, tool_calls_json = ?, summary = ? WHERE run_id = ? AND account_id = ? AND space_id = ? AND status = 'running'",
                (now, max(1, int(iterations)), json.dumps(safe_tool_calls, ensure_ascii=False), summary[:1000], run_id, account_id, space_id),
            ).rowcount
            if not changed:
                return None
            row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
            self._append_run_event(connection, account_id, space_id, run_id, "tools.completed", f"已完成 {len(safe_tool_calls)} 次工具调用", now)
            self._append_run_event(connection, account_id, space_id, run_id, "run.completed", "回复已写入会话", now)
            payload = {"run_id": run_id, "conversation_id": row["conversation_id"], "agent_id": row["agent_id"], "status": "completed", "finished_at": now, "summary": summary[:300]}
            self._append_sync_event(connection, account_id, space_id, "agent_run", run_id, "agent_run.completed", payload)
            self._append_sync_event(connection, account_id, space_id, "runtime", space_id, "runtime.updated", {"run_id": run_id, "status": "completed", "finished_at": now})
        return self.get_agent_run(account_id, space_id, run_id)

    def fail_agent_run(self, account_id: str, space_id: str, run_id: str, error_message: str) -> Optional[dict]:
        now = time.time()
        safe_error = error_message.replace("\n", " ")[:500]
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE agent_runs SET status = 'failed', finished_at = ?, error_message = ? WHERE run_id = ? AND account_id = ? AND space_id = ? AND status = 'running'",
                (now, safe_error, run_id, account_id, space_id),
            ).rowcount
            if not changed:
                return None
            row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
            self._append_run_event(connection, account_id, space_id, run_id, "run.failed", safe_error, now)
            payload = {"run_id": run_id, "conversation_id": row["conversation_id"], "agent_id": row["agent_id"], "status": "failed", "finished_at": now, "error": safe_error}
            self._append_sync_event(connection, account_id, space_id, "agent_run", run_id, "agent_run.failed", payload)
            self._append_sync_event(connection, account_id, space_id, "runtime", space_id, "runtime.updated", {"run_id": run_id, "status": "failed", "finished_at": now})
        return self.get_agent_run(account_id, space_id, run_id)

    def get_agent_run(self, account_id: str, space_id: str, run_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ? AND account_id = ? AND space_id = ?", (run_id, account_id, space_id)).fetchone()
        return self._agent_run_record(row) if row else None

    def append_agent_run_event(self, account_id: str, space_id: str, run_id: str, event_type: str, detail: str) -> Optional[dict]:
        now = time.time()
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM agent_runs WHERE run_id = ? AND account_id = ? AND space_id = ?",
                (run_id, account_id, space_id),
            ).fetchone()
            if not run:
                return None
            self._append_run_event(connection, account_id, space_id, run_id, event_type, detail, now)
        return {"run_id": run_id, "type": event_type, "detail": detail[:300], "created_at": now}

    def list_agent_run_events(self, account_id: str, space_id: str, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id, event_type, detail, created_at FROM run_events WHERE run_id = ? AND account_id = ? AND space_id = ? ORDER BY created_at, rowid",
                (run_id, account_id, space_id),
            ).fetchall()
        return [{"id": row["event_id"], "type": row["event_type"], "detail": row["detail"], "created_at": row["created_at"]} for row in rows]

    def list_agent_runs(self, account_id: str, space_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agent_runs WHERE account_id = ? AND space_id = ? ORDER BY started_at DESC LIMIT ?", (account_id, space_id, limit)).fetchall()
        return [self._agent_run_record(row) for row in rows]

    def runtime_snapshot(self, account_id: str, space_id: str, limit: int = 20) -> dict:
        runs = self.list_agent_runs(account_id, space_id, limit)
        active_runs = [run for run in runs if run["status"] == "running"]
        pending_tasks = len([task for task in self.list_tasks(account_id, space_id) if task["status"] == "pending"])
        pending_approvals = len(self.list_tool_approvals(account_id, space_id, status="pending", limit=500))
        return {"observed_at": time.time(), "cloud": {"status": "online", "detail": "IDEA service"}, "device_runtimes": self.list_device_runtimes(account_id, space_id), "active_runs": active_runs, "recent_runs": runs, "task_counts": {"active": len(active_runs), "pending": pending_tasks}, "pending_approvals": pending_approvals}

    @staticmethod
    def _append_run_event(connection, account_id: str, space_id: str, run_id: str, event_type: str, detail: str, created_at: float) -> None:
        connection.execute(
            "INSERT INTO run_events(event_id, run_id, account_id, space_id, event_type, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, run_id, account_id, space_id, event_type, detail[:300], created_at),
        )

    @staticmethod
    def _agent_run_record(row) -> dict:
        return {
            "id": row["run_id"], "conversation_id": row["conversation_id"], "task_id": row["task_id"],
            "agent_id": row["agent_id"], "snapshot_id": row["snapshot_id"], "status": row["status"],
            "model_key": row["model_key"], "prompt_version": row["prompt_version"], "prompt_hash": row["prompt_hash"], "started_at": row["started_at"], "finished_at": row["finished_at"],
            "iterations": row["iterations"], "tool_calls": json.loads(row["tool_calls_json"]),
            "summary": row["summary"], "error": row["error_message"],
        }

    def create_scheduled_job(self, account_id: str, space_id: str, agent_id: str, tool_name: str, args: dict, interval_seconds: float) -> dict:
        if interval_seconds < 30:
            raise ValueError("调度间隔不能小于 30 秒")
        job_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scheduled_jobs(job_id, account_id, space_id, agent_id, tool_name, args_json, interval_seconds, next_run_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (job_id, account_id, space_id, agent_id, tool_name, json.dumps(args or {}, ensure_ascii=False), interval_seconds, now + interval_seconds, now, now),
            )
        return {"id": job_id, "account_id": account_id, "space_id": space_id, "agent_id": agent_id, "tool_name": tool_name, "args": args or {}, "interval_seconds": interval_seconds, "status": "active", "next_run_at": now + interval_seconds}

    def list_scheduled_jobs(self, account_id: str, space_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM scheduled_jobs WHERE account_id = ? AND space_id = ? AND status != 'deleted' ORDER BY created_at DESC", (account_id, space_id)).fetchall()
            return [dict(row) for row in rows]

    def delete_scheduled_job(self, account_id: str, space_id: str, job_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute("UPDATE scheduled_jobs SET status = 'deleted', updated_at = ? WHERE job_id = ? AND account_id = ? AND space_id = ? AND status != 'deleted'", (time.time(), job_id, account_id, space_id)).rowcount
            return bool(changed)

    def due_scheduled_jobs(self, now: Optional[float] = None, limit: int = 20) -> list[dict]:
        """到期待执行的作业（scheduler 扫描）。"""
        now = now or time.time()
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM scheduled_jobs WHERE status = 'active' AND next_run_at <= ? ORDER BY next_run_at LIMIT ?", (now, limit)).fetchall()
            return [dict(row) for row in rows]

    def update_scheduled_job_run(self, job_id: str, last_status: str, last_output: str, next_run_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE scheduled_jobs SET last_status = ?, last_output = ?, next_run_at = ?, updated_at = ? WHERE job_id = ?",
                (last_status, last_output[:2000], next_run_at, time.time(), job_id),
            )

    def delete_task(self, account_id: str, space_id: str, task_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute("UPDATE tasks SET status = 'deleted', updated_at = ? WHERE task_id = ? AND account_id = ? AND space_id = ? AND status != 'deleted'", (time.time(), task_id, account_id, space_id)).rowcount
            if changed:
                self._append_sync_event(connection, account_id, space_id, "task", task_id, "task.deleted", {})
            return bool(changed)

    def list_sync_events(self, account_id: str, space_id: str, after_event_id: int, limit: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM sync_events WHERE account_id = ? AND space_id = ? AND event_id > ? ORDER BY event_id LIMIT ?", (account_id, space_id, after_event_id, limit)).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def create_memory(self, account_id: str, space_id: str, namespace: str, category: str, content: str, created_by: str) -> dict:
        memory_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO long_term_memories(memory_id, account_id, space_id, namespace, category, content, created_by, created_at, updated_at, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (memory_id, account_id, space_id, namespace, category, content, created_by, now, now),
            )
            self._append_memory_sync_events(connection, account_id, space_id, namespace, "memory", memory_id, "memory.created", {"namespace": namespace, "category": category, "revision": 1, "created_at": now, "actor_principal_id": created_by})
        return {"id": memory_id, "namespace": namespace, "category": category, "content": content, "status": "active", "revision": 1, "created_at": now, "updated_at": now}

    def list_memories(self, account_id: str, space_id: str, namespaces: list[str], query: Optional[str] = None, limit: int = 50) -> list[dict]:
        if not namespaces:
            return []
        placeholders = ", ".join("?" for _ in namespaces)
        params: list = [space_id, *namespaces]
        query_sql = f"SELECT * FROM long_term_memories WHERE space_id = ? AND status = 'active' AND namespace IN ({placeholders})"
        if query:
            query_sql += " AND content LIKE ?"
            params.append(f"%{query}%")
        query_sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query_sql, params).fetchall()
            return [{"id": row["memory_id"], "namespace": row["namespace"], "category": row["category"], "content": row["content"], "status": row["status"], "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    def update_memory(self, account_id: str, space_id: str, memory_id: str, namespaces: list[str], category: str, content: str, expected_revision: int, principal_id: str) -> tuple[Optional[dict], Optional[int]]:
        if not namespaces:
            return None, None
        personal_namespaces = [namespace for namespace in namespaces if not namespace.startswith("shared/") and not namespace.startswith("project/")]
        placeholders = ", ".join("?" for _ in personal_namespaces) or "''"
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(f"SELECT * FROM long_term_memories WHERE memory_id = ? AND space_id = ? AND status = 'active' AND (namespace IN (?, ?) OR (account_id = ? AND namespace IN ({placeholders})))", (memory_id, space_id, f"shared/{space_id}", f"project/{space_id}", account_id, *personal_namespaces)).fetchone()
            if not row:
                return None, None
            if not self.memory_write_allowed(principal_id, space_id, row["namespace"]):
                raise PermissionError("无权修改共享记忆")
            changed = connection.execute("UPDATE long_term_memories SET category = ?, content = ?, updated_at = ?, revision = revision + 1 WHERE memory_id = ? AND revision = ? AND status = 'active'", (category, content, now, memory_id, expected_revision)).rowcount
            if not changed:
                return None, row["revision"]
            revision = expected_revision + 1
            self._append_memory_sync_events(connection, account_id, space_id, row["namespace"], "memory", memory_id, "memory.updated", {"namespace": row["namespace"], "category": category, "revision": revision, "updated_at": now, "actor_principal_id": principal_id})
            return {"id": memory_id, "namespace": row["namespace"], "category": category, "content": content, "status": "active", "revision": revision, "created_at": row["created_at"], "updated_at": now}, None

    def delete_memory(self, account_id: str, space_id: str, memory_id: str, namespaces: list[str], expected_revision: int, principal_id: str) -> tuple[bool, Optional[int]]:
        if not namespaces:
            return False, None
        personal_namespaces = [namespace for namespace in namespaces if not namespace.startswith("shared/") and not namespace.startswith("project/")]
        placeholders = ", ".join("?" for _ in personal_namespaces) or "''"
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(f"SELECT namespace, revision FROM long_term_memories WHERE memory_id = ? AND space_id = ? AND status = 'active' AND (namespace IN (?, ?) OR (account_id = ? AND namespace IN ({placeholders})))", (memory_id, space_id, f"shared/{space_id}", f"project/{space_id}", account_id, *personal_namespaces)).fetchone()
            if not row:
                return False, None
            if not self.memory_write_allowed(principal_id, space_id, row["namespace"]):
                raise PermissionError("无权删除共享记忆")
            changed = connection.execute("UPDATE long_term_memories SET status = 'deleted', deleted_at = ?, updated_at = ?, revision = revision + 1 WHERE memory_id = ? AND revision = ? AND status = 'active'", (now, now, memory_id, expected_revision)).rowcount
            if not changed:
                return False, row["revision"]
            revision = expected_revision + 1
            self._append_memory_sync_events(connection, account_id, space_id, row["namespace"], "memory", memory_id, "memory.deleted", {"namespace": row["namespace"], "revision": revision, "deleted_at": now, "actor_principal_id": principal_id})
            return True, None

    def cleanup_daily_short_memories(self, account_id: str, space_id: str, namespace: str, created_before: float, principal_id: str) -> int:
        now = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT memory_id, revision FROM long_term_memories WHERE account_id = ? AND space_id = ? AND namespace = ? AND status = 'active' AND created_at < ? AND category LIKE ?",
                (account_id, space_id, namespace, created_before, "%/short]"),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE long_term_memories SET status = 'deleted', deleted_at = ?, updated_at = ?, revision = revision + 1 WHERE memory_id = ? AND status = 'active'",
                    (now, now, row["memory_id"]),
                )
                self._append_memory_sync_events(
                    connection,
                    account_id,
                    space_id,
                    namespace,
                    "memory",
                    row["memory_id"],
                    "memory.deleted",
                    {"namespace": namespace, "revision": row["revision"] + 1, "deleted_at": now, "actor_principal_id": principal_id},
                )
            return len(rows)

    def create_tool_approval(self, account_id: str, space_id: str, principal_id: str, agent_id: str, tool_name: str, fingerprint: str, args_summary: str, ttl_seconds: int = 600) -> dict:
        now = time.time()
        approval = {
            "approval_id": f"approval-{uuid.uuid4().hex}",
            "account_id": account_id,
            "space_id": space_id,
            "principal_id": principal_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "fingerprint": fingerprint,
            "args_summary": args_summary,
            "status": "pending",
            "requested_at": now,
            "expires_at": now + ttl_seconds,
            "decided_at": None,
            "decided_by": None,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tool_approvals(approval_id, account_id, space_id, principal_id, agent_id, tool_name, fingerprint, args_summary, status, requested_at, expires_at, decided_at, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL)",
                (approval["approval_id"], account_id, space_id, principal_id, agent_id, tool_name, fingerprint, args_summary, now, approval["expires_at"]),
            )
            for target in self._sync_target_accounts(connection, account_id, space_id):
                self._append_sync_event(
                    connection,
                    target,
                    space_id,
                    "tool_approval",
                    approval["approval_id"],
                    "approval.requested",
                    {"tool_name": tool_name, "args_summary": args_summary[:500], "agent_id": agent_id, "requester_principal_id": principal_id, "expires_at": approval["expires_at"]},
                )
        return approval

    def _sync_target_accounts(self, connection, account_id: str, space_id: str) -> list[str]:
        """同步事件的接收账号：发起者 + 所有已链接的 Owner 账号。"""
        owner_accounts = [row["account_id"] for row in connection.execute("SELECT account_id FROM owner_account_links WHERE status = 'active'").fetchall()]
        return list(dict.fromkeys([account_id, *owner_accounts]))

    def find_tool_approval(self, account_id: str, space_id: str, principal_id: str, tool_name: str, fingerprint: str) -> Optional[dict]:
        """返回可用的已批准授权，否则复用未过期的 pending 请求；两者皆无返回 None。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_approvals WHERE account_id = ? AND space_id = ? AND principal_id = ? AND tool_name = ? AND fingerprint = ? AND status != 'denied' AND expires_at > ? ORDER BY requested_at DESC LIMIT 1",
                (account_id, space_id, principal_id, tool_name, fingerprint, time.time()),
            ).fetchone()
            return dict(row) if row else None

    def list_tool_approvals(self, account_id: str, space_id: str, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM tool_approvals WHERE account_id = ? AND space_id = ? AND status = ? ORDER BY requested_at DESC LIMIT ?",
                    (account_id, space_id, status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM tool_approvals WHERE account_id = ? AND space_id = ? ORDER BY requested_at DESC LIMIT ?",
                    (account_id, space_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def decide_tool_approval(self, approval_id: str, account_id: str, space_id: str, decision: str, decided_by: str) -> Optional[dict]:
        if decision not in ("approved", "denied"):
            raise ValueError("审批决策无效")
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_approvals WHERE approval_id = ? AND account_id = ? AND space_id = ?",
                (approval_id, account_id, space_id),
            ).fetchone()
            if not row:
                return None
            if row["status"] != "pending":
                return dict(row)
            if row["expires_at"] <= now:
                connection.execute("UPDATE tool_approvals SET status = 'expired', decided_at = ?, decided_by = ? WHERE approval_id = ?", (now, decided_by, approval_id))
                return dict(connection.execute("SELECT * FROM tool_approvals WHERE approval_id = ?", (approval_id,)).fetchone())
            connection.execute(
                "UPDATE tool_approvals SET status = ?, decided_at = ?, decided_by = ? WHERE approval_id = ? AND status = 'pending'",
                (decision, now, decided_by, approval_id),
            )
            updated = dict(connection.execute("SELECT * FROM tool_approvals WHERE approval_id = ?", (approval_id,)).fetchone())
            for target in self._sync_target_accounts(connection, row["account_id"], row["space_id"]):
                self._append_sync_event(
                    connection,
                    target,
                    row["space_id"],
                    "tool_approval",
                    approval_id,
                    "approval.decided",
                    {"tool_name": row["tool_name"], "status": decision, "decided_by": decided_by},
                )
            return updated

    VALID_GRANT_CAPABILITIES = {"file.read", "file.write", "file.delete", "command", "network", "delegate", "ssh"}

    def create_capability_grant(self, account_id: str, granted_by: str, capability: str, workspace: str = "", constraints: Optional[dict] = None, expires_in_days: Optional[int] = None) -> dict:
        if capability not in self.VALID_GRANT_CAPABILITIES:
            raise ValueError("授权能力无效")
        now = time.time()
        grant = {
            "grant_id": f"grant-{uuid.uuid4().hex}",
            "account_id": account_id,
            "granted_by": granted_by,
            "capability": capability,
            "workspace": workspace or "",
            "constraints_json": json.dumps(constraints or {}, ensure_ascii=False)[:2000],
            "status": "active",
            "created_at": now,
            "expires_at": (now + expires_in_days * 86400) if expires_in_days else None,
            "revoked_at": None,
            "revoked_by": None,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO capability_grants(grant_id, account_id, granted_by, capability, workspace, constraints_json, status, created_at, expires_at, revoked_at, revoked_by) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL)",
                (grant["grant_id"], account_id, granted_by, capability, grant["workspace"], grant["constraints_json"], now, grant["expires_at"]),
            )
        return grant

    def find_valid_grant(self, account_id: str, capability: str, workspace: str = "") -> Optional[dict]:
        """返回当前账号在指定空间下可用的有效授权；workspace 为空表示通配。"""
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM capability_grants WHERE account_id = ? AND capability = ? AND status = 'active' AND (expires_at IS NULL OR expires_at > ?) AND (workspace = '' OR workspace = ?) ORDER BY created_at DESC LIMIT 1",
                (account_id, capability, now, workspace),
            ).fetchone()
            return dict(row) if row else None

    def list_capability_grants(self, account_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            if account_id:
                rows = connection.execute(
                    "SELECT * FROM capability_grants WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                    (account_id, limit),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM capability_grants ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def revoke_capability_grant(self, grant_id: str, revoked_by: str) -> Optional[dict]:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM capability_grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "active":
                connection.execute("UPDATE capability_grants SET status = 'revoked', revoked_at = ?, revoked_by = ? WHERE grant_id = ?", (now, revoked_by, grant_id))
            return dict(connection.execute("SELECT * FROM capability_grants WHERE grant_id = ?", (grant_id,)).fetchone())

    def create_file_change_review(self, account_id: str, space_id: str, principal_id: str, agent_id: str, tool_name: str, file_path: str, backup_path: Optional[str], diff_summary: str) -> dict:
        now = time.time()
        review = {
            "change_id": f"change-{uuid.uuid4().hex}",
            "account_id": account_id,
            "space_id": space_id,
            "principal_id": principal_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "file_path": file_path,
            "backup_path": backup_path,
            "diff_summary": diff_summary[:2000],
            "status": "pending",
            "created_at": now,
            "reviewed_at": None,
            "reviewed_by": None,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO file_change_reviews(change_id, account_id, space_id, principal_id, agent_id, tool_name, file_path, backup_path, diff_summary, status, created_at, reviewed_at, reviewed_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)",
                (review["change_id"], account_id, space_id, principal_id, agent_id, tool_name, file_path, backup_path, review["diff_summary"], now),
            )
        return review

    def get_file_change_review(self, change_id: str, account_id: str, space_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM file_change_reviews WHERE change_id = ? AND account_id = ? AND space_id = ?", (change_id, account_id, space_id)).fetchone()
            return dict(row) if row else None

    def list_file_change_reviews(self, account_id: str, space_id: str, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM file_change_reviews WHERE account_id = ? AND space_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                    (account_id, space_id, status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM file_change_reviews WHERE account_id = ? AND space_id = ? ORDER BY created_at DESC LIMIT ?",
                    (account_id, space_id, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def review_file_change(self, change_id: str, account_id: str, space_id: str, decision: str, reviewed_by: str) -> Optional[dict]:
        if decision not in ("accepted", "reverted"):
            raise ValueError("审查决策无效")
        now = time.time()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM file_change_reviews WHERE change_id = ? AND account_id = ? AND space_id = ?", (change_id, account_id, space_id)).fetchone()
            if not row:
                return None
            if row["status"] != "pending":
                return dict(row)
            connection.execute(
                "UPDATE file_change_reviews SET status = ?, reviewed_at = ?, reviewed_by = ? WHERE change_id = ? AND status = 'pending'",
                (decision, now, reviewed_by, change_id),
            )
            return dict(connection.execute("SELECT * FROM file_change_reviews WHERE change_id = ?", (change_id,)).fetchone())

    @staticmethod
    def _append_memory_sync_events(connection, account_id: str, space_id: str, namespace: str, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict) -> None:
        accounts = [account_id]
        if namespace.startswith("shared/") or namespace.startswith("project/"):
            accounts = [row["account_id"] for row in connection.execute("SELECT DISTINCT p.account_id FROM space_members m JOIN principals p ON p.principal_id = m.principal_id WHERE m.space_id = ?", (space_id,)).fetchall()]
        for target_account_id in accounts:
            PlatformStore._append_sync_event(connection, target_account_id, space_id, aggregate_type, aggregate_id, event_type, payload)

    @staticmethod
    def _append_sync_event(connection, account_id: str, space_id: str, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict) -> None:
        connection.execute("INSERT INTO sync_events(account_id, space_id, aggregate_type, aggregate_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (account_id, space_id, aggregate_type, aggregate_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()))

    @staticmethod
    def _password_hash(password: str) -> str:
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"

    @staticmethod
    def _password_matches(password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, salt, expected = encoded.split("$")
            if algorithm != "scrypt":
                return False
            derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
            return hmac.compare_digest(derived.hex(), expected)
        except (ValueError, TypeError):
            return False

    def set_password_credential(self, account_id: str, password: str, is_seeded: bool = True) -> None:
        if not isinstance(password, str) or not password:
            raise ValueError("密码不能为空")
        now = time.time()
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)).fetchone():
                raise ValueError("账户不存在")
            connection.execute(
                "INSERT INTO password_credentials(account_id, password_hash, is_seeded, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET password_hash = excluded.password_hash, is_seeded = excluded.is_seeded, updated_at = excluded.updated_at",
                (account_id, self._password_hash(password), int(is_seeded), now, now),
            )

    def seed_preconfigured_accounts(self, seed: list[dict]) -> None:
        if not isinstance(seed, list):
            raise ValueError("预置账号必须为列表")
        for item in seed:
            if not isinstance(item, dict):
                raise ValueError("预置账号格式无效")
            email = self.normalize_email(item.get("email", ""))
            password, name, is_owner = item.get("password"), item.get("name"), item.get("is_owner")
            if not isinstance(password, str) or not password or not isinstance(name, str) or not name.strip() or not isinstance(is_owner, bool):
                raise ValueError("预置账号字段无效")
            now = time.time()
            with self._connect() as connection:
                row = connection.execute("SELECT a.account_id, p.principal_id, p.role FROM accounts a JOIN principals p ON p.account_id = a.account_id WHERE a.email = ?", (email,)).fetchone()
                if row:
                    account_id = row["account_id"]
                    principal_id = row["principal_id"]
                else:
                    account_id = f"account-{uuid.uuid4().hex}"
                    principal_id = f"principal-{uuid.uuid4().hex}"
                    connection.execute("INSERT INTO accounts(account_id, name, email, created_at) VALUES (?, ?, ?, ?)", (account_id, name.strip()[:80], email, now))
                    connection.execute("INSERT INTO principals(principal_id, account_id, role, display_name, created_at) VALUES (?, ?, ?, ?, ?)", (principal_id, account_id, "member", name.strip()[:80], now))
                    space_id = f"space-{account_id}"
                    connection.execute("INSERT INTO spaces(space_id, name, space_type, created_by, created_at) VALUES (?, ?, ?, ?, ?)", (space_id, f"{name.strip()[:80]} 的个人空间", "personal", principal_id, now))
                    connection.execute("INSERT INTO space_members(space_id, principal_id, role, created_at) VALUES (?, ?, ?, ?)", (space_id, principal_id, "owner", now))
                if is_owner:
                    connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) VALUES (?, 'owner', ?) ON CONFLICT(account_id) DO UPDATE SET work_role = 'owner', updated_at = excluded.updated_at", (account_id, now))
                    connection.execute("INSERT INTO owner_principals(owner_principal_id, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id) DO NOTHING", ("owner-shiroha-nao", name.strip()[:80], now))
                    connection.execute("INSERT INTO owner_account_links(owner_principal_id, account_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id, account_id) DO UPDATE SET status = 'active', revoked_at = NULL", ("owner-shiroha-nao", account_id, now))
                else:
                    connection.execute("INSERT INTO account_profiles(account_id, work_role, updated_at) VALUES (?, 'user', ?) ON CONFLICT(account_id) DO NOTHING", (account_id, now))
            self.set_password_credential(account_id, password, is_seeded=True)

    def authenticate_password_login(self, email: str, password: str, device_id: Optional[str], normal_limit: int = 5, window: int = 300) -> tuple[Principal, dict, str]:
        email = self.normalize_email(email)
        now = time.time()
        if not isinstance(password, str) or not password:
            raise ValueError("邮箱或密码错误")
        with self._connect() as connection:
            row = connection.execute("SELECT p.principal_id, p.account_id, p.role, c.password_hash, EXISTS(SELECT 1 FROM owner_account_links l WHERE l.account_id = a.account_id AND l.status = 'active') AS is_owner FROM accounts a JOIN principals p ON p.account_id = a.account_id JOIN password_credentials c ON c.account_id = a.account_id WHERE a.email = ? AND a.status = 'active' AND p.status = 'active'", (email,)).fetchone()
            is_owner = bool(row and row["is_owner"])
            if not is_owner:
                failures = connection.execute("SELECT COUNT(*) FROM auth_login_attempts WHERE email = ? AND attempted_at > ?", (email, now - window)).fetchone()[0]
                if failures >= normal_limit:
                    raise ValueError("登录尝试过于频繁，请稍后再试")
            if not row or not self._password_matches(password, row["password_hash"]):
                if not is_owner:
                    connection.execute("INSERT INTO auth_login_attempts(email, attempted_at) VALUES (?, ?)", (email, now))
                    connection.commit()
                raise ValueError("邮箱或密码错误")
            connection.execute("DELETE FROM auth_login_attempts WHERE email = ?", (email,))
            principal = Principal(row["principal_id"], row["account_id"], row["role"], "")
        session = self.issue_session(principal, device_id)
        authenticated = self.authenticate(session["access_token"], device_id)
        return principal, session, self.route_for_principal(authenticated) if authenticated else "idea_assistant"

    @staticmethod
    def normalize_email(email: str) -> str:
        value = email.strip().lower()
        if len(value) > 254 or value.count("@") != 1:
            raise ValueError("邮箱地址格式无效")
        local, domain = value.split("@", 1)
        if not local or not domain or "." not in domain:
            raise ValueError("邮箱地址格式无效")
        return value

    def issue_email_code(self, email: str, purpose: str, cooldown_seconds: int = 60, ttl_seconds: int = 600) -> str:
        email = self.normalize_email(email)
        now = time.time()
        with self._connect() as connection:
            latest = connection.execute(
                "SELECT created_at FROM email_verification_codes WHERE email = ? AND purpose = ? ORDER BY created_at DESC LIMIT 1",
                (email, purpose),
            ).fetchone()
            if latest and now - latest["created_at"] < cooldown_seconds:
                raise ValueError("验证码发送过于频繁，请稍后再试")
            code = f"{secrets.randbelow(1_000_000):06d}"
            connection.execute(
                "INSERT INTO email_verification_codes(verification_id, email, purpose, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, email, purpose, self.hash_token(code), now + ttl_seconds, now),
            )
            return code

    def verify_email_code(self, email: str, purpose: str, code: str, max_attempts: int = 5) -> str:
        email = self.normalize_email(email)
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM email_verification_codes WHERE email = ? AND purpose = ? AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (email, purpose),
            ).fetchone()
            if not row or row["expires_at"] <= now or row["attempts"] >= max_attempts:
                raise ValueError("验证码无效或已过期")
            if not secrets.compare_digest(row["code_hash"], self.hash_token(code.strip())):
                connection.execute("UPDATE email_verification_codes SET attempts = attempts + 1 WHERE verification_id = ?", (row["verification_id"],))
                raise ValueError("验证码无效或已过期")
            connection.execute("UPDATE email_verification_codes SET consumed_at = ? WHERE verification_id = ?", (now, row["verification_id"]))
            return email

    def get_or_create_email_account(self, email: str) -> Principal:
        email = self.normalize_email(email)
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT p.principal_id, p.account_id, p.role FROM accounts a JOIN principals p ON p.account_id = a.account_id WHERE a.email = ?",
                (email,),
            ).fetchone()
            if row:
                return Principal(row["principal_id"], row["account_id"], row["role"], "")
            account_id = f"account-{uuid.uuid4().hex}"
            principal_id = f"principal-{uuid.uuid4().hex}"
            display_name = email.split("@", 1)[0][:80]
            connection.execute("INSERT INTO accounts(account_id, name, email, created_at) VALUES (?, ?, ?, ?)", (account_id, display_name, email, now))
            connection.execute("INSERT INTO principals(principal_id, account_id, role, display_name, created_at) VALUES (?, ?, ?, ?, ?)", (principal_id, account_id, "member", display_name, now))
            connection.execute("INSERT INTO spaces(space_id, name, space_type, created_by, created_at) VALUES (?, ?, ?, ?, ?)", (f"space-{account_id}", f"{display_name} 的个人空间", "personal", principal_id, now))
            connection.execute("INSERT INTO space_members(space_id, principal_id, role, created_at) VALUES (?, ?, ?, ?)", (f"space-{account_id}", principal_id, "owner", now))
            return Principal(principal_id, account_id, "member", "")

    def issue_session(self, principal: Principal, device_id: Optional[str], access_ttl_seconds: int = 900, refresh_ttl_seconds: int = 2_592_000) -> dict:
        if not device_id:
            raise ValueError("缺少设备标识")
        now = time.time()
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(48)
        session_id = f"session-{uuid.uuid4().hex}"
        token_id = f"token-{uuid.uuid4().hex}"
        with self._connect() as connection:
            owner_device_id = self._owner_device_for_account(connection, principal.account_id, device_id, now)
            connection.execute("INSERT INTO account_sessions(session_id, account_id, principal_id, device_id, refresh_token_hash, expires_at, created_at, owner_device_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (session_id, principal.account_id, principal.principal_id, device_id, self.hash_token(refresh_token), now + refresh_ttl_seconds, now, owner_device_id))
            connection.execute("INSERT INTO access_tokens(token_id, principal_id, token_hash, expires_at, created_at, session_id) VALUES (?, ?, ?, ?, ?, ?)", (token_id, principal.principal_id, self.hash_token(access_token), now + access_ttl_seconds, now, session_id))
        return {"access_token": access_token, "access_expires_at": now + access_ttl_seconds, "refresh_token": refresh_token, "session_id": session_id, "owner_device_id": owner_device_id}

    def refresh_session(self, refresh_token: str, device_id: Optional[str]) -> tuple[Principal, dict]:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.*, p.role FROM account_sessions s JOIN principals p ON p.principal_id = s.principal_id WHERE s.refresh_token_hash = ? AND s.status = 'active' AND s.expires_at > ?",
                (self.hash_token(refresh_token), now),
            ).fetchone()
            if not row or not device_id or row["device_id"] != device_id:
                raise ValueError("登录会话已失效")
            connection.execute("UPDATE account_sessions SET status = 'rotated', revoked_at = ?, last_used_at = ? WHERE session_id = ?", (now, now, row["session_id"]))
            connection.execute("UPDATE access_tokens SET status = 'revoked' WHERE session_id = ?", (row["session_id"],))
            principal = Principal(row["principal_id"], row["account_id"], row["role"], "")
        return principal, self.issue_session(principal, device_id)

    def revoke_session(self, refresh_token: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT session_id FROM account_sessions WHERE refresh_token_hash = ?", (self.hash_token(refresh_token),)).fetchone()
            if row:
                connection.execute("UPDATE account_sessions SET status = 'revoked', revoked_at = ? WHERE session_id = ?", (time.time(), row["session_id"]))
                connection.execute("UPDATE access_tokens SET status = 'revoked' WHERE session_id = ?", (row["session_id"],))

    def route_for_principal(self, principal: Principal) -> str:
        if principal.mcp_credential_id:
            return "owner_idea"
        if principal.principal_id == "principal-owner" and principal.token_id == "token-owner":
            return "owner_idea"
        if principal.owner_device_status != "approved":
            return "idea_assistant"
        with self._connect() as connection:
            linked = connection.execute(
                "SELECT 1 FROM owner_account_links l JOIN owner_principals o ON o.owner_principal_id = l.owner_principal_id WHERE l.account_id = ? AND l.status = 'active' AND o.status = 'active'",
                (principal.account_id,),
            ).fetchone()
            return "owner_idea" if linked else "idea_assistant"

    @staticmethod
    def _owner_device_for_account(connection, account_id: str, device_id: str, now: float) -> Optional[str]:
        linked = connection.execute("SELECT owner_principal_id FROM owner_account_links WHERE account_id = ? AND status = 'active'", (account_id,)).fetchone()
        if not linked:
            return None
        row = connection.execute("SELECT owner_device_id FROM owner_devices WHERE owner_principal_id = ? AND account_id = ? AND device_id = ?", (linked["owner_principal_id"], account_id, device_id)).fetchone()
        if row:
            return row["owner_device_id"]
        owner_device_id = f"owner-device-{uuid.uuid4().hex}"
        connection.execute("INSERT INTO owner_devices(owner_device_id, owner_principal_id, account_id, device_id, requested_at) VALUES (?, ?, ?, ?, ?)", (owner_device_id, linked["owner_principal_id"], account_id, device_id, now))
        return owner_device_id

    def is_approved_owner_device(self, principal: Principal) -> bool:
        return self.route_for_principal(principal) == "owner_idea"

    def is_owner_controller(self, principal: Principal) -> bool:
        return (principal.principal_id == "principal-owner" and principal.token_id == "token-owner") or (principal.owner_device_status == "approved" and self.is_owner_account(principal.account_id))

    def owner_principal_id_for_account(self, account_id: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute("SELECT l.owner_principal_id FROM owner_account_links l JOIN owner_principals o ON o.owner_principal_id = l.owner_principal_id WHERE l.account_id = ? AND l.status = 'active' AND o.status = 'active'", (account_id,)).fetchone()
            return row["owner_principal_id"] if row else None

    def owner_scope_id(self, principal: Principal) -> Optional[str]:
        if principal.principal_id == "principal-owner" and principal.token_id == "token-owner":
            return "owner-shiroha-nao"
        return self.owner_principal_id_for_account(principal.account_id)

    def list_owner_devices(self, principal: Principal) -> list[dict]:
        owner_principal_id = self.owner_scope_id(principal)
        if not owner_principal_id:
            return []
        with self._connect() as connection:
            rows = connection.execute("SELECT owner_device_id, device_id, status, requested_at, approved_at, revoked_at, last_seen_at FROM owner_devices WHERE owner_principal_id = ? ORDER BY requested_at DESC", (owner_principal_id,)).fetchall()
            return [dict(row) for row in rows]

    def approve_owner_device(self, principal: Principal, owner_device_id: str) -> bool:
        if not self.is_owner_controller(principal):
            return False
        owner_principal_id = self.owner_scope_id(principal)
        with self._connect() as connection:
            return connection.execute("UPDATE owner_devices SET status = 'approved', approved_at = ?, approved_by_principal_id = ? WHERE owner_device_id = ? AND owner_principal_id = ? AND status = 'pending'", (time.time(), principal.principal_id, owner_device_id, owner_principal_id)).rowcount == 1

    def revoke_owner_device(self, principal: Principal, owner_device_id: str) -> bool:
        if not self.is_owner_controller(principal):
            return False
        now = time.time()
        owner_principal_id = self.owner_scope_id(principal)
        with self._connect() as connection:
            changed = connection.execute("UPDATE owner_devices SET status = 'revoked', revoked_at = ?, revoked_by_principal_id = ? WHERE owner_device_id = ? AND owner_principal_id = ? AND status IN ('pending', 'approved')", (now, principal.principal_id, owner_device_id, owner_principal_id)).rowcount
            if changed:
                session_ids = [row["session_id"] for row in connection.execute("SELECT session_id FROM account_sessions WHERE owner_device_id = ?", (owner_device_id,)).fetchall()]
                connection.execute("UPDATE account_sessions SET status = 'revoked', revoked_at = ? WHERE owner_device_id = ?", (now, owner_device_id))
                for session_id in session_ids:
                    connection.execute("UPDATE access_tokens SET status = 'revoked' WHERE session_id = ?", (session_id,))
            return changed == 1

    def link_owner_account(self, email: str) -> bool:
        email = self.normalize_email(email)
        now = time.time()
        with self._connect() as connection:
            account = connection.execute("SELECT account_id FROM accounts WHERE email = ? AND status = 'active'", (email,)).fetchone()
            if not account:
                return False
            connection.execute(
                "INSERT INTO owner_account_links(owner_principal_id, account_id, status, linked_at, revoked_at) VALUES (?, ?, 'active', ?, NULL) ON CONFLICT(owner_principal_id, account_id) DO UPDATE SET status = 'active', linked_at = excluded.linked_at, revoked_at = NULL",
                ("owner-shiroha-nao", account["account_id"], now),
            )
            devices = connection.execute("SELECT DISTINCT device_id FROM account_sessions WHERE account_id = ? AND device_id IS NOT NULL", (account["account_id"],)).fetchall()
            for device in devices:
                self._owner_device_for_account(connection, account["account_id"], device["device_id"], now)
            return True


def configured_token(config: dict) -> str:
    token = os.getenv("IDEA_AUTH_TOKEN", "").strip() or str(config.get("auth", {}).get("token", "")).strip()
    if not token or token == "your-secret-token-here-change-in-production":
        return ""
    return token


def extract_bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else ""


def require_context(request: Request) -> RequestContext:
    context = getattr(request.state, "context", None)
    if not context:
        raise HTTPException(status_code=401, detail="需要 Bearer Token")
    return context
