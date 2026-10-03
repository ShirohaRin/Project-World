from __future__ import annotations

from typing import Any

from .event_log import EventLog
from .layout import MemoryLayout
from .outbox import Outbox
from .views import JsonViewStore


class MemoryViews:
    """统一暴露 NEKO 风格的角色 JSON 视图。"""

    def __init__(self, layout: MemoryLayout, views: JsonViewStore | None = None) -> None:
        self.views = views or JsonViewStore(layout)

    def load_recent(self, character: str) -> Any:
        return self.views.load(character, "recent.json", [])

    def save_recent(self, character: str, messages: list[dict[str, Any]]) -> None:
        self.views.save(character, "recent.json", messages)

    def load_reflections(self, character: str) -> list[dict[str, Any]]:
        value = self.views.load(character, "reflections.json", [])
        return value if isinstance(value, list) else []

    def save_reflections(self, character: str, reflections: list[dict[str, Any]]) -> None:
        self.views.save(character, "reflections.json", reflections)

    def load_persona(self, character: str) -> dict[str, Any]:
        value = self.views.load(character, "persona.json", {})
        return value if isinstance(value, dict) else {}

    def save_persona(self, character: str, persona: dict[str, Any]) -> None:
        self.views.save(character, "persona.json", persona)

    def load_settings(self, character: str) -> dict[str, Any]:
        value = self.views.load(character, "settings.json", {})
        return value if isinstance(value, dict) else {}

    def save_settings(self, character: str, settings: dict[str, Any]) -> None:
        self.views.save(character, "settings.json", settings)
