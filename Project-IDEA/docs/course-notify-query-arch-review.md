# 课程提醒 + 固定命令查询：代码审阅文档

> 生成时间：2026-09-03
> 范围：QQ「上课前 30 分钟通知」+「固定命令课程查询」，**全程不接入 LLM**。
> 用途：供白羽奈绪审阅当前架构，定位结构性问题。

---

## 1. 两条链路总览

### 链路 A：课程提醒（定时 → 主动推送，无用户交互）

```text
系统时钟（每 60 秒）
   │
   ▼
JobScheduler (server/jobs.py，进程内 asyncio 循环)
   │ 读 scheduled_jobs 表里到期的任务
   ▼
automation.course_reminder (server/automation_tools.py 注册的工具)
   │ 读 config.yaml 拿课程表路径 / QQ 端点
   ▼
runner.run_course_reminders (modules/automation/runner.py)
   │ 读 course_schedule.json
   ▼
course_reminder_service.send_due_course_reminders
   │ 计算“距上课正好 30 分钟”的课 → 检查去重 → 发送 → 记 checkpoint
   ▼
OneBotQQProvider.send_text → POST http://127.0.0.1:3000/send_private_msg
   ▼
NapCat → 你的 QQ (3080713452)
```

关键设计：发送成功后写 checkpoint（SQLite），保证同一条提醒只发一次；只有服务 60 秒一个周期内恰好命中提前 30 分钟的课程才会发。

### 链路 B：固定命令查询（QQ 消息 → 脚本解析 → 回复）

```text
你在 QQ 私聊发：“明天下午有什么课”
   │
   ▼
NapCat HTTP 回调 POST /onebot/event (NapCat → IDEA Server)
   │ 校验 X-OneBot-Secret / Authorization: Bearer
   │ 校验发送者 user_id == 3080713452
   ▼
main.py onebot_event
   │ raw_message 原样传给纯脚本解析器（不经过任何 LLM / Agent）
   ▼
query_course_command (modules/automation/course_command.py)
   │ 正则解析 日期 + 时段/节次 → 查 course_schedule.json
   ▼
OneBotQQProvider.send_text → POST /send_private_msg
   ▼
你的 QQ 收到纯文本回复
```

设计要点：入站消息不进入 AgentRunner，不消耗 API Key，固定命令集全部由正则 + 默认值覆盖。

---

## 2. 文件清单与职责

| 层 | 文件 | 职责 | 是否常驻进程 |
|---|---|---|---|
| 服务端入口 | `server/main.py` | FastAPI（8900），含 `/onebot/event` 入站、job 调度 API | 常驻（云端） |
| 调度 | `server/jobs.py` | 周期扫描 `scheduled_jobs` 表并执行工具 | 常驻（云端） |
| 工具注册 | `server/automation_tools.py` | 把 `automation.course_reminder` 等注册成可调度工具 | 依赖注册 |
| 领域 | `modules/automation/course_reminder.py` | `Course` 模型 + 提前 30 分钟计算 + 提醒文本 | 纯函数 |
| 加载 | `modules/automation/course_loader.py` | JSON 课程表 → `Course` | 纯函数 |
| 查询 | `modules/automation/course_query.py` | 日期/周次匹配、相邻同课合并 | 纯函数 |
| 命令解析 | `modules/automation/course_command.py` | 把自然短句解析成“日期+节次范围”再查 | 纯函数 |
| 提醒服务 | `modules/automation/course_reminder_service.py` | 到期 → 去重 → 发送 → 记 checkpoint | 被调用 |
| 编排 | `modules/automation/runner.py` | 提醒/雨课堂两个任务的统一入口 | 被调用 |
| 去重 | `modules/automation/checkpoint_store.py` | 内存 / SQLite 两种 checkpoint | 被调用 |
| 外部服务 | `modules/external_services/qq.py` | `QQMessage` + 发送协议 | 协议 |
| 外部服务 | `modules/external_services/onebot_qq.py` | OneBot v11 实际 HTTP 发送 | 被调用 |
| 配置 | `server/config.yaml` | automation 段：课程表路径、QQ 端点/token/目标 | — |
| 数据 | `server/automation/course_schedule.json` | 本学期全部课程 | 静态数据 |

