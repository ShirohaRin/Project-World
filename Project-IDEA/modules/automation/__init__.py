"""自动化模块的最小运行入口。云端调度器调用业务函数；本地不启动常驻进程。"""

from .checkpoint_store import MemoryCheckpointStore, SqliteCheckpointStore
from .course_loader import course_from_dict, load_courses
from .course_query import CourseOccurrence, query_courses, query_courses_from_path
from .course_reminder import Course, CourseReminder, format_reminder, reminder_for, reminder_key, reminders_due
from .course_reminder_service import send_due_course_reminders
from .rainclass_collector import RainClassConfig, RainClassPage, parse_rainclass_text
from .rainclass_monitor import RainClassItem, check_rainclass, format_notification
from .runner import run_course_reminders, run_rainclass_monitor

__all__ = [
    "Course", "CourseReminder", "MemoryCheckpointStore", "RainClassConfig", "RainClassItem",
    "RainClassPage", "SqliteCheckpointStore", "check_rainclass", "course_from_dict",
    "format_notification", "format_reminder", "load_courses", "parse_rainclass_text",
    "reminder_for", "reminder_key", "reminders_due", "run_course_reminders",
    "run_rainclass_monitor", "run_rainclass_ppt_monitor", "send_due_course_reminders",
    "MarkdownNoteWriter", "PptPageState", "RainClassPptConfig", "RainClassSlide",
]
