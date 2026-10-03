"""课程表加载器：JSON → Course。

示例格式：
{
  "courses": [
    {
      "id": "math-1", "name": "高等数学", "location": "A-302",
      "weekday": 0, "start_time": "10:00",
      "start_date": "2026-08-31", "end_date": "2026-12-31",
      "weeks": [1, 3, 5], "memo": "带作业本", "enabled": true
    }
  ]
}
"""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

from .course_reminder import Course


def load_courses(path: str | Path) -> list[Course]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    courses = data.get("courses", data) if isinstance(data, dict) else data
    return [course_from_dict(item) for item in courses]


def course_from_dict(item: dict) -> Course:
    weeks = frozenset(int(week) for week in item["weeks"]) if item.get("weeks") else None
    return Course(
        id=str(item["id"]),
        name=str(item["name"]),
        location=str(item.get("location", "")),
        weekday=int(item["weekday"]),
        start_time=time.fromisoformat(item["start_time"]),
        start_date=date.fromisoformat(item["start_date"]),
        end_date=date.fromisoformat(item["end_date"]),
        end_time=time.fromisoformat(item["end_time"]) if item.get("end_time") else None,
        weeks=weeks,
        memo=str(item.get("memo", "")),
        enabled=bool(item.get("enabled", True)),
    )
