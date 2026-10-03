"""课程提醒领域逻辑。只负责根据结构化课程表计算提醒，不依赖调度器或 QQ。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    location: str
    weekday: int
    start_time: time
    start_date: date
    end_date: date
    end_time: time | None = None
    weeks: frozenset[int] | None = None
    memo: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class CourseReminder:
    course: Course
    class_date: date
    remind_at: datetime


def reminder_for(course: Course, day: date, lead_minutes: int = 30) -> CourseReminder | None:
    if not course.enabled or day < course.start_date or day > course.end_date or day.weekday() != course.weekday:
        return None
    week = (day - course.start_date).days // 7 + 1
    if course.weeks is not None and week not in course.weeks:
        return None
    starts_at = datetime.combine(day, course.start_time)
    return CourseReminder(course, day, starts_at - timedelta(minutes=lead_minutes))


def reminder_key(reminder: CourseReminder) -> str:
    return f"{reminder.course.id}:{reminder.class_date.isoformat()}:30"


def reminders_due(courses: list[Course], now: datetime, lead_minutes: int = 30) -> list[CourseReminder]:
    """返回当前应发送的提醒。

    窗口是 [remind_at, 上课时刻)：提醒时点一到即可发送；若发送失败，下一轮调度
    （每 60 秒）会自动重试，直到开课为止，不会因为错过某一分钟而永久漏发。
    开课之后不再补发。
    """
    due: list[CourseReminder] = []
    for course in courses:
        reminder = reminder_for(course, now.date(), lead_minutes)
        if reminder is None:
            continue
        starts_at = datetime.combine(now.date(), course.start_time)
        if reminder.remind_at <= now < starts_at:
            due.append(reminder)
    return due


def format_reminder(reminder: CourseReminder) -> str:
    course = reminder.course
    lines = ["【课程提醒】", f"课程：{course.name}", f"时间：{reminder.class_date.isoformat()} {course.start_time.strftime('%H:%M')}-{course.end_time.strftime('%H:%M') if course.end_time else '未知'}", f"地点：{course.location}"]
    if course.memo.strip():
        lines.append(f"备忘录：{course.memo.strip()}")
    return "\n".join(lines)
