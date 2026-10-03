from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .atomic import atomic_write_json
from .layout import MemoryLayout
from .locks import CharacterLocks

CACHE_FILENAME = "local_cache.json"
CACHE_CAPACITY = 500


class LocalCache:
    """云端记忆的本地只读缓存。

    职责边界：
    - 只保存云端下发的内容，不做学习、合并、晋升、归档或权限判断；
    - 内容跟随云端 revision 整体替换；
    - 读取会刷新 ``last_accessed_at``，用于长期未用的自动遗忘；
    - 淘汰只影响本地缓存，绝不回写云端。
    """

    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None, capacity: int = CACHE_CAPACITY) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()
        self.capacity = max(1, capacity)

    def _load(self, character: str) -> dict[str, Any]:
        path = self.layout.file(character, CACHE_FILENAME)
        try:
            value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            value = None
        if not isinstance(value, dict):
            value = {"cloud_revision": None, "items": []}
        items = value.get("items")
        if not isinstance(items, list):
            value["items"] = []
        return value

    def replace_from_cloud(self, character: str, items: list[dict[str, Any]], cloud_revision: str | None) -> None:
        """用云端快照替换本地缓存。仅当 revision 变更时才真正写盘。"""
        now_iso = _now()
        normalized = []
        seen: set[str] = set()
        for item in items:
            memory_id = str(item.get("memory_id") or item.get("id") or "")
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            normalized.append({
                "memory_id": memory_id,
                "layer": str(item.get("layer") or "fact"),
                "content": str(item.get("content") or item.get("text") or ""),
                "cloud_revision": cloud_revision,
                "last_accessed_at": now_iso,
            })
        with self.locks.hold(character):
            current = self._load(character)
            if current.get("cloud_revision") == cloud_revision and cloud_revision is not None:
                return
            payload = {"cloud_revision": cloud_revision, "items": normalized[-self.capacity:]}
            atomic_write_json(self.layout.file(character, CACHE_FILENAME), payload)

    def get(self, character: str, memory_id: str) -> dict[str, Any] | None:
        with self.locks.hold(character):
            current = self._load(character)
            for item in current.get("items", []):
                if item.get("memory_id") == memory_id:
                    item["last_accessed_at"] = _now()
                    atomic_write_json(self.layout.file(character, CACHE_FILENAME), current)
                    return dict(item)
        return None

    def all(self, character: str) -> list[dict[str, Any]]:
        with self.locks.hold(character):
            return [dict(item) for item in self._load(character).get("items", [])]

    def evict_untouched(self, character: str, *, max_age_days: float) -> int:
        """淘汰长期未被访问的缓存项；返回淘汰数量。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with self.locks.hold(character):
            current = self._load(character)
            before = len(current.get("items", []))
            remaining = []
            for item in current.get("items", []):
                last = item.get("last_accessed_at")
                try:
                    parsed = datetime.fromisoformat(last) if last else None
                except (TypeError, ValueError):
                    parsed = None
                if parsed is None or parsed >= cutoff:
                    remaining.append(item)
            current["items"] = remaining
            atomic_write_json(self.layout.file(character, CACHE_FILENAME), current)
            return before - len(remaining)

    def cloud_revision(self, character: str) -> str | None:
        with self.locks.hold(character):
            return self._load(character).get("cloud_revision")

    async def areplace_from_cloud(self, character: str, items: list[dict[str, Any]], cloud_revision: str | None) -> None:
        await asyncio.to_thread(self.replace_from_cloud, character, items, cloud_revision)

    async def aget(self, character: str, memory_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.get, character, memory_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
