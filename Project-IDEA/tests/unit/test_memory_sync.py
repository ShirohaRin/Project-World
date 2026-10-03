from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from Memory.storage import (
    CloudMemoryBackend,
    CloudSnapshot,
    LocalCache,
    MemoryLayout,
    SyncCoordinator,
    UploadStaging,
)


class FakeBackend:
    """可编程假云端：按需抛错、记录收到的推送。"""

    def __init__(self, snapshot_items=None, snapshot_revision="rev-1", fail_pull=False, fail_push_ids=()):
        self.snapshot_items = snapshot_items or []
        self.snapshot_revision = snapshot_revision
        self.fail_pull = fail_pull
        self.fail_push_ids = set(fail_push_ids)
        self.pushed: list[tuple[str, str, dict, str]] = []

    def pull_snapshot(self, character, since_revision):
        if self.fail_pull:
            raise RuntimeError("云端不可达")
        return CloudSnapshot(list(self.snapshot_items), self.snapshot_revision)

    def push_mutation(self, character, operation, payload, idempotency_key):
        if idempotency_key in self.fail_push_ids:
            raise RuntimeError("推送失败")
        self.pushed.append((character, operation, payload, idempotency_key))


def test_local_cache_replace_and_evict(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    cache = LocalCache(layout)
    cache.replace_from_cloud("idea", [
        {"memory_id": "m1", "layer": "fact", "text": "内容一"},
        {"memory_id": "m2", "layer": "fact", "text": "内容二"},
    ], "rev-1")
    assert cache.cloud_revision("idea") == "rev-1"
    assert cache.get("idea", "m1")["content"] == "内容一"

    # 相同 revision 不会重复写盘
    old_updated = cache.get("idea", "m2")["last_accessed_at"]
    cache.replace_from_cloud("idea", [
        {"memory_id": "m1", "layer": "fact", "text": "内容一"},
        {"memory_id": "m2", "layer": "fact", "text": "内容二"},
    ], "rev-1")
    assert cache.cloud_revision("idea") == "rev-1"

    # 模拟 m2 很久没被访问
    import json
    path = layout.file("idea", "local_cache.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["memory_id"] == "m2":
            item["last_accessed_at"] = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert cache.evict_untouched("idea", max_age_days=30) == 1
    assert cache.get("idea", "m2") is None
    assert cache.get("idea", "m1") is not None


def test_upload_staging_idempotent_and_ack(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    staging = UploadStaging(layout)
    first = staging.enqueue("idea", "key-1", "fact.create", {"text": "断联时的新记忆"})
    again = staging.enqueue("idea", "key-1", "fact.create", {"text": "断联时的新记忆"})
    assert first == again
    assert staging.pending_count("idea") == 1

    staging.mark_attempt("idea", first)
    assert staging.pending("idea")[0]["attempts"] == 1

    assert staging.ack("idea", first)
    assert staging.pending_count("idea") == 0
    assert not staging.ack("idea", first)


def test_sync_coordinator_down_up_and_retry(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    cache = LocalCache(layout)
    staging = UploadStaging(layout)
    backend = FakeBackend(
        snapshot_items=[{"memory_id": "m1", "layer": "fact", "text": "云端记忆"}],
        snapshot_revision="rev-9",
    )
    coordinator = SyncCoordinator(cache, staging, backend, max_attempts=2)

    staging_id = staging.enqueue("idea", "key-1", "fact.create", {"text": "断联记忆"})
    report = coordinator.sync("idea")
    assert report.pulled == 1
    assert report.uploaded == 1
    assert cache.cloud_revision("idea") == "rev-9"
    assert cache.get("idea", "m1") is not None
    assert staging.pending_count("idea") == 0
    assert backend.pushed[0][3] == "key-1"


def test_sync_coordinator_push_failure_then_deadletter(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    cache = LocalCache(layout)
    staging = UploadStaging(layout)
    backend = FakeBackend(fail_push_ids={"key-bad"})
    coordinator = SyncCoordinator(cache, staging, backend, max_attempts=2)

    staging.enqueue("idea", "key-bad", "fact.create", {"text": "会失败"})
    first = coordinator.sync_up("idea")
    assert first.uploaded == 0
    assert first.failed == 1
    assert staging.pending("idea")[0]["attempts"] == 1

    second = coordinator.sync_up("idea")
    assert second.uploaded == 0
    assert second.failed == 1
    assert staging.pending("idea")[0]["attempts"] == 2

    third = coordinator.sync_up("idea")
    assert third.uploaded == 0
    assert third.deadlettered == 1
    assert staging.pending_count("idea") == 0