> 注：`modules/automation/` 下还有 `rainclass_*`（雨课堂采集/监控），本轮**未启用**，本文件不展开；但它们挂在同一个 `runner.py` / `checkpoint_store.py` 上，见 §7 疑点。

---

## 3. 领域层：课程模型与提醒计算

### 3.1 `modules/automation/course_reminder.py`

只做“数学”，不碰调度、不碰 QQ、不碰文件。`Course` 是全部自动化功能共享的核心模型（查询、提醒、雨课堂复用同一份）。

```python
"""课程提醒领域逻辑。只负责根据结构化课程表计算提醒，不依赖调度器或 QQ。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    location: str
    weekday: int                # 周一=0 … 周日=6
    start_time: time
    start_date: date            # 该课开始生效的日期
    end_date: date              # 该课失效日期
    end_time: time | None = None
    weeks: frozenset[int] | None = None   # None=每周都上；否则只在这些教学周上
    memo: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class CourseReminder:
    course: Course
    class_date: date
    remind_at: datetime         # 真正要发提醒的时刻 = 上课时刻 - lead


def reminder_for(course: Course, day: date, lead_minutes: int = 30) -> CourseReminder | None:
    """判断某课程在某天是否需要提醒；lead 分钟提前，如 08:20 的课在 07:50 提醒。"""
    if not course.enabled or day < course.start_date or day > course.end_date or day.weekday() != course.weekday:
        return None
    week = (day - course.start_date).days // 7 + 1     # 教学周 = 相对开课日的自然周
    if course.weeks is not None and week not in course.weeks:
        return None
    starts_at = datetime.combine(day, course.start_time)
    return CourseReminder(course, day, starts_at - timedelta(minutes=lead_minutes))


def reminder_key(reminder: CourseReminder) -> str:
    # ⚠ 去重键里写死了 ":30"，即“提前30分钟”。若以后 lead 可变，同一节课会发两次。
    return f"{reminder.course.id}:{reminder.class_date.isoformat()}:30"


def reminders_due(courses: list[Course], now: datetime, lead_minutes: int = 30) -> list[CourseReminder]:
    """返回“现在正好处于提醒窗口内”的提醒。
    ⚠ 窗口只有 1 分钟：remind_at <= now < remind_at + 1min。
      若调度在提醒时刻中断超过 1 分钟，这条提醒会被跳过、不补发。"""
    due: list[CourseReminder] = []
    for course in courses:
        reminder = reminder_for(course, now.date(), lead_minutes)
        if reminder is not None and reminder.remind_at <= now < reminder.remind_at + timedelta(minutes=1):
            due.append(reminder)
    return due


def format_reminder(reminder: CourseReminder) -> str:
    """QQ 上实际显示的通知文本。"""
    course = reminder.course
    lines = ["【课程提醒】", f"课程：{course.name}",
             f"时间：{reminder.class_date.isoformat()} {course.start_time.strftime('%H:%M')}-{course.end_time.strftime('%H:%M') if course.end_time else '未知'}",
             f"地点：{course.location}"]
    if course.memo.strip():
        lines.append(f"备忘录：{course.memo.strip()}")
    return "\n".join(lines)
```

### 3.2 `modules/automation/course_loader.py`

JSON → `Course`。节次时间（如 `08:20`）在这里只是字符串转 `time`，**没有**“第几节”的概念。

```python
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
```

---

## 4. 查询层：课程查询 + 固定命令解析

### 4.1 `modules/automation/course_query.py`

按日期/周次过滤，并把“同一天、同课、同地点、时间首尾相接”的条目合并成一大节（如 08:20–09:05 + 09:15–10:00 → 08:20–10:00，按用户课表规则默认两小段已合在 `start_time/end_time` 里，这里是兜底合并）。

```python
"""结构化课程查询服务。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from .course_loader import load_courses
from .course_reminder import Course


@dataclass(frozen=True)
class CourseOccurrence:
    """某天实际发生的一节课（由 Course + 具体日期推导）。"""
    course_id: str
    name: str
    date: str
    weekday: int
    week: int              # 教学周
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
    """判断这门课在 day 当天是否真的上课（含 weeks 黑白名单）。"""
    if not course.enabled or not (course.start_date <= day <= course.end_date):
        return False
    if day.weekday() != course.weekday:
        return False
    actual_week = (day - course.start_date).days // 7 + 1
    return (course.weeks is None or actual_week in course.weeks) and (week is None or actual_week == week)


def _merge_adjacent(items: list[CourseOccurrence]) -> list[CourseOccurrence]:
    """合并相邻同课同地点且时间首尾相接的条目（处理跨小节的连堂）。"""
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
```

