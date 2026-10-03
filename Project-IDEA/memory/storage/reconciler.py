from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .event_log import EventLog


class Reconciler:
    """按事件日志顺序将未应用事件交给幂等处理器。"""

    def __init__(self, event_log: EventLog, handlers: dict[str, Callable[[str, dict[str, Any]], None]] | None = None) -> None:
        self.event_log = event_log
        self.handlers = handlers or {}

    def register(self, event_type: str, handler: Callable[[str, dict[str, Any]], None]) -> None:
        self.handlers[event_type] = handler

    def reconcile(self, character: str) -> int:
        applied = self.event_log.read_sentinel(character)
        records = self.event_log.read_since(character, applied)
        count = 0
        for record in records:
            event_id = record.get("event_id")
            event_type = record.get("type")
            handler = self.handlers.get(event_type)
            if handler is not None:
                handler(character, record.get("payload", {}))
            self.event_log.advance_sentinel(character, event_id)
            count += 1
        return count
