from __future__ import annotations

import pytest

from Memory.core import AuthorizedMemoryRequest, MemoryLayer
from Memory.service import MemoryService
from Memory.storage import MemoryLayout


def request(*layers):
    return AuthorizedMemoryRequest("tester", "idea", frozenset(layers), allow_raw_content=True)


def test_recent_learning_exact_dedup_and_evidence_status(tmp_path):
    service = MemoryService(MemoryLayout(tmp_path))
    req = request(MemoryLayer.RECENT, MemoryLayer.FACT)
    service.append_recent(req, {"id": "m1", "role": "user", "content": "喜欢猫"})
    service.append_recent(req, {"id": "m2", "role": "user", "content": "  喜欢猫  "})
    ids = service.learn_recent(req)
    assert ids == [ids[0], ids[0]]
    assert len(service.facts.load("idea")) == 1
    assert service.update_fact_evidence(req, ids[0], {"reinforcement": 2}, source="user_fact")
    assert service.facts.load("idea")[0]["status"] == "promoted"


def test_reflection_requires_metadata_and_promoted_reflection_can_be_persona(tmp_path):
    service = MemoryService(MemoryLayout(tmp_path))
    req = request(MemoryLayer.REFLECTION, MemoryLayer.PERSONA)
    with pytest.raises(ValueError):
        service.synthesize_reflection(req, "偏好稳定协作", ["f1"])
    rid = service.synthesize_reflection(req, "偏好稳定协作", ["f1"], synthesis_version="v1", source_window="recent:10")
    service.reflections.set_status("idea", rid, "promoted")
    pid = service.promote_reflection_to_persona(req, rid)
    assert pid.startswith("persona_master_")
    assert service.persona.load("idea")["master"]["facts"][0]["source_reflection_id"] == rid
