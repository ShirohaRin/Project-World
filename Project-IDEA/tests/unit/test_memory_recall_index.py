from __future__ import annotations

from pathlib import Path

from Memory.storage import (
    FactStore, MemoryLayout, TimeIndex, build_dedup_pairs, cosine_similarity,
    find_exact_duplicate, token_overlap,
)


def test_dedup_and_time_index(tmp_path: Path):
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert token_overlap("用户喜欢猫", "用户喜欢猫咪") > 0
    existing = [{"id": "old", "text": "用户喜欢猫"}]
    fact = {"id": "new", "text": "用户喜欢猫"}
    assert find_exact_duplicate(fact, existing)["id"] == "old"
    assert build_dedup_pairs(fact, existing) == []

    layout = MemoryLayout(tmp_path)
    index = TimeIndex(layout)
    index.upsert("idea", "f1", "fact", "2026-01-01T00:00:00+00:00")
    index.upsert("idea", "f2", "fact", "2026-02-01T00:00:00+00:00")
    assert index.search("idea", after="2026-01-15T00:00:00+00:00")[0]["memory_id"] == "f2"
