from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from .atomic import atomic_write_json
from .layout import MemoryLayout
from .locks import CharacterLocks

CURSOR_REBUTTAL_CHECKED_UNTIL = "rebuttal_checked_until"
CURSOR_EXTRACTED_UNTIL = "extracted_until"


class CursorStore:
    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()
        self._cache: dict[str, dict[str, datetime]] = {}

    def get_cursor(self, character: str, key: str) -> datetime | None:
        with self.locks.hold(character):
            return self._load(character).get(key)

    def set_cursor(self, character: str, key: str, value: datetime) -> None:
        with self.locks.hold(character):
            data = self._load(character)
            serialized = {k: v.isoformat() for k, v in data.items() if k != key}
            serialized[key] = value.isoformat()
            atomic_write_json(self.layout.file(character, "cursors.json"), serialized)
            data[key] = value

    def _load(self, character: str) -> dict[str, datetime]:
        if character in self._cache:
            return self._cache[character]
        path = self.layout.file(character, "cursors.json")
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        data: dict[str, datetime] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, str):
                    try:
                        data[key] = datetime.fromisoformat(value)
                    except ValueError:
                        continue
        self._cache[character] = data
        return data

    async def aget_cursor(self, character: str, key: str) -> datetime | None:
        return await asyncio.to_thread(self.get_cursor, character, key)

    async def aset_cursor(self, character: str, key: str, value: datetime) -> None:
        await asyncio.to_thread(self.set_cursor, character, key, value)
