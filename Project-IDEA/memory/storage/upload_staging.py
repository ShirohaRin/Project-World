from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .atomic import append_ndjson, read_ndjson
from .layout import MemoryLayout
from .locks import CharacterLocks

STAGING_FILENAME = "upload_staging.ndjson"


class UploadStaging:
    """断联期间的新记忆上传暂存。

    规则：
    - 云端明确确认（ack）后才清除对应记录；
    - 每条记录带 idempotency_key，相同键未确认前重复入队只返回原记录；
    - 暂存内容不参与普通记忆召回；
    - 生产环境应注入 encrypt_payload/decrypt_payload 启用加密保存。
    """

    def __init__(
        self,
        layout: MemoryLayout,
        locks: CharacterLocks | None = None,
        encrypt_payload: Callable[[Any], Any] | None = None,
        decrypt_payload: Callable[[Any], Any] | None = None,
    ) -> None:
        self.layout = layout
        self.locks = locks or CharacterLocks()
        self.encrypt_payload = encrypt_payload
        self.decrypt_payload = decrypt_payload

    def enqueue(self, character: str, idempotency_key: str, operation: str, payload: dict[str, Any]) -> str:
        with self.locks.hold(character):
            records = read_ndjson(self.layout.file(character, STAGING_FILENAME))
            for record in records:
                if record.get("idempotency_key") == idempotency_key and record.get("status") == "pending":
                    return str(record["staging_id"])
            staging_id = str(uuid.uuid4())
            body = self.encrypt_payload(payload) if self.encrypt_payload else payload
            record = {
                "staging_id": staging_id,
                "idempotency_key": idempotency_key,
                "operation": operation,
                "payload": body,
                "status": "pending",
                "attempts": 0,
                "created_at": _now(),
            }
            append_ndjson(self.layout.file(character, STAGING_FILENAME), record)
            return staging_id

    def pending(self, character: str) -> list[dict[str, Any]]:
        with self.locks.hold(character):
            records = read_ndjson(self.layout.file(character, STAGING_FILENAME))
        attempt_counts: dict[str, int] = {}
        pending_records: dict[str, dict[str, Any]] = {}
        for record in records:
            staging_id = record.get("staging_id")
            status = record.get("status")
            if status == "attempt":
                attempt_counts[staging_id] = attempt_counts.get(staging_id, 0) + 1
            elif status == "pending":
                pending_records[staging_id] = record
            elif status == "deadletter":
                pending_records.pop(staging_id, None)
        result = []
        for staging_id, record in pending_records.items():
            item = dict(record)
            item["attempts"] = attempt_counts.get(staging_id, 0)
            if self.decrypt_payload and isinstance(item.get("payload"), dict):
                item["payload"] = self.decrypt_payload(item["payload"])
            result.append(item)
        result.sort(key=lambda item: item.get("created_at", ""))
        return result

    def mark_attempt(self, character: str, staging_id: str) -> None:
        with self.locks.hold(character):
            append_ndjson(self.layout.file(character, STAGING_FILENAME), {
                "staging_id": staging_id, "status": "attempt", "ts": _now(),
            })

    def deadletter(self, character: str, staging_id: str) -> bool:
        """将仍处于 pending 的记录标记为 deadletter，之后不再参与上传。"""
        with self.locks.hold(character):
            path = self.layout.file(character, STAGING_FILENAME)
            records = read_ndjson(path)
            if not any(record.get("staging_id") == staging_id and record.get("status") == "pending" for record in records):
                return False
            remaining = [
                dict(record)
                for record in records
                if not (record.get("staging_id") == staging_id and record.get("status") == "pending")
            ]
            for record in records:
                if record.get("staging_id") == staging_id and record.get("status") == "pending":
                    archived = dict(record)
                    archived["status"] = "deadletter"
                    archived["deadlettered_at"] = _now()
                    remaining.append(archived)
            self._rewrite(path, remaining)
            return True

    def ack(self, character: str, staging_id: str) -> bool:
        """云端确认上传成功后清除暂存记录。返回是否找到并清除。"""
        with self.locks.hold(character):
            path = self.layout.file(character, STAGING_FILENAME)
            records = read_ndjson(path)
            if not any(record.get("staging_id") == staging_id and record.get("status") == "pending" for record in records):
                return False
            remaining = [dict(record) for record in records if record.get("staging_id") != staging_id]
            self._rewrite(path, remaining)
            return True

    def _rewrite(self, path, records: list[dict[str, Any]]) -> None:
        from .atomic import atomic_write_text
        text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
        atomic_write_text(path, text)

    def pending_count(self, character: str) -> int:
        return len(self.pending(character))

    async def aenqueue(self, character: str, idempotency_key: str, operation: str, payload: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.enqueue, character, idempotency_key, operation, payload)

    async def apending(self, character: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.pending, character)

    async def aack(self, character: str, staging_id: str) -> bool:
        return await asyncio.to_thread(self.ack, character, staging_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
