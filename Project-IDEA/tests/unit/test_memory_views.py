from __future__ import annotations

from pathlib import Path

from Memory.storage import MemoryLayout, PersonaStore, RecentStore, ReflectionStore


def test_recent_reflection_persona_views(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    recent = RecentStore(layout)
    for index in range(12):
        recent.append("idea", {"role": "user", "content": str(index)})
    assert len(recent.window("idea")) == 10

    reflections = ReflectionStore(layout)
    reflection_id = reflections.synthesize("idea", "用户偏好稳定的科研协作", ["f1", "f2"])
    assert reflections.set_status("idea", reflection_id, "confirmed")
    assert reflections.load("idea")[0]["status"] == "confirmed"

    persona = PersonaStore(layout)
    fact_id = persona.add_fact("idea", "偏好清晰的技术方案")
    assert persona.add_fact("idea", "偏好清晰的技术方案") == fact_id
    assert persona.update_fact("idea", "master", fact_id, confidence=0.9)
