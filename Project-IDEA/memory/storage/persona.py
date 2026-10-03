from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .event_log import EVT_PERSONA_ENTRY_UPDATED, EVT_PERSONA_FACT_ADDED, EventLog
from .layout import MemoryLayout
from .memory_views import MemoryViews


class PersonaStore:
    def __init__(self, layout: MemoryLayout, views: MemoryViews | None = None, events: EventLog | None = None) -> None:
        self.views = views or MemoryViews(layout)
        self.events = events or EventLog(layout)

    def load(self, character: str) -> dict[str, Any]:
        return self.views.load_persona(character)

    def save(self, character: str, persona: dict[str, Any]) -> None:
        self.views.save_persona(character, persona)

    def add_fact(self, character: str, text: str, entity: str = "master", **metadata: Any) -> str:
        persona = self.load(character)
        section = persona.setdefault(entity, {"facts": []})
        facts = section.setdefault("facts", [])
        for fact in facts:
            if fact.get("text") == text:
                return str(fact["id"])
        fact_id = f"persona_{entity}_{len(facts) + 1}"
        facts.append({"id": fact_id, "text": text, "created_at": datetime.now(timezone.utc).isoformat(), **metadata})
        self.save(character, persona)
        self.events.append(character, EVT_PERSONA_FACT_ADDED, {"entity": entity, "fact_id": fact_id})
        return fact_id

    def update_fact(self, character: str, entity: str, fact_id: str, **changes: Any) -> bool:
        persona = self.load(character)
        facts = persona.get(entity, {}).get("facts", [])
        for fact in facts:
            if fact.get("id") == fact_id:
                fact.update(changes)
                fact["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.save(character, persona)
                self.events.append(character, EVT_PERSONA_ENTRY_UPDATED, {"entity": entity, "fact_id": fact_id})
                return True
        return False
