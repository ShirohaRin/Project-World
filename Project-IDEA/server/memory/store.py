"""
共享记忆库 — 为 IDEA 体系提供跨会话持久化存储
支持 SQLite（关键词检索）和 ChromaDB（向量语义检索）
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


class MemoryStore:
    def __init__(self, backend: str = "sqlite", db_path: str = "./memory/idea_memory.db"):
        self.backend = backend
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT
            )
        """)
        self.conn.commit()

    def store(self, key: str, value: str, category: str = "general"):
        now = time.time()
        self.conn.execute(
            """INSERT OR REPLACE INTO memory (key, value, category, created_at, updated_at)
               VALUES (?, ?, ?, COALESCE((SELECT created_at FROM memory WHERE key=?), ?), ?)""",
            (key, value, category, key, now, now),
        )
        self.conn.commit()

    def search(self, query: str, top_k: int = 5) -> dict:
        cursor = self.conn.execute(
            "SELECT key, value, category FROM memory WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", top_k),
        )
        results = [{"key": r[0], "value": r[1], "category": r[2]} for r in cursor.fetchall()]
        return {"query": query, "results": results, "count": len(results)}

    def get(self, key: str) -> Optional[dict]:
        cursor = self.conn.execute("SELECT key, value, category FROM memory WHERE key=?", (key,))
        row = cursor.fetchone()
        return {"key": row[0], "value": row[1], "category": row[2]} if row else None

    def log_audit(self, agent: str, action: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO audit_log (timestamp, agent, action, detail) VALUES (?, ?, ?, ?)",
            (time.time(), agent, action, detail),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
