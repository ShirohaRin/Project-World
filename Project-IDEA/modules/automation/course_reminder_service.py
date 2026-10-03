"""课程提醒任务服务。

调度器只需提供当前时间、课程数据、幂等存储和通知适配器；本地不会启动常驻进程。
"""

from __future__ import annotations

from typing import Protocol
from datetime import datetime

from .course_reminder import Course, CourseReminder, format_reminder, reminder_key, reminders_due


class ReminderCheckpointStore(Protocol):
    async def known(self, key: str) -> bool: ...
    async def remember(self, key: str) -> None: ...


class ReminderNotifier(Protocol):
    async def send(self, content: str) -> None: ...


async def send_due_course_reminders(
    courses: list[Course],
    now: datetime,
    checkpoints: ReminderCheckpointStore,
    notifier: ReminderNotifier,
    lead_minutes: int = 30,
) -> list[CourseReminder]:
    sent: list[CourseReminder] = []
    failures: list[str] = []
    for reminder in reminders_due(courses, now, lead_minutes):
        key = reminder_key(reminder)
        if await checkpoints.known(key):
            continue
        try:
            await notifier.send(format_reminder(reminder))
        except Exception as error:
            # 单条发送失败不阻断其他课程；不写 checkpoint，下一轮调度会自动重试。
            failures.append(f"{reminder.course.name}: {error}")
            continue
        await checkpoints.remember(key)
        sent.append(reminder)
    if failures:
        raise RuntimeError("部分课程提醒发送失败：" + "；".join(failures))
    return sent
