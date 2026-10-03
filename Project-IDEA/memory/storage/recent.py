from __future__ import annotations

from typing import Any

from .layout import MemoryLayout
from .memory_views import MemoryViews

RECENT_HISTORY_MAX_ITEMS = 10
RECENT_COMPRESS_THRESHOLD_ITEMS = 20
RECENT_HARD_CAP_ITEMS = 100


class RecentStore:
    """近期上下文视图；摘要由上层模型生成，本层只负责边界与持久化。"""

    def __init__(self, layout: MemoryLayout, views: MemoryViews | None = None) -> None:
        self.views = views or MemoryViews(layout)

    def load(self, character: str) -> list[dict[str, Any]]:
        value = self.views.load_recent(character)
        return value if isinstance(value, list) else []

    def append(self, character: str, message: dict[str, Any]) -> list[dict[str, Any]]:
        messages = self.load(character)
        messages.append(dict(message))
        if len(messages) > RECENT_HARD_CAP_ITEMS:
            messages = messages[-RECENT_HARD_CAP_ITEMS:]
        self.views.save_recent(character, messages)
        return messages

    def replace(self, character: str, messages: list[dict[str, Any]]) -> None:
        self.views.save_recent(character, messages[-RECENT_HARD_CAP_ITEMS:])

    def window(self, character: str, limit: int = RECENT_HISTORY_MAX_ITEMS) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("近期记忆窗口必须至少保留一条消息")
        return self.load(character)[-limit:]

    def needs_compression(self, character: str) -> bool:
        return len(self.load(character)) >= RECENT_COMPRESS_THRESHOLD_ITEMS
