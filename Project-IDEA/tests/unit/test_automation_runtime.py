"""自动化运行时测试：检查点、课程加载、雨课堂文本解析、云端工具注册。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio  # noqa: E402

from modules.automation.checkpoint_store import MemoryCheckpointStore, SqliteCheckpointStore  # noqa: E402
from modules.automation.course_loader import load_courses  # noqa: E402
from modules.automation.rainclass_collector import parse_rainclass_text  # noqa: E402
from modules.automation.runner import run_course_reminders  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_memory_checkpoint_deduplicates() -> None:
    store = MemoryCheckpointStore()
    _run(store.remember("key-a"))
    assert _run(store.known("key-a"))
    assert not _run(store.known("key-b"))


def test_sqlite_checkpoint_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteCheckpointStore(Path(tmp) / "checkpoints.db")
        _run(store.remember("rain:course:1"))
        assert _run(store.known("rain:course:1"))
        reopened = SqliteCheckpointStore(Path(tmp) / "checkpoints.db")
        assert _run(reopened.known("rain:course:1"))


def test_course_loader_parses_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "courses.json"
        path.write_text(
            """{"courses": [{"id": "math-1", "name": "高等数学", "location": "A-302", "weekday": 0,
            "start_time": "10:00", "start_date": "2026-08-31", "end_date": "2026-12-31",
            "weeks": [1, 3], "memo": "带作业本", "enabled": true}]}""",
            encoding="utf-8",
        )
        courses = load_courses(path)
        assert len(courses) == 1
        assert courses[0].name == "高等数学"
        assert courses[0].weeks == frozenset({1, 3})


def test_parse_rainclass_text_extracts_announcement() -> None:
    text = "课程动态\n作业：第三章课后作业\n截止时间：2026-09-03 23:59\n要求：完成第3章1-8题\n公告：补课通知\n"
    items = parse_rainclass_text(text, "course-1")
    assert len(items) == 2
    assert items[0].item_type == "assignment"
    assert items[0].title == "第三章课后作业"
    assert items[0].due_time == "2026-09-03 23:59"
    assert items[1].item_type == "announcement"


def test_course_reminder_runner_sends_once() -> None:
    sent: list[str] = []

    async def notifier(content: str) -> None:
        sent.append(content)

    class _Notifier:
        async def send(self, content: str) -> None:
            sent.append(content)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "courses.json"
        path.write_text(
            """{"courses": [{"id": "math-1", "name": "高等数学", "location": "A-302", "weekday": 0,
            "start_time": "10:00", "start_date": "2026-08-31", "end_date": "2026-12-31", "enabled": true}]}""",
            encoding="utf-8",
        )
        from datetime import datetime

        store = MemoryCheckpointStore()
        _run(run_course_reminders(path, store, _Notifier(), now=datetime(2026, 8, 31, 9, 30)))
        _run(run_course_reminders(path, store, _Notifier(), now=datetime(2026, 8, 31, 9, 31)))
        assert len(sent) == 1


def test_server_automation_tools_register() -> None:
    sys.path.insert(0, str(PROJECT_ROOT / "server"))
    from platform_auth import Principal, RequestContext  # noqa: F401
    from tool_runtime.permissions import ExecutionContext  # noqa: F401
    from tool_runtime.registry import ToolRegistry  # noqa: F401
    from automation_tools import register_automation_tools  # noqa: E402

    registry = ToolRegistry(workspace=str(PROJECT_ROOT), allowed_dirs=[str(PROJECT_ROOT)])
    register_automation_tools(registry, {"automation": {"enabled": False}})
    assert "automation.course_query" in registry.get_all_tool_names()
    assert "automation.course_reminder" in registry.get_all_tool_names()
    assert "automation.rainclass_monitor" in registry.get_all_tool_names()

    owner_context = ExecutionContext(
        RequestContext("req-test", Principal("p-owner", "acct-owner", "owner", "token"), None, "space-1"),
        "idea",
        is_owner=True,
    )
    # 未启用时工具返回明确提示，而非伪造成功
    result = _run(registry.execute("automation.course_reminder", {}, owner_context))
    assert not result.success
    assert "未启用" in result.output
