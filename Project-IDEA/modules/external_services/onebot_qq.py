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