### 4.2 `modules/automation/course_command.py`（本轮新增，纯脚本查询窗口）

这是“命令查询窗口”的核心：把 `下周一上午第一节什么课` 这类短句，用正则拆成 **日期 + 节次范围**，再调 `query_courses_from_path`。不接任何模型。

⚠ 注意：本文件**自己又定义了一份**“节次时间表”（`PERIODS`）和“周首日”（`WEEK_START`）。这份表与 `course_schedule.json` 里的时间并无绑定关系——改课表节次时间时容易两边不一致（见 §7 疑点 5）。

```python
"""固定格式的课程查询命令，不调用大模型。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from .course_query import query_courses_from_path

WEEK_START = date(2026, 8, 31)   # ⚠ 定义但未被本文件使用（周次由课程 start_date 推导）——死代码
WEEKDAYS = "一二三四五六日"
PERIODS = (                     # ⚠ 第二份节次时间表，与配置数据无共享
    (1, "08:20", "10:00"),
    (2, "10:20", "12:00"),
    (3, "13:30", "15:10"),
    (4, "15:25", "17:05"),
    (5, "18:30", "20:10"),
)
PERIOD_ALIASES = {
    # ⚠ 含 六/七，但下方正则最多只到 五 —— 冗余键
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}


def _resolve_date(text: str, today: date) -> date | None:
    """从句子里解析目标日期；缺省回落到‘今天’。优先级：
    YYYY-MM-DD / YYYY年MM月DD日 → 今天/明天/后天 → 本周X/下周X → 今天。"""
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
    monday = today - timedelta(days=today.weekday())   # 本周一
    if match.group(1) == "下周":
        monday += timedelta(days=7)
    return monday + timedelta(days=weekday)


def _period_filter(text: str) -> tuple[int, int] | None:
    """解析节次/时段为 [start, end] 闭区间；什么都没说返回 None（=全天）。"""
    if match := re.search(r"第?([一二三四五12345])(?:到|至|-|—)([一二三四五12345])节", text):
        return PERIOD_ALIASES[match.group(1)], PERIOD_ALIASES[match.group(2)]
    if match := re.search(r"第?([一二三四五12345])节", text):
        period = PERIOD_ALIASES[match.group(1)]
        return period, period
    if "上午" in text:
        return 1, 2            # 默认值：上午 = 第1-2节
    if "下午" in text:
        return 3, 4            # 默认值：下午 = 第3-4节（用户定义的默认规则）
    if "晚上" in text or "夜间" in text:
        return 5, 5
    return None


def _period_number(start_time: str) -> int:
    """按 start_time 反查它在第几节（供过滤/显示用）。"""
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
    """解析固定课程命令并返回文本结果。这是 QQ 入站的唯一查询入口。"""
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
```

---

## 5. 提醒服务与去重

### 5.1 `modules/automation/course_reminder_service.py`

纯 async 的业务流程：**到期 → 去重 → 发送 → 记 checkpoint**。通过 Protocol 解耦存储和通知，测试时可注入内存实现。

```python
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
    for reminder in reminders_due(courses, now, lead_minutes):
        key = reminder_key(reminder)
        if await checkpoints.known(key):
            continue                 # 已发过（幂等）
        await notifier.send(format_reminder(reminder))
        await checkpoints.remember(key)   # 只有发送成功才记 checkpoint
        sent.append(reminder)
    return sent
```

### 5.2 `modules/automation/checkpoint_store.py`

两个实现：`MemoryCheckpointStore`（测试用）、`SqliteCheckpointStore`（云端持久化，重启不重发）。每连接用完即关，规避 Windows/SQLite 文件锁。

```python
"""检查点存储：课程提醒幂等与雨课堂内容去重共用。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class MemoryCheckpointStore:
    """进程内检查点，适合测试和短任务。"""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def known(self, key: str) -> bool:
        return key in self._keys

    async def remember(self, key: str) -> None:
        self._keys.add(key)


class SqliteCheckpointStore:
    """持久化检查点，云端重启后不会重复发送。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, created_at REAL NOT NULL)")
            connection.commit()
        finally:
            connection.close()

    async def known(self, key: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute("SELECT 1 FROM checkpoints WHERE key = ?", (key,)).fetchone()
            return row is not None
        finally:
            connection.close()

    async def remember(self, key: str) -> None:
        connection = self._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO checkpoints(key, created_at) VALUES (?, ?)", (key, time.time()))
            connection.commit()
        finally:
            connection.close()
```

