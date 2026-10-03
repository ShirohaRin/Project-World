from __future__ import annotations

from typing import Any, Callable
from datetime import datetime, timezone

from .core import AuthorizedMemoryRequest, MemoryLayer, MemoryStatus, RecallResult, hybrid_recall, render_recall_block
from .core.evidence import derive_status
from .storage.dedup import exact_fact_key
from .storage import (
    CloudMemoryBackend,
    EventLog,
    FactStore,
    LocalCache,
    MemoryLayout,
    MemoryViews,
    PersonaStore,
    RecentStore,
    ReflectionStore,
    SyncCoordinator,
    SyncReport,
    TimeIndex,
    UploadStaging,
)


class MemoryService:
    """Memory 模块的统一门面；Token 和平台权限由调用方后端负责。"""

    def __init__(
        self,
        layout: MemoryLayout,
        cloud_backend: CloudMemoryBackend | None = None,
        *,
        embed: Callable[[str], list[float] | None] | None = None,
    ) -> None:
        self.layout = layout
        self.views = MemoryViews(layout)
        self.events = EventLog(layout)
        self.facts = FactStore(layout, events=self.events)
        self.recent = RecentStore(layout, views=self.views)
        self.reflections = ReflectionStore(layout, views=self.views, events=self.events)
        self.persona = PersonaStore(layout, views=self.views, events=self.events)
        self.time_index = TimeIndex(layout)
        self.cache = LocalCache(layout)
        self.staging = UploadStaging(layout)
        self.syncer = (
            SyncCoordinator(self.cache, self.staging, cloud_backend)
            if cloud_backend is not None
            else None
        )
        self.embed = embed

    def _character(self, request: AuthorizedMemoryRequest) -> str:
        if not request.caller_id.strip() or not request.container_id.strip():
            raise PermissionError("Memory 请求缺少调用方或容器标识")
        self.layout.initialize_character(request.container_id)
        return request.container_id

    def _require_layer(self, request: AuthorizedMemoryRequest, layer: MemoryLayer) -> str:
        character = self._character(request)
        if not request.allows(layer):
            raise PermissionError(f"请求不允许访问 {layer.value} 层记忆")
        return character

    def _entries(self, request: AuthorizedMemoryRequest) -> list[dict[str, Any]]:
        character = self._character(request)
        entries: list[dict[str, Any]] = []
        if request.allows(MemoryLayer.FACT):
            entries.extend(self._normalize_entries(self.facts.load(character), MemoryLayer.FACT))
        if request.allows(MemoryLayer.REFLECTION):
            entries.extend(self._normalize_entries(self.reflections.load(character), MemoryLayer.REFLECTION))
        if request.allows(MemoryLayer.PERSONA):
            persona = self.persona.load(character)
            for entity, section in persona.items():
                if not isinstance(section, dict):
                    continue
                for fact in section.get("facts", []):
                    if isinstance(fact, dict):
                        item = dict(fact)
                        item["id"] = str(item.get("id") or f"persona_{entity}")
                        item["layer"] = MemoryLayer.PERSONA.value
                        item["subject_ref"] = entity
                        entries.append(item)
        if not request.allow_raw_content:
            entries = [
                entry for entry in entries
                if not entry.get("metadata", {}).get("raw_content", False)
            ]
        return entries

    @staticmethod
    def _normalize_entries(entries: list[dict[str, Any]], layer: MemoryLayer) -> list[dict[str, Any]]:
        normalized = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item["id"] = str(item.get("id") or item.get("memory_id") or "")
            item["layer"] = layer.value
            normalized.append(item)
        return normalized

    def add_fact(self, request: AuthorizedMemoryRequest, fact: dict[str, Any]) -> str:
        character = self._require_layer(request, MemoryLayer.FACT)
        if not isinstance(fact, dict):
            raise TypeError("Fact 必须是对象")
        content = str(fact.get("text") or fact.get("content") or "").strip()
        if not content:
            raise ValueError("Fact 内容不能为空")
        payload = dict(fact)
        payload["text"] = content
        payload["container_id"] = character
        fact_id = self.facts.add(character, payload)
        self.staging.enqueue(
            character,
            request.idempotency_key or f"{request.request_id}:fact:{fact_id}",
            "fact.create",
            {"id": fact_id, **payload},
        )
        return fact_id

    def learn_recent(self, request: AuthorizedMemoryRequest, *, limit: int = 10) -> list[str]:
        character = self._require_layer(request, MemoryLayer.RECENT)
        self._require_layer(request, MemoryLayer.FACT)
        learned: list[str] = []
        for message in self.recent.window(character, limit):
            if not isinstance(message, dict) or message.get("role") not in {"user", "human"}:
                continue
            text = str(message.get("content") or message.get("text") or "").strip()
            if not text:
                continue
            fact_id = self.add_fact(request, {"text": text, "source_message_id": message.get("id"), "metadata": {"extracted_from_recent": True}})
            learned.append(fact_id)
        return learned

    def update_fact_evidence(self, request: AuthorizedMemoryRequest, fact_id: str, delta: dict[str, Any], *, source: str = "system") -> bool:
        character = self._require_layer(request, MemoryLayer.FACT)
        return self.facts.update_evidence(character, fact_id, delta, source=source)

    def synthesize_reflection(self, request: AuthorizedMemoryRequest, text: str, source_fact_ids: list[str], **metadata: Any) -> str:
        character = self._require_layer(request, MemoryLayer.REFLECTION)
        if not source_fact_ids or not metadata.get("synthesis_version") or not metadata.get("source_window"):
            raise ValueError("Reflection 必须带 synthesis_version 和 source_window 元数据")
        return self.reflections.synthesize(character, text, source_fact_ids, **metadata)

    def promote_reflection_to_persona(self, request: AuthorizedMemoryRequest, reflection_id: str, entity: str = "master") -> str:
        character = self._require_layer(request, MemoryLayer.PERSONA)
        self._require_layer(request, MemoryLayer.REFLECTION)
        reflection = next((item for item in self.reflections.load(character) if item.get("id") == reflection_id), None)
        if reflection is None or reflection.get("status") != MemoryStatus.PROMOTED.value:
            raise ValueError("只有 promoted Reflection 才能晋升 Persona")
        return self.persona.add_fact(character, str(reflection.get("text", "")), entity, source_reflection_id=reflection_id)

    def recall(
        self,
        request: AuthorizedMemoryRequest,
        query: str,
        *,
        limit: int = 8,
    ) -> list[RecallResult]:
        self._character(request)
        if not isinstance(query, str) or not query.strip():
            return []
        results = hybrid_recall(
            query,
            self._entries(request),
            embed=self.embed,
            budget_total=max(1, limit),
        )
        return results

    def render_recall(
        self,
        request: AuthorizedMemoryRequest,
        query: str,
        *,
        limit: int = 8,
    ) -> str:
        return render_recall_block([result.as_dict() for result in self.recall(request, query, limit=limit)])

    def synchronize(self, request: AuthorizedMemoryRequest) -> SyncReport:
        character = self._character(request)
        if self.syncer is None:
            return SyncReport()
        return self.syncer.sync(character)

    def recent_window(self, request: AuthorizedMemoryRequest, limit: int = 10) -> list[dict[str, Any]]:
        character = self._require_layer(request, MemoryLayer.RECENT)
        return self.recent.window(character, limit)

    def append_recent(self, request: AuthorizedMemoryRequest, message: dict[str, Any]) -> list[dict[str, Any]]:
        character = self._require_layer(request, MemoryLayer.RECENT)
        if not isinstance(message, dict):
            raise TypeError("近期消息必须是对象")
        return self.recent.append(character, message)

    def add_persona_fact(self, request: AuthorizedMemoryRequest, text: str, entity: str = "master", **metadata: Any) -> str:
        character = self._require_layer(request, MemoryLayer.PERSONA)
        if not request.allow_raw_content:
            raise PermissionError("写入 Persona 原始内容需要明确授权")
        return self.persona.add_fact(character, text, entity, **metadata)

    def evict_cache(self, request: AuthorizedMemoryRequest, *, max_age_days: float) -> int:
        self._character(request)
        return self.cache.evict_untouched(request.container_id, max_age_days=max_age_days)


__all__ = ["MemoryService"]
