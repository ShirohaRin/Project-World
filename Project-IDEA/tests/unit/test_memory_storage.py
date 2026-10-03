from __future__ import annotations

import json
import tempfile
from pathlib import Path

from Memory.storage import CursorStore, EventLog, MemoryLayout, Outbox


def test_character_layout_and_path_safety(tmp_path: Path):
    layout = MemoryLayout(tmp_path / "memory")
    layout.initialize_character("idea")
    assert layout.character("idea").name == "idea"
    assert (layout.character("idea") / "facts.json").exists()
    try:
        layout.character("../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("角色路径穿越未被拒绝")


def test_outbox_replays_only_unfinished_operations(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    outbox = Outbox(layout)
    op_id = outbox.append_pending("idea", "extract_facts", {"message_id": "m1"})
    outbox.append_attempt("idea", op_id)
    assert outbox.pending_ops("idea")[0]["_attempt_count"] == 1
    outbox.append_done("idea", op_id)
    assert outbox.pending_ops("idea") == []


def test_cursor_preserves_other_keys(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    cursors = CursorStore(layout)
    from datetime import datetime, timezone
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = datetime(2026, 1, 2, tzinfo=timezone.utc)
    cursors.set_cursor("idea", "a", first)
    cursors.set_cursor("idea", "b", second)
    assert cursors.get_cursor("idea", "a") == first
    assert cursors.get_cursor("idea", "b") == second


def test_event_log_rejects_unknown_and_reads_tail(tmp_path: Path):
    layout = MemoryLayout(tmp_path)
    events = EventLog(layout)
    event_id = events.append("idea", "fact.added", {"content": "记忆"})
    assert events.read_since("idea")[0]["event_id"] == event_id
    assert events.read_since("idea", event_id) == []
    try:
        events.append("idea", "unknown", {})
    except ValueError:
        pass
    else:
        raise AssertionError("未知事件未被拒绝")