### 5.3 `modules/automation/runner.py`

统一入口。⚠ 课程提醒和雨课堂共用一个 runner；`run_course_reminders` 并没有雨课堂依赖，但模块顶部 import 了 `..browser.BrowserTool` 与 rainclass 相关（见 §7 疑点 4）。

```python
"""自动化运行编排：云端调度器按需调用的入口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..browser import BrowserTool                        # ⚠ 提醒链路并不需要浏览器
from .checkpoint_store import MemoryCheckpointStore, SqliteCheckpointStore
from .course_loader import load_courses
from .course_reminder_service import send_due_course_reminders
from .rainclass_collector import RainClassConfig, RainClassPage
from .rainclass_monitor import RainClassItem, check_rainclass

__all__ = [
    "MemoryCheckpointStore", "RainClassConfig", "RainClassPage", "SqliteCheckpointStore",
    "run_course_reminders", "run_rainclass_monitor",
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
```

---

## 6. 外部服务与 Server 端

### 6.1 `modules/external_services/qq.py` / `onebot_qq.py`

```python
"""QQ 外部通知服务的协议。具体 MCP、HTTP 或本地代理由部署配置提供。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QQMessage:
    content: str
    target_id: str


class QQProvider(Protocol):
    async def send_text(self, message: QQMessage) -> None: ...
    async def health(self) -> bool: ...
```

```python
"""OneBot v11（NapCat）QQ 适配器。

对接 NapCat 暴露的 OneBot v11 HTTP API：
- 发送私聊：POST /send_private_msg，body {"user_id": <QQ号>, "message": "..."}
- 健康检查：GET /get_version

鉴权：Authorization: Bearer <access_token>（与 NapCat 网络配置中的 token 一致）。
凭据只在实例化时传入，不写入模块、日志或 manifest。
"""

from __future__ import annotations

import httpx

from .qq import QQMessage


class OneBotQQProvider:
    def __init__(self, endpoint: str, access_token: str = "", timeout: float = 10.0, transport=None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self.transport = transport

    async def send_text(self, message: QQMessage) -> None:
        payload = {"user_id": _to_user_id(message.target_id), "message": message.content}
        response = await self._request("POST", "/send_private_msg", payload)
        data = response.json()
        if data.get("status") != "ok" or data.get("retcode") != 0:
            raise RuntimeError(f"QQ 消息发送失败: {data.get('message') or data.get('wording') or data}")

    async def health(self) -> bool:
        try:
            response = await self._request("GET", "/get_version")
            data = response.json()
            return data.get("status") == "ok" and data.get("retcode") == 0
        except (httpx.HTTPError, ValueError):
            return False

    async def _request(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
        async with httpx.AsyncClient(base_url=self.endpoint, timeout=self.timeout, transport=self.transport) as client:
            return await client.request(method, path, json=payload, headers=headers)


def _to_user_id(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value
```

### 6.2 `server/automation_tools.py`

自动化工具在 Server 侧的注册层：工具函数的入参 = `ToolRegistry` 调用约定，内部再从 `config` 取真实配置。`course_query` / `course_reminder` / `rainclass_monitor` 三个工具注册为 Owner 工具，供 JobScheduler 与未来的 Agent 调用。

