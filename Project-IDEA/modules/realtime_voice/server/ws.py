"""WebSocket endpoint serving realtime voice sessions.

One connection carries exactly one session. The endpoint owns transport
concerns only: handshake, authorization, frame classification and error
reporting. Session state lives in :mod:`session`, and audio processing,
recognition, dialogue and synthesis are attached in later milestones.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..contract.protocols import (
    MAX_FRAME_DURATION_MS,
    ClientMessageType,
    ProtocolError,
    ProtocolErrorCode,
    ServerMessageType,
    decode_audio_frame,
    encode_message,
    error_message,
    parse_client_hello,
    server_message,
)
from .audio.resample import INTERNAL_SAMPLE_RATE, pcm16_duration_ms, resample_pcm16
from .session import VoiceSession

logger = logging.getLogger("idea.realtime_voice")

router = APIRouter()

# The client must open with `hello`; a silent connection is dropped instead of
# being held open.
HELLO_TIMEOUT_SECONDS = 5.0

WS_CLOSE_PROTOCOL_ERROR = 1002
WS_CLOSE_POLICY_VIOLATION = 1008


@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    """Serve a single realtime voice session."""
    if not _authorize(websocket):
        logger.warning("voice endpoint rejected an unauthorized connection")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="unauthorized")
        return

    await websocket.accept()
    session = VoiceSession(session_id=uuid.uuid4().hex)
    try:
        await _run_session(websocket, session)
    except WebSocketDisconnect:
        logger.info("voice session %s: client disconnected", session.session_id)
    finally:
        session.close()
        logger.info(
            "voice session %s closed (frames=%d, bytes=%d)",
            session.session_id,
            session.frames_received,
            session.bytes_received,
        )


def _authorize(websocket: WebSocket) -> bool:
    """Check the optional shared token from $IDEA_VOICE_TOKEN.

    M0 is deliberately minimal: with the variable unset every connection is
    accepted, which is only acceptable while the endpoint is not exposed.
    M1 must replace this with the platform credential check
    (``server/platform_auth.py``), including audit records.
    """
    expected = os.environ.get("IDEA_VOICE_TOKEN", "").strip()
    if not expected:
        return True
    supplied = websocket.query_params.get("token", "")
    return hmac.compare_digest(supplied, expected)


async def _run_session(websocket: WebSocket, session: VoiceSession) -> None:
    """Drive the receive loop until the client ends or disconnects."""
    payload = await _await_hello(websocket)
    if payload is None:
        return
    try:
        hello = parse_client_hello(payload)
    except ProtocolError as error:
        await _send_error(websocket, error.code, error.message)
        await websocket.close(code=WS_CLOSE_PROTOCOL_ERROR, reason=error.code)
        return
    await _send(websocket, session.apply_hello(hello))

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is not None:
            await _handle_audio(websocket, session, data)
            continue
        text = message.get("text")
        if text is None:
            continue
        if not await _handle_text(websocket, session, text):
            return


async def _await_hello(websocket: WebSocket) -> dict[str, Any] | None:
    """Read the mandatory first message, which must be a ``hello`` object."""
    try:
        message = await asyncio.wait_for(
            websocket.receive(), timeout=HELLO_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.info("voice endpoint closed a connection that never said hello")
        await websocket.close(code=WS_CLOSE_PROTOCOL_ERROR, reason="hello timeout")
        return None

    if message["type"] == "websocket.disconnect":
        return None

    raw = message.get("text")
    payload = _parse_json_object(raw) if raw is not None else None
    if payload is None or payload.get("t") != ClientMessageType.HELLO:
        await _send_error(
            websocket,
            ProtocolErrorCode.BAD_MESSAGE,
            "the first message must be a hello message",
        )
        await websocket.close(code=WS_CLOSE_PROTOCOL_ERROR, reason="expected hello")
        return None
    return payload


async def _handle_text(websocket: WebSocket, session: VoiceSession, raw: str) -> bool:
    """Handle one JSON control message. Returns False when the session ends."""
    payload = _parse_json_object(raw)
    if payload is None:
        await _send_error(
            websocket,
            ProtocolErrorCode.BAD_MESSAGE,
            "control message must be a JSON object",
        )
        return True

    kind = payload.get("t")
    try:
        if kind == ClientMessageType.PING:
            await _send(websocket, server_message(ServerMessageType.PONG))
        elif kind == ClientMessageType.START:
            session.start()
        elif kind == ClientMessageType.TEXT:
            text = payload.get("text")
            if not isinstance(text, str):
                raise ProtocolError(
                    ProtocolErrorCode.BAD_MESSAGE, "text field must be a string"
                )
            session.accept_text(text)
        elif kind == ClientMessageType.INTERRUPT:
            interrupted = session.interrupt()
            if interrupted is not None:
                await _send(
                    websocket,
                    server_message(
                        ServerMessageType.USER_ACTIVITY,
                        interrupted_speech_id=interrupted,
                    ),
                )
        elif kind == ClientMessageType.END:
            await _send(
                websocket,
                server_message(ServerMessageType.SESSION_ENDED, reason="client_end"),
            )
            session.close()
            await websocket.close()
            return False
        else:
            raise ProtocolError(
                ProtocolErrorCode.BAD_MESSAGE, f"unknown message type {kind!r}"
            )
    except ProtocolError as error:
        await _send_error(websocket, error.code, error.message)
    return True


async def _handle_audio(websocket: WebSocket, session: VoiceSession, data: bytes) -> None:
    """Validate one inbound frame and normalize it to the internal rate.

    M1 normalizes and counts; M2 hands the normalized payload to the
    recognition runtime.
    """
    try:
        _header, payload = decode_audio_frame(data)
        rate = session.audio_rate
        # 字节上限是速率无关的，这里再按协商采样率核对实际时长
        if pcm16_duration_ms(len(payload), rate) > MAX_FRAME_DURATION_MS:
            raise ProtocolError(
                ProtocolErrorCode.FRAME_TOO_LARGE,
                f"audio frame exceeds {MAX_FRAME_DURATION_MS} ms",
            )
        normalized = resample_pcm16(
            payload, src_rate=rate, dst_rate=INTERNAL_SAMPLE_RATE
        )
        session.note_audio_frame(len(normalized))
    except ProtocolError as error:
        await _send_error(websocket, error.code, error.message)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse ``raw`` into a JSON object, or None when it is not one."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _send(websocket: WebSocket, message: dict[str, Any]) -> None:
    await websocket.send_text(encode_message(message))


async def _send_error(
    websocket: WebSocket, code: ProtocolErrorCode, detail: str
) -> None:
    await _send(websocket, error_message(code, detail))
