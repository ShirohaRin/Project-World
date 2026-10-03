"""外部服务适配协议；不在模块中硬编码 QQ 凭据或具体客户端。"""

from __future__ import annotations

from typing import Protocol


class ExternalNotificationProvider(Protocol):
    async def send_text(self, content: str) -> None: ...
    async def health(self) -> bool: ...
