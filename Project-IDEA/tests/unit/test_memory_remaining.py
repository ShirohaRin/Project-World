from __future__ import annotations

from pathlib import Path

from Memory.storage import FactStore, MemoryLayout, PersonaStore, RecentStore, ReflectionStore


def test_remaining_views_and_archive(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    facts = FactStore(layout)
    facts.add("idea", {"id": "f1", "text": "长期偏好"})
    assert facts.archive("idea", "f1")
    assert facts.load("idea") == []
    assert facts.load_full("idea")[0]["status"] == "archived"

    recent = RecentStore(layout)
    for index in range(21):
        recent.append("idea", {"role": "user", "content": str(index)})
    assert recent.needs_compression("idea")
    assert len(recent.window("idea", 3)) == 3

    reflections = ReflectionStore(layout)
    reflection_id = reflections.synthesize("idea", "偏好稳定协作", ["f1"])
    assert not reflections.set_status("idea", reflection_id, "pending")
    assert reflections.set_status("idea", reflection_id, "confirmed")

    persona = PersonaStore(layout)
    persona_id = persona.add_fact("idea", "偏好清晰方案")
    assert persona.update_fact("idea", "master", persona_id, confidence=0.9)