```python
"""自动化模块的 Server 端工具注册。 ...（docstring 略）"""

from __future__ import annotations

import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tool_runtime.permissions import ToolRisk  # noqa: E402
from tool_runtime.registry import ToolResult  # noqa: E402

from modules.automation.checkpoint_store import SqliteCheckpointStore  # noqa: E402
from modules.automation.rainclass_collector import RainClassConfig  # noqa: E402
from modules.automation.course_query import query_courses_from_path  # noqa: E402
from modules.automation.runner import run_course_reminders, run_rainclass_monitor  # noqa: E402
from modules.browser.playwright_runtime import PlaywrightBrowser  # noqa: E402
from modules.external_services.onebot_qq import OneBotQQProvider  # noqa: E402
from modules.external_services.qq import QQMessage  # noqa: E402


def register_automation_tools(registry, config: dict) -> None:
    """把自动化工具注册进 Owner（idea）工具注册表。"""
    automation_cfg = config.get("automation", {}) or {}
    policy = registry.policy

    def _checkpoint() -> SqliteCheckpointStore:
        db_path = automation_cfg.get("checkpoint_db_path", str(PROJECT_ROOT / "memory" / "automation_checkpoints.db"))
        return SqliteCheckpointStore(db_path)

    class _TargetNotifier:                      # 适配 ReminderNotifier Protocol 的最小实现
        def __init__(self, provider: OneBotQQProvider, target_id: str) -> None:
            self.provider = provider
            self.target_id = target_id

        async def send(self, content: str) -> None:
            await self.provider.send_text(QQMessage(content=content, target_id=self.target_id))

    def _notifier(target_id: str) -> _TargetNotifier | None:
        qq = automation_cfg.get("qq", {}) or {}
        endpoint = qq.get("endpoint", "")
        if not endpoint:
            return None
        return _TargetNotifier(OneBotQQProvider(endpoint, qq.get("token", "")), target_id or qq.get("target_id", ""))

    async def course_query_tool(week: int = 0, query_date: str = "", course_schedule_path: str = "", execution_context=None) -> ToolResult:
        course_path = course_schedule_path or automation_cfg.get("course_schedule_path", "")
        if not course_path:
            return ToolResult(False, "未配置课程表路径（config.automation.course_schedule_path）", "automation.course_query")
        try:
            courses = query_courses_from_path(course_path, query_date=query_date or None, week=week or None)
        except Exception as error:
            return ToolResult(False, f"课程查询失败: {error}", "automation.course_query")
        return ToolResult(True, json.dumps(courses, ensure_ascii=False), "automation.course_query", {"count": len(courses)})

    async def course_reminder_tool(course_schedule_path: str = "", target_id: str = "", execution_context=None) -> ToolResult:
        if not automation_cfg.get("enabled", False):
            return ToolResult(False, "自动化模块未启用（config.automation.enabled=false）", "automation.course_reminder")
        course_path = course_schedule_path or automation_cfg.get("course_schedule_path", "")
        if not course_path:
            return ToolResult(False, "未配置课程表路径（config.automation.course_schedule_path）", "automation.course_reminder")
        notifier = _notifier(target_id)
        if notifier is None:
            return ToolResult(False, "未配置 QQ 通知端点（config.automation.qq.endpoint）", "automation.course_reminder")
        try:
            sent = await run_course_reminders(course_path, _checkpoint(), notifier)
        except Exception as error:
            return ToolResult(False, f"课程提醒执行失败: {error}", "automation.course_reminder")
        return ToolResult(True, f"课程提醒检查完成，本次发送 {len(sent)} 条", "automation.course_reminder", {"sent": len(sent)})

    async def rainclass_monitor_tool(target_id: str = "", execution_context=None) -> ToolResult:
        # 本轮未启用，逻辑从略；注册了但不配置 base_url/course_id 就会返回明确的“未配置”提示。
        ...

    for name in ("automation.course_query", "automation.course_reminder", "automation.rainclass_monitor"):
        policy.TOOL_RISKS[name] = ToolRisk.WRITE
        policy.OWNER_MODULE_TOOLS.add(name)

    registry.register_tool("automation.course_query", course_query_tool, {...})
    registry.register_tool("automation.course_reminder", course_reminder_tool, {...})
    registry.register_tool("automation.rainclass_monitor", rainclass_monitor_tool, {...})
```

### 6.3 `server/main.py` 关键段

`/onebot/event`（QQ 入站）—— 已从 Agent/LLM 路径改成**纯脚本查询**：

