"""雨课堂只读采集器。

当前按"页面可见文本 + 可配置关键词"解析公告/通知/作业/任务/测验；
雨课堂页面结构改版或登录态变化时，可在此基础上补充 CSS 选择器配置，
但采集始终只读：不点击提交、不发送、不修改课程数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from ..browser import BrowserTool
from .rainclass_monitor import RainClassItem

DEFAULT_TYPE_KEYWORDS = {
    "公告": "announcement",
    "通知": "notice",
    "作业": "assignment",
    "任务": "task",
    "测验": "quiz",
}

_DUE_PATTERNS = [
    re.compile(r"(?:截止|提交截止|截止时间)\s*[:：]?\s*([0-9]{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s*\d{1,2}:\d{2})?)"),
    re.compile(r"(?:截止|提交截止|截止时间)\s*[:：]?\s*(\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{2})?)"),
]
_MAX_TITLE_LEN = 80


@dataclass
class RainClassConfig:
    base_url: str
    course_id: str = ""
    type_keywords: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TYPE_KEYWORDS))


class RainClassPage:
    """把浏览器工具适配成 check_rainclass 需要的 collect_items 协议。"""

    def __init__(self, browser: BrowserTool, config: RainClassConfig) -> None:
        self.browser = browser
        self.config = config

    async def collect_items(self) -> list[RainClassItem]:
        page = getattr(self.browser, "page", None)
        if page is None:
            snapshot = await self.browser.navigate(self.config.base_url)
            return parse_rainclass_text(snapshot.text, self.config.course_id, self.config.type_keywords)

        await page.goto(self.config.base_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        if "login" in page.url.lower() or "登录" in (await page.title()):
            raise RuntimeError("雨课堂登录态已失效，请重新登录并更新 storage_state")

        courses = await _load_student_courses(page)
        selected = [course for course in courses if not self.config.course_id or course["classroom_id"] == self.config.course_id]
        items: list[RainClassItem] = []
        for course in selected:
            try:
                await page.goto(course["url"], wait_until="domcontentloaded")
                await page.wait_for_timeout(1200)
                if "login" in page.url.lower() or "登录" in (await page.title()):
                    raise RuntimeError("雨课堂登录态已失效，请重新登录并更新 storage_state")
                items.extend(await _collect_course_items(page, course, self.config.type_keywords))
            except RuntimeError:
                raise
            except Exception:
                # 单门课程失败不能阻断其他课程；API 采集函数会尽量保留可用结果。
                continue
        return items

async def _load_student_courses(page) -> list[dict[str, str]]:
    payload = await page.evaluate(
        """async () => {
            const response = await fetch('/v2/api/web/courses/list?identity=2');
            return await response.json();
        }"""
    )
    courses = []
    for entry in (payload.get("data", {}).get("list", []) if isinstance(payload, dict) else []):
        classroom_id = str(entry.get("classroom_id", ""))
        course = entry.get("course", {}) or {}
        if classroom_id and entry.get("name") and course.get("name") and "无此班" not in entry.get("name", ""):
            courses.append({
                "classroom_id": classroom_id,
                "name": str(course["name"]),
                "url": f"https://www.yuketang.cn/v2/web/studentLog/{classroom_id}?university_id={course.get('university_id', '')}&platform_id={course.get('platform', 3) or 3}&classroom_id={classroom_id}",
            })
    return courses


async def _collect_course_items(page, course: dict[str, str], keywords: dict[str, str]) -> list[RainClassItem]:
    api_items = await _collect_course_api_items(page, course, keywords)
    if api_items is not None:
        return api_items

    await page.get_by_text("公告", exact=True).first.click(timeout=8000)
    await page.wait_for_timeout(800)
    text = await page.inner_text("body")
    items = parse_rainclass_text(text, course["classroom_id"], keywords)
    return [
        RainClassItem(
            id=item.id,
            course_id=item.course_id,
            item_type=item.item_type,
            title=f"{course['name']}：{item.title}",
            content=item.content,
            url=page.url,
            publish_time=item.publish_time,
            due_time=item.due_time,
        )
        for item in items
    ]


async def _collect_course_api_items(
    page,
    course: dict[str, str],
    keywords: dict[str, str],
) -> list[RainClassItem] | None:
    classroom_id = course["classroom_id"]
    detail = await page.evaluate(
        """async (classroomId) => {
            const response = await fetch(
                `/v2/api/web/classrooms/${classroomId}?role=5`
            );
            return await response.json();
        }""",
        classroom_id,
    )
    data = detail.get("data", {}) if isinstance(detail, dict) else {}
    if not data or not data.get("free_sku_id"):
        return None

    items: list[RainClassItem] = []
    try:
        announcements = await page.evaluate(
            """async (classroomId) => {
                const response = await fetch(
                    `/v/discussion/v2/announcements/?content=&cid=${classroomId}&limit=100&offset=0&type=9`
                );
                return await response.json();
            }""",
            classroom_id,
        )
    except Exception:
        announcements = None

    announcement_data = _list_value(
        announcements,
        ("data", "data"),
        ("data", "results"),
        ("results",),
    )
    for entry in announcement_data:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("name") or entry.get("subject") or "雨课堂公告")
        content = str(entry.get("content") or entry.get("description") or entry.get("text") or "")
        entry_id = str(entry.get("id") or entry.get("announcement_id") or entry.get("topic_id") or entry.get("pk") or "")
        items.append(_api_item(course, "announcement", title, content, entry_id, entry))

    todo_url = (
        f"/c27/online_courseware/course/classroom/{classroom_id}/"
        f"{data['free_sku_id']}/todo_list/?client_type=web"
    )
    try:
        todo = await page.evaluate(
            """async (url) => {
                const response = await fetch(url);
                return await response.json();
            }""",
            todo_url,
        )
    except Exception:
        todo = None
    todo_entries = _list_value(
        todo,
        ("data", "result"),
        ("data", "results"),
        ("result",),
        ("results",),
    )
    for entry in todo_entries:
        if not isinstance(entry, dict):
            continue
        item_type = _todo_item_type(entry.get("type"), keywords)
        title = str(entry.get("name") or entry.get("title") or entry.get("subject") or "雨课堂学习任务")
        content = str(entry.get("description") or entry.get("content") or entry.get("text") or "")
        entry_id = str(entry.get("leaf_id") or entry.get("leaf_type_id") or entry.get("id") or entry.get("task_id") or "")
        item = _api_item(course, item_type, title, content, entry_id, entry)
        items.append(
            RainClassItem(
                id=item.id,
                course_id=item.course_id,
                item_type=item.item_type,
                title=item.title,
                content=item.content,
                url=str(entry.get("link_url") or item.url),
                publish_time=item.publish_time,
                due_time=item.due_time,
            )
        )

    return items


def _list_value(payload: Any, *paths: tuple[str, ...]) -> list[Any]:
    """兼容雨课堂不同接口版本的列表字段。"""
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, list):
            return value
    return []


def _api_item(
    course: dict[str, str],
    item_type: str,
    title: str,
    content: str,
    entry_id: str,
    entry: dict[str, Any],
) -> RainClassItem:
    publish_time = str(entry.get("publish_time") or "")
    due_time = str(entry.get("score_deadline") or "")
    stable = entry_id or f"{item_type}:{title}:{content}:{publish_time}:{due_time}"
    return RainClassItem(
        id=f"rc:{sha256(f'{course['classroom_id']}:{stable}'.encode()).hexdigest()[:16]}",
        course_id=course["classroom_id"],
        item_type=item_type,
        title=f"{course['name']}：{title}",
        content=content,
        url=course["url"],
        publish_time=publish_time,
        due_time=due_time,
    )


def _todo_item_type(value: Any, keywords: dict[str, str]) -> str:
    type_names = {8: "课堂", 9: "作业", 10: "测验"}
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = value
    name = type_names.get(normalized, "学习内容")
    return keywords.get(name, name)

def parse_rainclass_text(text: str, course_id: str, type_keywords: dict[str, str] | None = None) -> list[RainClassItem]:
    """从页面可见文本中提取内容条目。启发式解析，需按真实页面调优。"""
    keywords = type_keywords or DEFAULT_TYPE_KEYWORDS
    items: list[RainClassItem] = []
    current: dict[str, str | list[str]] | None = None
    item_index = 0

    def flush() -> None:
        nonlocal current, item_index
        if current is not None:
            title = str(current["title"]).strip()
            if title:
                items.append(_build_item(current, course_id, item_index))
                item_index += 1
            current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = _match_type(line, keywords)
        if matched is not None and len(line) <= _MAX_TITLE_LEN:
            flush()
            keyword, item_type = matched
            current = {"type": item_type, "title": _strip_type_prefix(line, keyword), "lines": []}
        elif current is not None:
            current["lines"].append(line)
    flush()
    return items


def _build_item(current: dict, course_id: str, index: int) -> RainClassItem:
    lines = [line for line in current["lines"] if line][:40]
    title = current["title"]
    content = "\n".join(lines)[:1200]
    due_time = _first_match(_DUE_PATTERNS, f"{title}\n{content}")
    return RainClassItem(
        id=f"rc:{sha256(f'{course_id}:{title}:{content}'.encode()).hexdigest()[:16]}",
        course_id=course_id,
        item_type=current["type"],
        title=title,
        content=content,
        due_time=due_time,
    )


def _match_type(line: str, keywords: dict[str, str]) -> tuple[str, str] | None:
    head = line[:8]
    for keyword, item_type in keywords.items():
        if keyword in head:
            return keyword, item_type
    return None


def _strip_type_prefix(line: str, keyword: str) -> str:
    stripped = line[len(keyword):].lstrip()
    return re.sub(r"^[:：]\s*", "", stripped)


def _first_match(patterns: list[re.Pattern], value: str) -> str:
    for pattern in patterns:
        match = pattern.search(value)
        if match:
            return match.group(1).strip()
    return ""
