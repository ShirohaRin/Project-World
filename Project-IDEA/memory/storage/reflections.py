from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from Memory.core.evidence import compute_evidence_snapshot, derive_status
from .event_log import EVT_REFLECTION_EVIDENCE_UPDATED, EVT_REFLECTION_STATE_CHANGED, EVT_REFLECTION_SYNTHESIZED, EventLog
from .layout import MemoryLayout
from .memory_views import MemoryViews


class ReflectionStore:
    def __init__(self, layout: MemoryLayout, views: MemoryViews | None = None, events: EventLog | None = None) -> None:
        self.views = views or MemoryViews(layout)
        self.events = events or EventLog(layout)

    def load(self, character: str) -> list[dict[str, Any]]:
        return self.views.load_reflections(character)

    def save(self, character: str, reflections: list[dict[str, Any]]) -> None:
        self.views.save_reflections(character, reflections)

    def synthesize(self, character: str, text: str, source_fact_ids: list[str], **metadata: Any) -> str:
        digest = hashlib.sha256("\0".join(sorted(source_fact_ids)).encode()).hexdigest()[:24]
        reflection_id = f"reflection_{digest}"
        reflections = self.load(character)
        existing = next((item for item in reflections if item.get("id") == reflection_id), None)
        if existing is None:
            existing = {
                "id": reflection_id, "text": text, "source_fact_ids": list(source_fact_ids),
                "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(), **metadata,
            }
            reflections.append(existing)
            self.save(character, reflections)
            self.events.append(character, EVT_REFLECTION_SYNTHESIZED, {"reflection_id": reflection_id})
        return reflection_id

    def update_evidence(self, character: str, reflection_id: str, delta: dict[str, Any], *, source: str = "system") -> bool:
        reflections = self.load(character)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        for reflection in reflections:
            if reflection.get("id") != reflection_id:
                continue
            reflection.update(compute_evidence_snapshot(reflection, delta, now_iso, source))
            reflection["status"] = derive_status(reflection, now)
            reflection["updated_at"] = now_iso
            self.save(character, reflections)
            self.events.append(character, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": reflection_id, "status": reflection["status"]})
            return True
        return False
    def set_status(self, character: str, reflection_id: str, status: str) -> bool:
        reflections = self.load(character)
        for reflection in reflections:
            if reflection.get("id") == reflection_id:
                if reflection.get("status") == status:
                    return False
                old_status = reflection.get("status")
                reflection["status"] = status
                reflection["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.save(character, reflections)
                self.events.append(character, EVT_REFLECTION_STATE_CHANGED, {
                    "reflection_id": reflection_id, "from": old_status, "to": status,
                })
                return True
        return False
