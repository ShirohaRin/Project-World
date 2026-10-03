from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .atomic import append_ndjson, atomic_write_json, read_ndjson
from .layout import MemoryLayout
from .locks import CharacterLocks

EVT_FACT_ADDED = "fact.added"
EVT_FACT_ABSORBED = "fact.absorbed"
EVT_FACT_ARCHIVED = "fact.archived"
EVT_FACT_EVIDENCE_UPDATED = "fact.evidence_updated"
EVT_REFLECTION_SYNTHESIZED = "reflection.synthesized"
EVT_REFLECTION_STATE_CHANGED = "reflection.state_changed"
EVT_REFLECTION_SURFACED = "reflection.surfaced"
EVT_REFLECTION_REBUTTED = "reflection.rebutted"
EVT_PERSONA_FACT_ADDED = "persona.fact_added"
EVT_PERSONA_FACT_MENTIONED = "persona.fact_mentioned"
EVT_PERSONA_SUPPRESSED = "persona.suppressed"
EVT_CORRECTION_QUEUED = "correction.queued"
EVT_CORRECTION_RESOLVED = "correction.resolved"
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"
EVT_PERSONA_EVIDENCE_UPDATED = "persona.evidence_updated"
EVT_PERSONA_ENTRY_UPDATED = "persona.entry_updated"

ALL_EVENT_TYPES = frozenset({
    EVT_FACT_ADDED, EVT_FACT_ABSORBED, EVT_FACT_ARCHIVED, EVT_FACT_EVIDENCE_UPDATED,
    EVT_REFLECTION_SYNTHESIZED, EVT_REFLECTION_STATE_CHANGED,
    EVT_REFLECTION_SURFACED, EVT_REFLECTION_REBUTTED,
    EVT_PERSONA_FACT_ADDED, EVT_PERSONA_FACT_MENTIONED, EVT_PERSONA_SUPPRESSED,
    EVT_CORRECTION_QUEUED, EVT_CORRECTION_RESOLVED,
    EVT_REFLECTION_EVIDENCE_UPDATED, EVT_PERSONA_EVIDENCE_UPDATED,
    EVT_PERSONA_ENTRY_UPDATED,
})


class EventLog:
    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()

    def append(self, character: str, event_type: str, payload: dict[str, Any]) -> str:
        if event_type not in ALL_EVENT_TYPES:
            raise ValueError(f"未知事件类型: {event_type}")
        event_id = str(uuid.uuid4())
        with self.locks.hold(character):
            append_ndjson(self.layout.file(character, "events.ndjson"), {
                "event_id": event_id, "type": event_type, "ts": _now(), "payload": payload,
            })
        return event_id

    def read_since(self, character: str, after_event_id: str | None = None) -> list[dict[str, Any]]:
        with self.locks.hold(character):
            records = read_ndjson(self.layout.file(character, "events.ndjson"))
        if after_event_id is None:
            return records
        for index, record in enumerate(records):
            if record.get("event_id") == after_event_id:
                return records[index + 1:]
        return records

    def read_sentinel(self, character: str) -> str | None:
        path = self.layout.file(character, "events_applied.json")
        try:
            value = path.read_text(encoding="utf-8")
            data = json.loads(value)
        except (OSError, ValueError):
            return None
        return data.get("last_applied_event_id") if isinstance(data, dict) else None

    def advance_sentinel(self, character: str, event_id: str | None) -> None:
        with self.locks.hold(character):
            atomic_write_json(self.layout.file(character, "events_applied.json"), {
                "last_applied_event_id": event_id, "ts": _now(),
            })

    async def aappend(self, character: str, event_type: str, payload: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.append, character, event_type, payload)

    async def aread_since(self, character: str, after_event_id: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.read_since, character, after_event_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
