from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .layout import MemoryLayout
from .locks import CharacterLocks


class TimeIndex:
    """每角色 SQLite 时间索引；原始记忆仍以 JSON 视图为权威。"""

    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()

    def _connect(self, character: str) -> sqlite3.Connection:
        connection = sqlite3.connect(self.layout.file(character, "time_indexed.db"))
        connection.execute("CREATE TABLE IF NOT EXISTS memory_time (memory_id TEXT PRIMARY KEY, layer TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_memory_time_created ON memory_time(created_at)")
        return connection

    def upsert(self, character: str, memory_id: str, layer: str, created_at: str, updated_at: str | None = None) -> None:
        updated_at = updated_at or created_at
        with self.locks.hold(character):
            connection = self._connect(character)
            try:
                with connection:
                    connection.execute("INSERT INTO memory_time(memory_id, layer, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(memory_id) DO UPDATE SET layer=excluded.layer, created_at=excluded.created_at, updated_at=excluded.updated_at", (memory_id, layer, created_at, updated_at))
            finally:
                connection.close()

    def search(self, character: str, *, layer: str | None = None, before: str | None = None, after: str | None = None, limit: int = 100) -> list[dict[str, str]]:
        if limit < 1:
            return []
        clauses, params = [], []
        if layer:
            clauses.append("layer = ?"); params.append(layer)
        if before:
            clauses.append("created_at < ?"); params.append(before)
        if after:
            clauses.append("created_at > ?"); params.append(after)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.locks.hold(character):
            connection = self._connect(character)
            try:
                rows = connection.execute(f"SELECT memory_id, layer, created_at, updated_at FROM memory_time {where} ORDER BY created_at DESC LIMIT ?", (*params, limit)).fetchall()
            finally:
                connection.close()
        return [{"memory_id": row[0], "layer": row[1], "created_at": row[2], "updated_at": row[3]} for row in rows]
