import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
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


class PlatformStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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
                    capability TEXT NOT NULL DEFAULT 'memory',
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

    def create_mcp_credential(self, principal: Principal, space_id: str, device_label: str, capability: str = "memory", expires_at: Optional[float] = None) -> dict:
        if not self.is_owner_controller(principal):
            raise PermissionError("需要已批准的私有设备")
        if not isinstance(device_label, str) or not (1 <= len(device_label.strip()) <= 80):
            raise ValueError("device_label 必须为 1 到 80 个字符")
        if capability not in {"memory", "idea"}:
            raise ValueError("capability 必须为 memory 或 idea")
        owner_principal_id = self.owner_scope_id(principal)
        if not owner_principal_id or not self.resolve_space(principal.principal_id, space_id):
            raise PermissionError("无权访问指定空间")
        credential_id = f"mcp-{uuid.uuid4().hex}"
        secret = secrets.token_urlsafe(32)
        token = f"mcp_{credential_id}.{secret}"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO mcp_device_credentials(credential_id, secret_hash, capability, owner_principal_id, account_id, principal_id, space_id, device_label, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (credential_id, self.hash_token(token), capability, owner_principal_id, principal.account_id, principal.principal_id, space_id, device_label.strip(), expires_at, now),
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
            self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "conversation.created", {"agent_id": agent_id, "created_at": now})
            return {"conversation_id": conversation_id, "account_id": account_id, "space_id": space_id, "agent_id": agent_id, "status": "active", "created_at": now, "updated_at": now}

    def get_conversation(self, account_id: str, space_id: str, conversation_id: str) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'", (conversation_id, account_id, space_id)).fetchone()
            return dict(row) if row else None

    def list_conversations(self, account_id: str, space_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT c.*, COUNT(m.message_id) AS message_count FROM conversations c LEFT JOIN conversation_messages m ON m.conversation_id = c.conversation_id WHERE c.account_id = ? AND c.space_id = ? AND c.status = 'active' GROUP BY c.conversation_id ORDER BY c.updated_at DESC", (account_id, space_id)).fetchall()
            return [dict(row) for row in rows]

    def count_active_conversations(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM conversations WHERE status = 'active'").fetchone()[0]

    def list_messages(self, account_id: str, space_id: str, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        if not self.get_conversation(account_id, space_id, conversation_id):
            raise LookupError("会话不存在")
        query = "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at"
        params: tuple = (conversation_id,)
        if limit:
            query = "SELECT * FROM (" + query + " DESC LIMIT ?) ORDER BY created_at"
            params = (conversation_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return [{"id": row["message_id"], "role": row["role"], "content": row["content"], "timestamp": row["created_at"], **json.loads(row["metadata_json"])} for row in rows]

    def append_message(self, account_id: str, space_id: str, conversation_id: str, role: str, content: str, metadata: Optional[dict] = None) -> dict:
        now = time.time()
        if not self.get_conversation(account_id, space_id, conversation_id):
            raise LookupError("会话不存在")
        message_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("INSERT INTO conversation_messages(message_id, conversation_id, role, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)", (message_id, conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), now))
            connection.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
            self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "message.appended", {"message_id": message_id, "role": role, "created_at": now})
        return {"id": message_id, "role": role, "content": content, "timestamp": now, **(metadata or {})}

    def reset_conversation(self, account_id: str, space_id: str, conversation_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute("UPDATE conversations SET status = 'reset', updated_at = ? WHERE conversation_id = ? AND account_id = ? AND space_id = ? AND status = 'active'", (time.time(), conversation_id, account_id, space_id)).rowcount
            if changed:
                self._append_sync_event(connection, account_id, space_id, "conversation", conversation_id, "conversation.reset", {})
            return bool(changed)

    def create_task(self, account_id: str, space_id: str, agent_id: str, title: str, description: str, conversation_id: Optional[str]) -> dict:
        task_id, now = uuid.uuid4().hex, time.time()
        with self._connect() as connection:
            connection.execute("INSERT INTO tasks(task_id, account_id, space_id, conversation_id, agent_id, title, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (task_id, account_id, space_id, conversation_id, agent_id, title, description, now, now))
            self._append_sync_event(connection, account_id, space_id, "task", task_id, "task.created", {"title": title, "status": "pending", "created_at": now})
        return {"id": task_id, "title": title, "description": description, "agent_id": agent_id, "conversation_id": conversation_id, "status": "pending", "account_id": account_id, "space_id": space_id, "created_at": now, "updated_at": now}

    def list_tasks(self, account_id: str, space_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks WHERE account_id = ? AND space_id = ? ORDER BY created_at DESC", (account_id, space_id)).fetchall()
            return [{"id": row["task_id"], **{key: row[key] for key in row.keys() if key != "task_id"}} for row in rows]

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
