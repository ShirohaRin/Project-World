from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from .atomic import append_ndjson, read_ndjson
from .layout import MemoryLayout
from .locks import CharacterLocks


class Outbox:
    def __init__(self, layout: MemoryLayout, locks: CharacterLocks | None = None) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()

    def append_pending(self, character: str, op_type: str, payload: dict[str, Any]) -> str:
        op_id = str(uuid.uuid4())
        record = {"op_id": op_id, "type": op_type, "payload": payload, "status": "pending", "ts": _now()}
        with self.locks.hold(character):
            append_ndjson(self.layout.file(character, "outbox.ndjson"), record)
        return op_id

    def append_done(self, character: str, op_id: str) -> None:
        self._append_status(character, op_id, "done")

    def append_attempt(self, character: str, op_id: str) -> None:
        self._append_status(character, op_id, "attempt")

    def pending_ops(self, character: str) -> list[dict[str, Any]]:
        with self.locks.hold(character):
            records = read_ndjson(self.layout.file(character, "outbox.ndjson"))
        pending: dict[str, dict[str, Any]] = {}
        attempts: dict[str, int] = {}
        for record in records:
            op_id, status = record.get("op_id"), record.get("status")
            if not op_id:
                continue
            if status == "pending":
                pending[op_id] = dict(record)
            elif status == "attempt":
                attempts[op_id] = attempts.get(op_id, 0) + 1
            elif status == "done":
                pending.pop(op_id, None)
                attempts.pop(op_id, None)
        for op_id, record in pending.items():
            record["_attempt_count"] = attempts.get(op_id, 0)
        return list(pending.values())

    def _append_status(self, character: str, op_id: str, status: str) -> None:
        with self.locks.hold(character):
            append_ndjson(self.layout.file(character, "outbox.ndjson"), {"op_id": op_id, "status": status, "ts": _now()})

    async def aappend_pending(self, character: str, op_type: str, payload: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.append_pending, character, op_type, payload)

    async def aappend_done(self, character: str, op_id: str) -> None:
        await asyncio.to_thread(self.append_done, character, op_id)

    async def apending_ops(self, character: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.pending_ops, character)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
