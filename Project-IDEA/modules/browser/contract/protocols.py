"""浏览器工具的跨运行时协议。具体实现可由 Electron 或云端 Worker 提供。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BrowserSnapshot:
    url: str
    title: str
    text: str


class BrowserTool(Protocol):
    async def navigate(self, url: str) -> BrowserSnapshot: ...
    async def snapshot(self) -> BrowserSnapshot: ...
    async def click(self, selector: str) -> None: ...
    async def type_text(self, selector: str, text: str) -> None: ...
    async def screenshot(self) -> bytes: ...
