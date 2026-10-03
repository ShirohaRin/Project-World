from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from Memory.core.evidence import compute_evidence_snapshot, derive_status
from .dedup import exact_fact_key
from .event_log import EVT_FACT_ADDED, EVT_FACT_ARCHIVED, EVT_FACT_EVIDENCE_UPDATED, EventLog
from .layout import MemoryLayout
from .outbox import Outbox
from .views import JsonViewStore


class FactStore:
    """Facts 视图的最小持久化管理器；抽取、去重和学习由上层实现。"""

    def __init__(self, layout: MemoryLayout, views: JsonViewStore | None = None, events: EventLog | None = None, outbox: Outbox | None = None) -> None:
        self.views = views or JsonViewStore(layout)
        self.events = events or EventLog(layout)
        self.outbox = outbox or Outbox(layout)

    def load(self, character: str) -> list[dict[str, Any]]:
        value = self.views.load(character, "facts.json", [])
        return value if isinstance(value, list) else []

    def load_full(self, character: str) -> list[dict[str, Any]]:
        active = self.load(character)
        archived = self.views.load(character, "facts_archive.json", [])
        if not isinstance(archived, list):
            return active
        active_ids = {row.get("id") for row in active if isinstance(row, dict) and row.get("id") is not None}
        return active + [row for row in archived if not isinstance(row, dict) or row.get("id") not in active_ids]

    def save(self, character: str, facts: list[dict[str, Any]]) -> None:
        self.views.save(character, "facts.json", facts)

    def add(self, character: str, fact: dict[str, Any]) -> str:
        facts = self.load(character)
        content_key = exact_fact_key(str(fact.get("text", "")))
        duplicate = next((row for row in facts if exact_fact_key(str(row.get("text", ""))) == content_key), None)
        if duplicate is not None:
            return str(duplicate.get("id"))
        fact_id = str(fact.get("id") or self.events.append(character, EVT_FACT_ADDED, fact))
        if not any(row.get("id") == fact_id for row in facts if isinstance(row, dict)):
            facts.append({**fact, "id": fact_id})
            self.save(character, facts)
        return fact_id

    def update_evidence(self, character: str, fact_id: str, delta: dict[str, Any], *, source: str = "system") -> bool:
        facts = self.load(character)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        for fact in facts:
            if fact.get("id") != fact_id:
                continue
            fact.update(compute_evidence_snapshot(fact, delta, now_iso, source))
            fact["status"] = derive_status(fact, now)
            fact["updated_at"] = now_iso
            self.save(character, facts)
            self.events.append(character, EVT_FACT_EVIDENCE_UPDATED, {"fact_id": fact_id, "delta": delta, "source": source, "status": fact["status"]})
            return True
        return False

    def archive(self, character: str, fact_id: str) -> bool:
        active = self.load(character)
        target = next((fact for fact in active if fact.get("id") == fact_id), None)
        if target is None:
            return False
        archived = self.views.load(character, "facts_archive.json", [])
        archived = archived if isinstance(archived, list) else []
        if not any(fact.get("id") == fact_id for fact in archived if isinstance(fact, dict)):
            archived.append({**target, "status": "archived"})
        self.views.save(character, "facts_archive.json", archived)
        self.save(character, [fact for fact in active if fact.get("id") != fact_id])
        self.events.append(character, EVT_FACT_ARCHIVED, {"fact_id": fact_id})
        return True

    def queue_extraction(self, character: str, payload: dict[str, Any]) -> str:
        return self.outbox.append_pending(character, "extract_facts", payload)
