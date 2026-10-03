"""Wire protocol contract tests."""

from __future__ import annotations

import json

import pytest

from modules.realtime_voice.contract.protocols import (
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    MAX_FRAME_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    ProtocolErrorCode,
    ServerMessageType,
    decode_audio_frame,
    encode_audio_frame,
    encode_message,
    error_message,
    parse_client_hello,
    server_message,
)


def _hello_payload(**overrides: object) -> dict[str, object]:
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


# --- binary audio frames ---------------------------------------------------


def test_audio_frame_roundtrip() -> None:
    payload = b"\x01\x02\x03\x04"

    frame = encode_audio_frame(7, payload)
    header, decoded = decode_audio_frame(frame)

    assert header.seq == 7
    assert header.version == PROTOCOL_VERSION
    assert header.frame_type == 1
    assert decoded == payload
    assert len(frame) == FRAME_HEADER_SIZE + len(payload)


def test_frame_header_layout_is_stable() -> None:
    frame = encode_audio_frame(1, b"\x00\x00")

    assert frame[:2] == FRAME_MAGIC
    assert frame[2] == PROTOCOL_VERSION
    assert frame[3] == 1
    # seq is uint32 little-endian at offset 4.
    assert frame[4:8] == b"\x01\x00\x00\x00"


def test_decode_rejects_short_frame() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(b"IV\x01\x01\x01")

    assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_decode_rejects_bad_magic() -> None:
    frame = encode_audio_frame(1, b"\x00\x00")

    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(b"XX" + frame[2:])

    assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_decode_rejects_version_mismatch() -> None:
    frame = bytearray(encode_audio_frame(1, b"\x00\x00"))
    frame[2] = PROTOCOL_VERSION + 1

    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(bytes(frame))

    assert excinfo.value.code is ProtocolErrorCode.VERSION_MISMATCH


def test_decode_rejects_unknown_frame_type() -> None:
    frame = bytearray(encode_audio_frame(1, b"\x00\x00"))
    frame[3] = 9

    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(bytes(frame))

    assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_decode_rejects_odd_pcm_length() -> None:
    frame = encode_audio_frame(1, b"\x00\x00") + b"\x00"

    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(frame)

    assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_decode_rejects_oversized_payload() -> None:
    header = encode_audio_frame(1, b"\x00\x00")[:FRAME_HEADER_SIZE]
    oversized = header + b"\x00" * (MAX_FRAME_PAYLOAD_BYTES + 2)

    with pytest.raises(ProtocolError) as excinfo:
        decode_audio_frame(oversized)

    assert excinfo.value.code is ProtocolErrorCode.FRAME_TOO_LARGE


def test_encode_rejects_seq_out_of_uint32_range() -> None:
    for seq in (0, -1, 0x1_0000_0000):
        with pytest.raises(ProtocolError) as excinfo:
            encode_audio_frame(seq, b"\x00\x00")

        assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_encode_rejects_empty_payload() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        encode_audio_frame(1, b"")

    assert excinfo.value.code is ProtocolErrorCode.BAD_FRAME


def test_encode_rejects_oversized_payload() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        encode_audio_frame(1, b"\x00" * (MAX_FRAME_PAYLOAD_BYTES + 2))

    assert excinfo.value.code is ProtocolErrorCode.FRAME_TOO_LARGE


# --- hello negotiation -----------------------------------------------------


@pytest.mark.parametrize("rate", [16000, 48000])
def test_parse_hello_accepts_supported_rates(rate: int) -> None:
    hello = parse_client_hello(_hello_payload(rate=rate))

    assert hello.rate == rate
    assert hello.codec == "pcm16"
    assert hello.channels == 1
    assert hello.protocol_version == PROTOCOL_VERSION
    assert hello.platform == "android"


def test_parse_hello_rejects_unsupported_codec() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(_hello_payload(codec="opus"))

    assert excinfo.value.code is ProtocolErrorCode.UNSUPPORTED_FORMAT


def test_parse_hello_rejects_unsupported_rate() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(_hello_payload(rate=8000))

    assert excinfo.value.code is ProtocolErrorCode.UNSUPPORTED_FORMAT


def test_parse_hello_rejects_stereo() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(_hello_payload(channels=2))

    assert excinfo.value.code is ProtocolErrorCode.UNSUPPORTED_FORMAT


def test_parse_hello_rejects_protocol_version_mismatch() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(_hello_payload(v=PROTOCOL_VERSION + 1))

    assert excinfo.value.code is ProtocolErrorCode.VERSION_MISMATCH


def test_parse_hello_rejects_boolean_version() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(_hello_payload(v=True))

    assert excinfo.value.code is ProtocolErrorCode.BAD_MESSAGE


def test_parse_hello_rejects_missing_platform() -> None:
    payload = _hello_payload()
    del payload["platform"]

    with pytest.raises(ProtocolError) as excinfo:
        parse_client_hello(payload)

    assert excinfo.value.code is ProtocolErrorCode.BAD_MESSAGE


# --- JSON messages ---------------------------------------------------------


def test_server_message_shapes() -> None:
    message = server_message(ServerMessageType.AUDIO_DONE, speech_id="s1")

    assert message == {"t": "audio_done", "speech_id": "s1"}


def test_server_message_rejects_unknown_type() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        server_message("not_a_type")  # type: ignore[arg-type]

    assert excinfo.value.code is ProtocolErrorCode.BAD_MESSAGE


def test_error_message_carries_code_and_detail() -> None:
    message = error_message(ProtocolErrorCode.BAD_FRAME, "frame magic mismatch")

    assert message == {
        "t": "error",
        "code": "bad_frame",
        "message": "frame magic mismatch",
    }


def test_encode_message_keeps_non_ascii_readable() -> None:
    encoded = encode_message(server_message(ServerMessageType.ASR_FINAL, text="你好"))

    assert "你好" in encoded
    assert json.loads(encoded)["t"] == "asr_final"
