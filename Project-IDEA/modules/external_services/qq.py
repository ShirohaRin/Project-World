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