```python
# 相关导入
from modules.external_services.onebot_qq import OneBotQQProvider
from modules.external_services.qq import QQMessage
from modules.automation.course_command import query_course_command


@app.post("/onebot/event")
async def onebot_event(request: Request):
    onebot_cfg = config.get("onebot", {}) or {}
    secret = os.environ.get("IDEA_ONEBOT_SECRET", onebot_cfg.get("secret", ""))
    # 同时兼容两种常见头，避免 NapCat 版本差异导致 401
    supplied_values = [request.headers.get("X-OneBot-Secret", "").strip()]
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied_values.append(authorization[7:].strip())
    if not secret or not any(
        hmac.compare_digest(supplied, secret)
        for supplied in supplied_values
        if supplied
    ):
        raise HTTPException(status_code=401, detail="OneBot secret 无效")

    body = await request.json()
    # 只处理“人发给机器人”的私聊消息；user_id 白名单
    if body.get("post_type") != "message" or str(body.get("user_id", "")) != "3080713452":
        return {"status": "ignored"}
    message = body.get("raw_message") or body.get("message")
    if not isinstance(message, str) or not (message := message.strip()):
        return {"status": "ignored"}

    qq_cfg = config.get("automation", {}).get("qq", {}) or {}
    endpoint = qq_cfg.get("endpoint", "")
    if not endpoint:
        raise HTTPException(status_code=503, detail="未配置 OneBot 回复端点")
    # course_schedule_path 实际不在 qq_cfg 里，此写法是“优先 qq 段、兜底 automation 段”（见 §7 疑点 3）
    schedule_path = qq_cfg.get(
        "course_schedule_path",
        config.get("automation", {}).get("course_schedule_path", ""),
    )
    # ★ 核心：纯脚本解析，不经过 Agent / LLM
    reply = query_course_command(message, schedule_path)
    await OneBotQQProvider(
        endpoint,
        qq_cfg.get("token", ""),
    ).send_text(
        QQMessage(
            content=reply,
            target_id=str(qq_cfg.get("target_id", "3080713452")),
        )
    )
    return {"status": "ok"}
```

调度器装配（main.py 中）：

```python
# 自动化工具注册进 idea 的 ToolRegistry
from automation_tools import register_automation_tools
register_automation_tools(idea_tool_registry, config)

# 四个独立执行器；idea 持有调度工具
agent_runners = {"idea": AgentRunner(llm=llm_client, tools=idea_tool_registry, ...), ...}

# 后台定时作业调度器
job_scheduler = JobScheduler(platform_store, agent_runners["idea"].tools)

# lifespan 里 start/stop
@asynccontextmanager
async def app_lifespan(app):
    ...
    await job_scheduler.start()
    ...
```

调度 API（main.py，供 Owner 用 token 创建/删除任务）：

```python
@app.get("/api/platform/jobs")          # 列出
@app.post("/api/platform/jobs")         # 创建：tool_name + args + interval_seconds(≥30)
@app.delete("/api/platform/jobs/{job_id}")  # 删除
```

### 6.4 `server/jobs.py`（调度器）

每 `scan_interval` 秒扫一次 `scheduled_jobs`，到期则用 **Owner 上下文**执行工具，回写结果并推进 `next_run_at`。工具执行本身仍受权限/审批链约束。

```python
"""后台作业调度器（对齐 dsh jobs/schedule）。...（docstring 略）"""

class JobScheduler:
    def __init__(self, platform_store, tool_registry, scan_interval: float = 10.0):
        self.store = platform_store
        self.registry = tool_registry
        self.scan_interval = scan_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="idea-job-scheduler")
        logger.info("job scheduler started")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("job scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.scan_interval)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        for job in self.store.due_scheduled_jobs():     # next_run_at <= now
            await self._run_job(job)

    async def _run_job(self, job: dict) -> None:
        job_id, tool_name = job["job_id"], job["tool_name"]
        try:
            args = json.loads(job["args_json"] or "{}")
            context = ExecutionContext(
                request_context=RequestContext(
                    f"job-{job_id}",
                    Principal(job["account_id"], job["account_id"], "owner", "token-job"),
                    None, job["space_id"],
                ),
                agent_id=job["agent_id"] or "idea",
                is_owner=True,
            )
            result = await self.registry.execute(tool_name, args, context)
            status = "success" if result.success else "failed"
            output = result.output[:1000] if result.output else ""
            if not result.success:
                output = f"[{result.metadata.get('decision', 'deny')}] {output}"
            self.store.update_scheduled_job_run(job_id, status, output, job["next_run_at"] + float(job["interval_seconds"]))
        except Exception as error:
            logger.warning("scheduled job %s failed: %s", job_id, error)
            self.store.update_scheduled_job_run(job_id, "error", str(error)[:1000], job["next_run_at"] + float(job["interval_seconds"]))
```

### 6.5 `server/config.yaml` 相关段

