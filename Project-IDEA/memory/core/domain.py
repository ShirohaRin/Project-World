from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import hashlib
import json
import uuid


class MemoryLayer(StrEnum):
    JOURNAL = "journal"
    RECENT = "recent"
    FACT = "fact"
    REFLECTION = "reflection"
    PERSONA = "persona"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    PROMOTED = "promoted"
    MERGED = "merged"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True)
class AuthorizedMemoryRequest:
    caller_id: str
    container_id: str
    allowed_layers: frozenset[MemoryLayer] = frozenset(MemoryLayer)
    allow_raw_content: bool = False
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str | None = None

    def allows(self, layer: MemoryLayer) -> bool:
        return bool(self.caller_id.strip() and self.container_id.strip()) and layer in self.allowed_layers


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    container_id: str
    layer: MemoryLayer
    content: str
    status: MemoryStatus = MemoryStatus.CANDIDATE
    source_message_id: str | None = None
    source_conversation_id: str | None = None
    subject_ref: str | None = None
    importance: float = 0.5
    confidence: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revision: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.strip().encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "container_id": self.container_id,
            "layer": self.layer.value,
            "content": self.content,
            "status": self.status.value,
            "source_message_id": self.source_message_id,
            "source_conversation_id": self.source_conversation_id,
            "subject_ref": self.subject_ref,
            "importance": self.importance,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "revision": self.revision,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class MemoryEvidence:
    evidence_id: str
    target_memory_id: str
    kind: str
    reinforcement: float = 0.0
    disputation: float = 0.0
    source_message_id: str | None = None
    processor_version: str = "idea-memory.v1"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    event_type: str
    memory_id: str | None
    container_id: str
    payload: dict[str, Any]
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_json(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "memory_id": self.memory_id,
                "container_id": self.container_id,
                "payload": self.payload,
                "occurred_at": self.occurred_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
