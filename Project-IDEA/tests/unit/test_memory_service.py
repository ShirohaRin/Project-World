from __future__ import annotations

import pytest

from Memory.core import AuthorizedMemoryRequest, MemoryLayer
from Memory.service import MemoryService
from Memory.storage import MemoryLayout


def _request(*layers: MemoryLayer, raw: bool = False, container: str = "idea") -> AuthorizedMemoryRequest:
    return AuthorizedMemoryRequest(
        caller_id="caller-1",
        container_id=container,
        allowed_layers=frozenset(layers),
        allow_raw_content=raw,
    )


def test_service_recall_respects_allowed_layers_and_container(tmp_path):
    service = MemoryService(MemoryLayout(tmp_path))
    fact_request = _request(MemoryLayer.FACT)
    reflection_request = _request(MemoryLayer.REFLECTION)

    service.add_fact(fact_request, {"id": "fact-1", "text": "用户喜欢阅读科学小说"})
    service.reflections.synthesize("idea", "用户长期关注科学与小说", ["fact-1"])

    facts = service.recall(fact_request, "科学")
    assert [result.memory_id for result in facts] == ["fact-1"]
    assert service.recall(reflection_request, "科学")[0].memory_id.startswith("reflection_")
    assert service.recall(_request(MemoryLayer.FACT, container="other"), "科学") == []


def test_service_rejects_unauthorized_writes_and_keeps_staging_out_of_recall(tmp_path):
    service = MemoryService(MemoryLayout(tmp_path))
    recent_only = _request(MemoryLayer.RECENT)

    with pytest.raises(PermissionError):
        service.add_fact(recent_only, {"text": "不应写入"})

    fact_request = _request(MemoryLayer.FACT)
    service.add_fact(fact_request, {"id": "fact-1", "text": "云端上传暂存内容"})
    assert service.recall(fact_request, "上传")[0].memory_id == "fact-1"
    assert service.staging.pending_count("idea") == 1
    assert all("staging" not in result.memory_id for result in service.recall(fact_request, "上传"))


def test_service_raw_content_requires_explicit_authorization(tmp_path):
    service = MemoryService(MemoryLayout(tmp_path))
    owner_like = _request(MemoryLayer.FACT, raw=True)
    normal = _request(MemoryLayer.FACT)

    service.add_fact(owner_like, {
        "id": "raw-1",
        "text": "原始私密内容",
        "metadata": {"raw_content": True},
    })
    assert service.recall(normal, "私密") == []
    assert service.recall(owner_like, "私密")[0].memory_id == "raw-1"
