import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
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

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

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
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)").fetchall()}
            if "email" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email) WHERE email IS NOT NULL")

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
                        "INSERT INTO owner_account_links(owner_principal_id, account_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(owner_principal_id, account_id) DO NOTHING",
                        ("owner-shiroha-nao", bootstrap_account["account_id"], now),
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

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate(self, token: str) -> Optional[Principal]:
        token_hash = self.hash_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.principal_id, p.account_id, p.role, t.token_id
                FROM access_tokens t
                JOIN principals p ON p.principal_id = t.principal_id
                JOIN accounts a ON a.account_id = p.account_id
                WHERE t.token_hash = ? AND t.status = 'active'
                  AND p.status = 'active' AND a.status = 'active'
                  AND (t.expires_at IS NULL OR t.expires_at > ?)
                """,
                (token_hash, time.time()),
            ).fetchone()
            if not row:
                return None
            connection.execute("UPDATE access_tokens SET last_used_at = ? WHERE token_id = ?", (time.time(), row["token_id"]))
            return Principal(row["principal_id"], row["account_id"], row["role"], row["token_id"])

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

    @staticmethod
    def _append_sync_event(connection, account_id: str, space_id: str, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict) -> None:
        connection.execute("INSERT INTO sync_events(account_id, space_id, aggregate_type, aggregate_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (account_id, space_id, aggregate_type, aggregate_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()))

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
        now = time.time()
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(48)
        session_id = f"session-{uuid.uuid4().hex}"
        token_id = f"token-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("INSERT INTO access_tokens(token_id, principal_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)", (token_id, principal.principal_id, self.hash_token(access_token), now + access_ttl_seconds, now))
            connection.execute("INSERT INTO account_sessions(session_id, account_id, principal_id, device_id, refresh_token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (session_id, principal.account_id, principal.principal_id, device_id, self.hash_token(refresh_token), now + refresh_ttl_seconds, now))
        return {"access_token": access_token, "access_expires_at": now + access_ttl_seconds, "refresh_token": refresh_token, "session_id": session_id}

    def refresh_session(self, refresh_token: str, device_id: Optional[str]) -> tuple[Principal, dict]:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.*, p.role FROM account_sessions s JOIN principals p ON p.principal_id = s.principal_id WHERE s.refresh_token_hash = ? AND s.status = 'active' AND s.expires_at > ?",
                (self.hash_token(refresh_token), now),
            ).fetchone()
            if not row or (row["device_id"] and device_id and row["device_id"] != device_id):
                raise ValueError("登录会话已失效")
            connection.execute("UPDATE account_sessions SET status = 'rotated', revoked_at = ?, last_used_at = ? WHERE session_id = ?", (now, now, row["session_id"]))
            principal = Principal(row["principal_id"], row["account_id"], row["role"], "")
        return principal, self.issue_session(principal, device_id)

    def revoke_session(self, refresh_token: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE account_sessions SET status = 'revoked', revoked_at = ? WHERE refresh_token_hash = ?", (time.time(), self.hash_token(refresh_token)))

    def route_for_principal(self, principal: Principal) -> str:
        with self._connect() as connection:
            linked = connection.execute(
                "SELECT 1 FROM owner_account_links l JOIN owner_principals o ON o.owner_principal_id = l.owner_principal_id WHERE l.account_id = ? AND l.status = 'active' AND o.status = 'active'",
                (principal.account_id,),
            ).fetchone()
            return "owner_idea" if linked else "idea_assistant"

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
