"""OneBot（NapCat）QQ 适配器测试：使用 httpx MockTransport，不触碰真实网络。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

from modules.external_services.onebot_qq import OneBotQQProvider  # noqa: E402
from modules.external_services.qq import QQMessage  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_send_text_posts_onebot_payload() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode("utf-8")
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 42}})

    provider = OneBotQQProvider("http://127.0.0.1:3000", access_token="napcat-secret",
                                transport=httpx.MockTransport(handler))
    _run(provider.send_text(QQMessage(content="【课程提醒】高等数学", target_id="123456789")))

    assert captured["url"] == "http://127.0.0.1:3000/send_private_msg"
    assert captured["auth"] == "Bearer napcat-secret"
    assert '"user_id": 123456789' in captured["body"]
    assert "高等数学" in captured["body"]


def test_send_text_raises_on_onebot_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed", "retcode": 100, "message": "target not found"})

    provider = OneBotQQProvider("http://127.0.0.1:3000", transport=httpx.MockTransport(handler))
    try:
        _run(provider.send_text(QQMessage(content="hello", target_id="1")))
    except RuntimeError as error:
        assert "发送失败" in str(error)
    else:
        raise AssertionError("发送失败时应抛出 RuntimeError")


def test_health_ok() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"app_name": "NapCat"}})

    provider = OneBotQQProvider("http://127.0.0.1:3000", transport=httpx.MockTransport(handler))
    assert _run(provider.health())


def test_health_false_on_network_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OneBotQQProvider("http://127.0.0.1:3000", transport=httpx.MockTransport(handler))
    assert not _run(provider.health())
