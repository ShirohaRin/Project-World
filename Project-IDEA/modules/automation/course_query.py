"""结构化课程查询服务。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from .course_loader import load_courses
from .course_reminder import Course


@dataclass(frozen=True)
class CourseOccurrence:
    course_id: str
    name: str
    date: str
    weekday: int
    week: int
    start_time: str
    end_time: str | None
    location: str
    memo: str

    def as_dict(self) -> dict:
        return asdict(self)


def _occurrence(course: Course, day: date) -> CourseOccurrence:
    return CourseOccurrence(
        course.id, course.name, day.isoformat(), course.weekday,
        (day - course.start_date).days // 7 + 1,
        course.start_time.strftime("%H:%M"),
        course.end_time.strftime("%H:%M") if course.end_time else None,
        course.location, course.memo,
    )


def _matches(course: Course, day: date, week: int | None) -> bool:
    if not course.enabled or not (course.start_date <= day <= course.end_date):
        return False
    if day.weekday() != course.weekday:
        return False
    actual_week = (day - course.start_date).days // 7 + 1
    return (course.weeks is None or actual_week in course.weeks) and (week is None or actual_week == week)


def _merge_adjacent(items: list[CourseOccurrence]) -> list[CourseOccurrence]:
    merged: list[CourseOccurrence] = []
    for item in items:
        if merged:
            previous = merged[-1]
            if (
                previous.course_id == item.course_id
                and previous.date == item.date
                and previous.location == item.location
                and previous.end_time is not None
                and previous.end_time == item.start_time
            ):
                merged[-1] = CourseOccurrence(
                    previous.course_id, previous.name, previous.date, previous.weekday,
                    previous.week, previous.start_time, item.end_time, previous.location,
                    previous.memo or item.memo,
                )
                continue
        merged.append(item)
    return merged


def query_courses(
    courses: list[Course],
    query_date: date | str | None = None,
    week: int | None = None,
) -> list[dict]:
    """按日期、周次查询课程；日期和周次同时提供时取交集。"""
    if isinstance(query_date, str):
        query_date = date.fromisoformat(query_date) if query_date else None
    if week is not None and week < 1:
        raise ValueError("week 必须是正整数")
    days = [query_date] if query_date else sorted({
        course.start_date + timedelta(days=offset)
        for course in courses
        for offset in range((course.end_date - course.start_date).days + 1)
        if week is None or (offset // 7 + 1) == week
    })
    items = [_occurrence(course, day) for day in days for course in courses if _matches(course, day, week)]
    items.sort(key=lambda item: (item.date, item.start_time, item.course_id))
    return [item.as_dict() for item in _merge_adjacent(items)]


def query_courses_from_path(path: str | Path, query_date: date | str | None = None, week: int | None = None) -> list[dict]:
    return query_courses(load_courses(path), query_date=query_date, week=week)
