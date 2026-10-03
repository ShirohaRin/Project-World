"""雨课堂增量监控的纯业务逻辑；浏览器和通知通过协议注入。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True)
class RainClassItem:
    id: str
    course_id: str
    item_type: str
    title: str
    content: str = ""
    url: str = ""
    publish_time: str = ""
    due_time: str = ""

    @property
    def fingerprint(self) -> str:
        value = self.id or "|".join((self.course_id, self.item_type, self.title, self.publish_time, self.content))
        return sha256(value.encode("utf-8")).hexdigest()


class RainClassBrowser(Protocol):
    async def collect_items(self) -> list[RainClassItem]: ...


class NotificationSender(Protocol):
    async def send(self, content: str) -> None: ...


class CheckpointStore(Protocol):
    async def known(self, fingerprint: str) -> bool: ...
    async def remember(self, fingerprint: str) -> None: ...


async def check_rainclass(browser: RainClassBrowser, checkpoints: CheckpointStore, notifier: NotificationSender) -> list[RainClassItem]:
    new_items: list[RainClassItem] = []
    for item in await browser.collect_items():
        if await checkpoints.known(item.fingerprint):
            continue
        await notifier.send(format_notification(item))
        await checkpoints.remember(item.fingerprint)
        new_items.append(item)
    return new_items


def format_notification(item: RainClassItem) -> str:
    lines = ["【雨课堂新通知】", f"类型：{item.item_type}", f"标题：{item.title}"]
    if item.content.strip():
        lines.append(f"内容：{item.content.strip()[:1200]}")
    if item.due_time:
        lines.append(f"截止时间：{item.due_time}")
    if item.url:
        lines.append(f"链接：{item.url}")
    return "\n".join(lines)
