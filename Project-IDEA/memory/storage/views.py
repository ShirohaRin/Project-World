from __future__ import annotations

import asyncio
import json
from typing import Any

from .atomic import atomic_write_json
from .layout import MemoryLayout
from .locks import CharacterLocks


class JsonViewStore:
    """角色级 JSON 视图读写；学习和权限逻辑由上层负责。"""

    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()
        self._cache: dict[tuple[str, str], Any] = {}

    def load(self, character: str, filename: str, default: Any = None) -> Any:
        key = (character, filename)
        with self.locks.hold(character):
            if key in self._cache:
                return self._cache[key]
            path = self.layout.file(character, filename)
            try:
                value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                value = default
            self._cache[key] = value
            return value

    def save(self, character: str, filename: str, value: Any) -> None:
        with self.locks.hold(character):
            atomic_write_json(self.layout.file(character, filename), value)
            self._cache[(character, filename)] = value

    def invalidate(self, character: str, filename: str | None = None) -> None:
        if filename is None:
            for key in list(self._cache):
                if key[0] == character:
                    self._cache.pop(key)
        else:
            self._cache.pop((character, filename), None)

    async def aload(self, character: str, filename: str, default: Any = None) -> Any:
        return await asyncio.to_thread(self.load, character, filename, default)

    async def asave(self, character: str, filename: str, value: Any) -> None:
        await asyncio.to_thread(self.save, character, filename, value)
