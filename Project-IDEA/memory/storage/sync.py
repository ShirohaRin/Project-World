from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from .local_cache import LocalCache
from .upload_staging import UploadStaging

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BATCH_SIZE = 20


class CloudSyncError(RuntimeError):
    """云端推送/拉取失败。"""


@dataclass(frozen=True)
class CloudSnapshot:
    items: list[dict[str, Any]]
    revision: str | None = None


class CloudMemoryBackend(Protocol):
    """云端记忆权威源的适配接口。

    实现方负责实际的鉴权、网络和错误映射：
    - ``pull_snapshot`` 抛出异常视为拉取失败；
    - ``push_mutation`` 抛出异常视为该条推送失败（可重试）；
    - 上传接口必须按 idempotency_key 幂等。
    """

    def pull_snapshot(self, character: str, since_revision: str | None) -> CloudSnapshot: ...

    def push_mutation(self, character: str, operation: str, payload: dict[str, Any], idempotency_key: str) -> None: ...


@dataclass
class SyncReport:
    pulled: int = 0
    uploaded: int = 0
    deadlettered: int = 0
    failed: int = 0
    evicted: int = 0
    errors: list[str] = field(default_factory=list)


class SyncCoordinator:
    """云端权威源的同步协调器。

    - ``sync_down``：拉取云端快照并替换本地只读缓存；
    - ``sync_up``：把断联暂存逐条推送云端，成功后 ack 清除，超限转死信；
    - ``sync``：先下后上，返回汇总报告。
    """

    def __init__(
        self,
        cache: LocalCache,
        staging: UploadStaging,
        backend: CloudMemoryBackend,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.cache = cache
        self.staging = staging
        self.backend = backend
        self.max_attempts = max(1, max_attempts)
        self.batch_size = max(1, batch_size)

    def sync_down(self, character: str) -> SyncReport:
        report = SyncReport()
        try:
            snapshot = self.backend.pull_snapshot(character, self.cache.cloud_revision(character))
        except Exception as exc:
            report.failed += 1
            report.errors.append(f"pull: {exc}")
            return report
        items = [item for item in snapshot.items if isinstance(item, dict)]
        self.cache.replace_from_cloud(character, items, snapshot.revision)
        report.pulled = len(items)
        return report

    def sync_up(self, character: str) -> SyncReport:
        report = SyncReport()
        pending = self.staging.pending(character)
        for record in pending[: self.batch_size]:
            staging_id = str(record["staging_id"])
            if record.get("attempts", 0) >= self.max_attempts:
                if self.staging.deadletter(character, staging_id):
                    report.deadlettered += 1
                continue
            self.staging.mark_attempt(character, staging_id)
            try:
                self.backend.push_mutation(
                    character,
                    str(record.get("operation", "memory.mutate")),
                    record.get("payload") or {},
                    str(record.get("idempotency_key") or staging_id),
                )
            except Exception as exc:
                report.failed += 1
                report.errors.append(f"push {staging_id}: {exc}")
                continue
            if self.staging.ack(character, staging_id):
                report.uploaded += 1
        return report

    def sync(self, character: str) -> SyncReport:
        report = self.sync_down(character)
        upload = self.sync_up(character)
        report.uploaded += upload.uploaded
        report.deadlettered += upload.deadlettered
        report.failed += upload.failed
        report.errors.extend(upload.errors)
        return report

    def evict_stale(self, character: str, *, max_age_days: float) -> int:
        count = self.cache.evict_untouched(character, max_age_days=max_age_days)
        return count

    async def async_sync(self, character: str) -> SyncReport:
        return await asyncio.to_thread(self.sync, character)

    async def async_sync_down(self, character: str) -> SyncReport:
        return await asyncio.to_thread(self.sync_down, character)

    async def async_sync_up(self, character: str) -> SyncReport:
        return await asyncio.to_thread(self.sync_up, character)