```yaml
automation:
  enabled: true                # 云端调度器只执行 enabled 的工具
  checkpoint_db_path: ./memory/automation_checkpoints.db
  course_schedule_path: ./automation/course_schedule.json
  qq:
    endpoint: http://127.0.0.1:3000      # NapCat OneBot HTTP Server（仅本机）
    token: <NapCat access_token>          # 发给 NapCat 的出站鉴权
    target_id: '3080713452'               # ★ 收提醒的人（不是机器人自己）
  rainclass:
    base_url: ''
    course_id: ''
    storage_state_path: ./automation/rainclass_storage_state.json

onebot:
  secret: <与 NapCat 一致的入站 secret>    # 校验 NapCat → IDEA 的回调
```

> 环境变量：`IDEA_AUTH_TOKEN`（管理 API 鉴权）、`IDEA_ONEBOT_SECRET`（可覆盖 onebot.secret），均在 `/etc/idea-server.env`。

---

## 7. 我在整理时注意到的架构疑点（供审阅聚焦）

按“影响大小”排序，均为**事实性观察**，不是结论：

1. **查询入口与调度注册对 config 的读取重复**：`main.py /onebot/event` 直接读 `config["automation"]["qq"]` 并自己 `new OneBotQQProvider`；`automation_tools.py` 里 `_notifier()` 也读同一段配置并再次构造 Provider。同一配置键在两个文件里各自解析，将来加字段/改键名要同步两处。

2. **`reminder_key` 硬编码 `:30`**：`course_reminder.py` 的去重键把“提前 30 分钟”写死在字符串里。一旦 lead 可配置，同一节课会被当成两条提醒（旧键无效，重发一次）。建议把 lead 纳入 key 参数。

3. **`course_schedule_path` 查找路径别扭**：`main.py` 先查 `qq_cfg["course_schedule_path"]`（qq 段里根本没有这个键），再兜底 `automation["course_schedule_path"]`。应只从 `automation` 段读。

4. **提醒链路背着雨课堂/浏览器依赖**：`runner.py` 顶层 `from ..browser import BrowserTool`，`runner.py`、`__init__.py`、`automation_tools.py` 都把提醒与雨课堂 import 搅在一起。课程提醒根本不碰浏览器。若 `browser` 或 `rainclass_*` 有导入问题（如未装 playwright），可能连累提醒任务启动失败。建议拆成两个 runner 或按需延迟 import。

5. **节次时间表与课程数据的“第几节”是两套隐式约定**：
   - `course_schedule.json` 里只写绝对时间 `start_time/end_time`，没有节次号；
   - `course_command.py` 另有一份 `PERIODS`（第 1–5 节 ↔ 08:20…20:10）负责“第 N 节”与时间的换算；
   - 两者无绑定：换上课时间（比如下午提前到 13:00）时，只改 JSON 而不同步 `PERIODS`，查询结果里的“第3节”就会错位。

6. **`WEEK_START` 死代码 + 冗余别名**：`course_command.py` 的 `WEEK_START = date(2026,8,31)` 定义了却未被使用（周次由各课程 `start_date` 推导）；`PERIOD_ALIASES` 里 `六/七` 无对应正则可命中。都属于改课表时容易被误改的坑。

7. **1 分钟提醒窗口不补发**：`reminders_due` 的命中窗口是 `[remind_at, remind_at+1min)`。调度器每 60 秒扫一次、重启/断档超过 1 分钟就会漏提醒且不补发。对“上课提醒”这种错过就无意义的场景可以接受，但值得明确记录这个行为。

8. **依赖方向轻微倒置（见仁见智）**：`server/automation_tools.py` 是“Server 层”却直接 import `modules/*` 并构造 `SqliteCheckpointStore`/`OneBotQQProvider`；而查询命令本身（`course_command.py`）又在 `modules/automation`（业务模块）里。将来若把 modules 打包成独立库给客户端复用，Server 与 modules 的边界要重新划定。

---

## 8. 说明

- 云端当前运行的就是上述代码：课程提醒任务每 60 秒跑一次，`/onebot/event` 为纯脚本查询，均不调用 LLM。
- 本文件由真实源码整理而来；源码以 `D:\Project World\Project-IDEA` 下 `modules/`、`server/` 为准，云端 `/opt/idea-server` 与之一一对应。
- 标注 `⚠` 的行注释为审阅提示，非代码缺陷定性。
