"""Wire protocol contract for realtime voice sessions.

This module is the single source of truth for the realtime voice protocol:
JSON control messages, the binary audio frame layout, and the validation
rules shared by the server and every client implementation. Client code in
other languages must mirror the constants defined here.

Message flow::

    client                                  server
      |-- hello ------------------------------->|
      |<--------------------- session_started --|
      |-- start ------------------------------->|
      |-- [binary audio frames] --------------->|
      |<-- asr_partial / asr_final -------------|
      |<-- assistant_delta / assistant_done ----|
      |<-- audio_chunk + [binary audio frames] -|
      |<-- audio_done --------------------------|
      |-- end --------------------------------->|
      |<----------------------- session_ended --|

Binary frame layout (8-byte header followed by the payload)::

    offset  size  field
    0       2     magic       b"IV"
    2       1     version     frame format version
    3       1     frame_type  1 = audio
    4       4     seq         uint32 little-endian, 1-based
    8       N     payload     PCM16 little-endian, mono

The sample rate is negotiated once in ``hello`` instead of being carried in
every frame; clients must re-send ``hello`` if the audio format changes.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

PROTOCOL_VERSION: Final[int] = 1

# --- binary frame layout ---------------------------------------------------

FRAME_MAGIC: Final[bytes] = b"IV"
FRAME_HEADER_SIZE: Final[int] = 8
FRAME_TYPE_AUDIO: Final[int] = 1

_FRAME_HEADER: Final[struct.Struct] = struct.Struct("<2sBBI")
_MAX_UINT32: Final[int] = 0xFFFF_FFFF

# --- audio format limits ---------------------------------------------------

MAX_FRAME_DURATION_MS: Final[int] = 120
# Largest payload accepted in one frame: 120 ms of 48 kHz mono PCM16.
MAX_FRAME_PAYLOAD_BYTES: Final[int] = 48_000 * 2 * MAX_FRAME_DURATION_MS // 1000
SUPPORTED_SAMPLE_RATES: Final[tuple[int, ...]] = (16_000, 48_000)
SUPPORTED_CHANNELS: Final[int] = 1
SUPPORTED_CODECS: Final[tuple[str, ...]] = ("pcm16",)

# Codec names reserved by the protocol but not implemented yet. The `codec`
# field exists so adding them later needs no message-structure change.
RESERVED_CODECS: Final[tuple[str, ...]] = ("opus",)


class ClientMessageType(StrEnum):
    """Message types a client may send."""

    HELLO = "hello"
    START = "start"
    TEXT = "text"
    INTERRUPT = "interrupt"
    END = "end"
    PING = "ping"


class ServerMessageType(StrEnum):
    """Message types the server may send."""

    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    ASR_PARTIAL = "asr_partial"
    ASR_FINAL = "asr_final"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_DONE = "assistant_done"
    AUDIO_CHUNK = "audio_chunk"
    AUDIO_DONE = "audio_done"
    USER_ACTIVITY = "user_activity"
    PONG = "pong"
    ERROR = "error"


class ProtocolErrorCode(StrEnum):
    """Machine-readable error codes carried by ``error`` messages."""

    BAD_FRAME = "bad_frame"
    FRAME_TOO_LARGE = "frame_too_large"
    VERSION_MISMATCH = "version_mismatch"
    BAD_MESSAGE = "bad_message"
    UNSUPPORTED_FORMAT = "unsupported_format"
    INVALID_STATE = "invalid_state"


class ProtocolError(Exception):
    """Raised when an inbound message violates the wire protocol."""

    def __init__(self, code: ProtocolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AudioFrameHeader:
    """Parsed 8-byte header of a binary audio frame."""

    version: int
    frame_type: int
    seq: int


@dataclass(frozen=True, slots=True)
class ClientHello:
    """Negotiated audio format declared by a client."""

    protocol_version: int
    platform: str
    codec: str
    rate: int
    channels: int


def encode_audio_frame(
    seq: int,
    payload: bytes,
    *,
    version: int = PROTOCOL_VERSION,
    frame_type: int = FRAME_TYPE_AUDIO,
) -> bytes:
    """Serialize one binary audio frame (header + PCM payload)."""
    if not 1 <= seq <= _MAX_UINT32:
        raise ProtocolError(
            ProtocolErrorCode.BAD_FRAME,
            f"frame seq must be within [1, {_MAX_UINT32}], got {seq}",
        )
    if not payload:
        raise ProtocolError(ProtocolErrorCode.BAD_FRAME, "audio frame payload must not be empty")
    if len(payload) > MAX_FRAME_PAYLOAD_BYTES:
        raise ProtocolError(
            ProtocolErrorCode.FRAME_TOO_LARGE,
            f"audio frame payload exceeds {MAX_FRAME_PAYLOAD_BYTES} bytes",
        )
    if len(payload) % 2:
        raise ProtocolError(
            ProtocolErrorCode.BAD_FRAME,
            "PCM16 payload must have an even byte length",
        )
    return _FRAME_HEADER.pack(FRAME_MAGIC, version, frame_type, seq) + payload


def decode_audio_frame(data: bytes | bytearray | memoryview) -> tuple[AudioFrameHeader, bytes]:
    """Parse one binary audio frame into its header and PCM payload.

    Raises:
        ProtocolError: the frame is truncated, has a wrong magic/version/
            frame type, an empty or oversized payload, or an odd PCM length.
    """
    view = memoryview(data)
    if len(view) < FRAME_HEADER_SIZE:
        raise ProtocolError(
            ProtocolErrorCode.BAD_FRAME,
            f"frame shorter than the {FRAME_HEADER_SIZE}-byte header",
        )
    magic, version, frame_type, seq = _FRAME_HEADER.unpack_from(view)
    if magic != FRAME_MAGIC:
        raise ProtocolError(ProtocolErrorCode.BAD_FRAME, "frame magic mismatch")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            ProtocolErrorCode.VERSION_MISMATCH,
            f"unsupported frame version {version}",
        )
    if frame_type != FRAME_TYPE_AUDIO:
        raise ProtocolError(ProtocolErrorCode.BAD_FRAME, f"unsupported frame type {frame_type}")
    payload = bytes(view[FRAME_HEADER_SIZE:])
    if not payload:
        raise ProtocolError(ProtocolErrorCode.BAD_FRAME, "audio frame payload must not be empty")
    if len(payload) > MAX_FRAME_PAYLOAD_BYTES:
        raise ProtocolError(
            ProtocolErrorCode.FRAME_TOO_LARGE,
            f"audio frame payload exceeds {MAX_FRAME_PAYLOAD_BYTES} bytes",
        )
    if len(payload) % 2:
        raise ProtocolError(
            ProtocolErrorCode.BAD_FRAME,
            "PCM16 payload must have an even byte length",
        )
    return AudioFrameHeader(version=version, frame_type=frame_type, seq=seq), payload


def parse_client_hello(payload: Mapping[str, Any]) -> ClientHello:
    """Validate a ``hello`` payload and return the declared audio format.

    Raises:
        ProtocolError: a required field is missing, malformed, or names an
            unsupported protocol version / codec / sample rate / channel count.
    """
    if not isinstance(payload, Mapping):
        raise ProtocolError(ProtocolErrorCode.BAD_MESSAGE, "hello payload must be an object")

    version = payload.get("v")
    # bool is an int subclass; reject it explicitly so true/false cannot pass
    # for a protocol version.
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProtocolError(ProtocolErrorCode.BAD_MESSAGE, "hello.v must be an integer")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            ProtocolErrorCode.VERSION_MISMATCH,
            f"unsupported protocol version {version}",
        )

    platform = payload.get("platform")
    if not isinstance(platform, str) or not platform.strip():
        raise ProtocolError(ProtocolErrorCode.BAD_MESSAGE, "hello.platform must be a non-empty string")

    codec = payload.get("codec")
    if codec not in SUPPORTED_CODECS:
        raise ProtocolError(
            ProtocolErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported codec {codec!r}; supported: {', '.join(SUPPORTED_CODECS)}",
        )

    rate = payload.get("rate")
    if rate not in SUPPORTED_SAMPLE_RATES:
        raise ProtocolError(
            ProtocolErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported sample rate {rate!r}; supported: {SUPPORTED_SAMPLE_RATES}",
        )

    channels = payload.get("channels")
    if channels != SUPPORTED_CHANNELS:
        raise ProtocolError(
            ProtocolErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported channel count {channels!r}; only mono is supported",
        )

    return ClientHello(
        protocol_version=version,
        platform=platform.strip(),
        codec=codec,
        rate=rate,
        channels=channels,
    )


def server_message(message_type: ServerMessageType, **fields: Any) -> dict[str, Any]:
    """Build a JSON-ready server message.

    Raises:
        ProtocolError: ``message_type`` is not a known server message type.
    """
    if not isinstance(message_type, ServerMessageType):
        raise ProtocolError(
            ProtocolErrorCode.BAD_MESSAGE,
            f"unknown server message type {message_type!r}",
        )
    return {"t": str(message_type), **fields}


def error_message(code: ProtocolErrorCode | str, detail: str) -> dict[str, Any]:
    """Build an ``error`` message from a protocol error code."""
    return server_message(ServerMessageType.ERROR, code=str(code), message=detail)


def encode_message(message: Mapping[str, Any]) -> str:
    """Serialize a server message for the wire (non-ASCII kept readable)."""
    return json.dumps(message, ensure_ascii=False)
