"""WebSocket endpoint tests for the realtime voice module."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.realtime_voice.contract.protocols import (
    PROTOCOL_VERSION,
    encode_audio_frame,
)
from modules.realtime_voice.server.ws import router

_app = FastAPI()
_app.include_router(router)
_client = TestClient(_app)


def _hello(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "t": "hello",
        "v": PROTOCOL_VERSION,
        "platform": "android",
        "codec": "pcm16",
        "rate": 16000,
        "channels": 1,
    }
    payload.update(overrides)
    return payload


def test_handshake_start_audio_end() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello())
        started = websocket.receive_json()
        assert started["t"] == "session_started"
        assert started["protocol"] == PROTOCOL_VERSION
        assert started["audio"] == {"codec": "pcm16", "rate": 16000, "channels": 1}

        websocket.send_json({"t": "start"})
        websocket.send_bytes(encode_audio_frame(1, b"\x01\x02" * 160))

        websocket.send_json({"t": "ping"})
        assert websocket.receive_json()["t"] == "pong"

        websocket.send_json({"t": "end"})
        assert websocket.receive_json() == {"t": "session_ended", "reason": "client_end"}


def test_high_sample_rate_is_negotiated() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello(rate=48000))

        started = websocket.receive_json()
        assert started["audio"]["rate"] == 48000


def test_first_message_must_be_hello() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json({"t": "start"})

        message = websocket.receive_json()
        assert message["t"] == "error"
        assert message["code"] == "bad_message"


def test_unsupported_protocol_version_is_rejected() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello(v=PROTOCOL_VERSION + 1))

        message = websocket.receive_json()
        assert message["t"] == "error"
        assert message["code"] == "version_mismatch"


def test_audio_before_start_is_rejected() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello())
        websocket.receive_json()

        websocket.send_bytes(encode_audio_frame(1, b"\x00\x00"))

        message = websocket.receive_json()
        assert message["t"] == "error"
        assert message["code"] == "invalid_state"


def test_corrupted_frame_is_rejected() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello())
        websocket.receive_json()
        websocket.send_json({"t": "start"})

        websocket.send_bytes(b"XX" + b"\x00" * 10)

        message = websocket.receive_json()
        assert message["t"] == "error"
        assert message["code"] == "bad_frame"


def test_text_turn_is_accepted_after_start() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello())
        websocket.receive_json()
        websocket.send_json({"t": "start"})

        websocket.send_json({"t": "text", "text": "你好"})
        websocket.send_json({"t": "interrupt"})

        # Nothing to interrupt while thinking: no user_activity is emitted, so
        # the next message is the response to `ping`.
        websocket.send_json({"t": "ping"})
        assert websocket.receive_json()["t"] == "pong"


def test_48k_audio_frames_are_normalized_without_error() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello(rate=48000))
        websocket.receive_json()
        websocket.send_json({"t": "start"})

        # 20 ms @48k，服务端会先降到 16k 再计数
        websocket.send_bytes(encode_audio_frame(1, b"\x01\x02" * 480))
        websocket.send_json({"t": "ping"})

        assert websocket.receive_json()["t"] == "pong"


def test_frame_longer_than_120ms_is_rejected() -> None:
    with _client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json(_hello(rate=16000))
        websocket.receive_json()
        websocket.send_json({"t": "start"})

        # 2000 个样本 @16k = 125 ms，字节数没超协议的速率无关上限，
        # 由端点按协商采样率算出的时长拦下
        websocket.send_bytes(encode_audio_frame(1, b"\x00\x00" * 2000))

        message = websocket.receive_json()
        assert message["t"] == "error"
        assert message["code"] == "frame_too_large"
