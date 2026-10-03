"""自动化运行编排：云端调度器按需调用的入口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..browser import BrowserTool
from .checkpoint_store import MemoryCheckpointStore, SqliteCheckpointStore
from .course_loader import load_courses
from .course_reminder_service import send_due_course_reminders
from .rainclass_collector import RainClassConfig, RainClassPage
from .rainclass_monitor import RainClassItem, check_rainclass
from .rainclass_ppt import RainClassPptConfig, run_rainclass_ppt_monitor as _run_rainclass_ppt_monitor
from .rainclass_ppt_notes import MarkdownNoteWriter, RainClassSlide

__all__ = [
    "MemoryCheckpointStore", "RainClassConfig", "RainClassPage", "SqliteCheckpointStore",
    "run_course_reminders", "run_rainclass_monitor", "run_rainclass_ppt_monitor",
]


async def run_course_reminders(
    course_path: str | Path,
    checkpoint: MemoryCheckpointStore | SqliteCheckpointStore,
    notifier,
    now: datetime | None = None,
    lead_minutes: int = 30,
) -> list:
    """发送当前时间点到期的课程提醒；成功发送的记录写入检查点。"""
    courses = load_courses(course_path)
    return await send_due_course_reminders(courses, now or datetime.now(), checkpoint, notifier, lead_minutes)


async def run_rainclass_monitor(
    browser: BrowserTool,
    config: RainClassConfig,
    checkpoint: MemoryCheckpointStore | SqliteCheckpointStore,
    notifier,
) -> list[RainClassItem]:
    """采集雨课堂页面内容并增量通知新增条目。"""
    page = RainClassPage(browser, config)
    return await check_rainclass(page, checkpoint, notifier)


async def run_rainclass_ppt_monitor(
    browser: BrowserTool,
    config: RainClassPptConfig,
    checkpoint: MemoryCheckpointStore | SqliteCheckpointStore,
    note_writer: MarkdownNoteWriter,
) -> list[RainClassSlide]:
    """持续监控雨课堂图片型 PPT，并将新页面写入 Markdown。"""
    return await _run_rainclass_ppt_monitor(browser, config, checkpoint, note_writer)
