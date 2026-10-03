"""检查点存储：课程提醒幂等与雨课堂内容去重共用。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class MemoryCheckpointStore:
    """进程内检查点，适合测试和短任务。"""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def known(self, key: str) -> bool:
        return key in self._keys

    async def remember(self, key: str) -> None:
        self._keys.add(key)


class SqliteCheckpointStore:
    """持久化检查点，云端重启后不会重复发送。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, created_at REAL NOT NULL)")
            connection.commit()
        finally:
            connection.close()

    async def known(self, key: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute("SELECT 1 FROM checkpoints WHERE key = ?", (key,)).fetchone()
            return row is not None
        finally:
            connection.close()

    async def remember(self, key: str) -> None:
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO checkpoints(key, created_at) VALUES (?, ?)", (key, time.time()))
            connection.commit()
        finally:
            connection.close()
