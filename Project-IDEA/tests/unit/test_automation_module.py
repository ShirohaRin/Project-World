"""自动化模块的最小行为测试。"""

from datetime import date, datetime, time

from modules.automation.course_reminder import Course, format_reminder, reminder_for, reminder_key, reminders_due
from modules.automation.course_query import query_courses
from modules.automation.rainclass_monitor import RainClassItem, format_notification


def course() -> Course:
    return Course("math-1", "高等数学", "A-302", 0, time(10, 0), date(2026, 8, 31), date(2026, 12, 31), memo="带作业本")


def test_course_query_filters_date_and_merges_adjacent_same_course() -> None:
    first = course()
    second = Course("math-1", "高等数学", "A-302", 0, time(11, 0), first.start_date, first.end_date, end_time=time(12, 0))
    result = query_courses([first, second], query_date="2026-08-31")
    assert len(result) == 1
    assert result[0]["start_time"] == "10:00"
    assert result[0]["end_time"] == "12:00"


def test_course_reminder_and_idempotency_key() -> None:
    reminder = reminder_for(course(), date(2026, 8, 31))
    assert reminder is not None
    assert reminder.remind_at == datetime(2026, 8, 31, 9, 30)
    assert reminder_key(reminder) == "math-1:2026-08-31:30"
    assert "备忘录：带作业本" in format_reminder(reminder)


def test_reminders_due_window_covers_catch_up_until_class_start() -> None:
    # 09:30 是提醒时点；09:31 仍在窗口内，发送失败可重试
    assert len(reminders_due([course()], datetime(2026, 8, 31, 9, 30))) == 1
    assert len(reminders_due([course()], datetime(2026, 8, 31, 9, 31))) == 1
    # 09:29 尚未到提醒时点；10:00 已开课，均不发送
    assert reminders_due([course()], datetime(2026, 8, 31, 9, 29)) == []
    assert reminders_due([course()], datetime(2026, 8, 31, 10, 0)) == []


def test_rainclass_notification_format() -> None:
    text = format_notification(RainClassItem("a-1", "course-1", "作业", "第三章作业", due_time="2026-09-03 23:59"))
    assert "第三章作业" in text
    assert "截止时间：2026-09-03 23:59" in text
