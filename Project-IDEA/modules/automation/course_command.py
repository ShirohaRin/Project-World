"""固定格式的课程查询命令，不调用大模型。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from .course_query import query_courses_from_path

WEEK_START = date(2026, 8, 31)
WEEKDAYS = "一二三四五六日"
PERIODS = (
    (1, "08:20", "10:00"),
    (2, "10:20", "12:00"),
    (3, "13:30", "15:10"),
    (4, "15:25", "17:05"),
    (5, "18:30", "20:10"),
)
PERIOD_ALIASES = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}


def _resolve_date(text: str, today: date) -> date | None:
    if match := re.search(r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?", text):
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if "今天" in text:
        return today
    if "明天" in text:
        return today + timedelta(days=1)
    if "后天" in text:
        return today + timedelta(days=2)
    match = re.search(r"(下周|本周|这周|周|星期)([一二三四五六日天1-7])", text)
    if not match:
        return today
    weekday_text = match.group(2)
    weekday = 6 if weekday_text in {"日", "天", "7"} else int(weekday_text) - 1 if weekday_text.isdigit() else WEEKDAYS.index(weekday_text)
    monday = today - timedelta(days=today.weekday())
    if match.group(1) == "下周":
        monday += timedelta(days=7)
    return monday + timedelta(days=weekday)


def _period_filter(text: str) -> tuple[int, int] | None:
    if match := re.search(r"第?([一二三四五12345])(?:到|至|-|—)([一二三四五12345])节", text):
        return PERIOD_ALIASES[match.group(1)], PERIOD_ALIASES[match.group(2)]
    if match := re.search(r"第?([一二三四五12345])节", text):
        period = PERIOD_ALIASES[match.group(1)]
        return period, period
    if "上午" in text:
        return 1, 2
    if "下午" in text:
        return 3, 4
    if "晚上" in text or "夜间" in text:
        return 5, 5
    return None


def _period_number(start_time: str) -> int:
    for number, start, end in PERIODS:
        if start_time == start:
            return number
    for number, start, end in PERIODS:
        if start_time < end:
            return number
    return 5


def _format(items: list[dict], target_date: date, period_range: tuple[int, int] | None) -> str:
    title = f"{target_date.isoformat()}（周{WEEKDAYS[target_date.weekday()]}）"
    if period_range:
        title += f" 第{period_range[0]}-{period_range[1]}节"
    if not items:
        return f"{title}没有安排课程。"
    lines = [f"{title}课程："]
    for item in items:
        period = _period_number(item["start_time"])
        lines.append(
            f"第{period}节 {item['start_time']}-{item.get('end_time') or '未知'}："
            f"{item['name']}｜{item['location']}"
            + (f"｜备忘录：{item['memo']}" if item.get("memo") else "")
        )
    return "\n".join(lines)


def query_course_command(command: str, schedule_path: str, today: date | None = None) -> str:
    """解析固定课程命令并返回文本结果。"""
    text = re.sub(r"[？?。！!]", "", command.strip())
    if text in {"帮助", "菜单", "课程帮助", "课程查询帮助"}:
        return (
            "课程查询命令：\n"
            "今天有啥课\n"
            "明天下午有什么课\n"
            "下周一上午第一节什么课\n"
            "2026-09-10第3-4节什么课\n"
            "可用日期：今天、明天、后天、本周/下周+周几或 YYYY-MM-DD；"
            "可用时段：上午、下午、晚上、第N节、第N-N节。"
        )
    if not any(word in text for word in ("课", "课程", "上课", "安排")):
        return "我现在只处理课程查询。发送“课程帮助”查看可用命令。"
    today = today or date.today()
    target_date = _resolve_date(text, today)
    if target_date is None:
        return "日期格式无法识别。请使用“今天/明天/下周一”或“YYYY-MM-DD”。"
    period_range = _period_filter(text)
    try:
        items = query_courses_from_path(schedule_path, query_date=target_date)
    except Exception as error:
        return f"课程表读取失败：{error}"
    if period_range:
        items = [
            item for item in items
            if period_range[0] <= _period_number(item["start_time"]) <= period_range[1]
        ]
    return _format(items, target_date, period_range)
