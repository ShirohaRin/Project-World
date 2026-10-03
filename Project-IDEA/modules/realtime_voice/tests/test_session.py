"""Voice session state machine tests."""

from __future__ import annotations

import pytest

from modules.realtime_voice.contract.protocols import (
    PROTOCOL_VERSION,
    ClientHello,
    ProtocolError,
    ProtocolErrorCode,
)
from modules.realtime_voice.server.session import SessionState, VoiceSession


def _session() -> VoiceSession:
    return VoiceSession(session_id="test-session")


def _hello(rate: int = 16000) -> ClientHello:
    return ClientHello(
        protocol_version=PROTOCOL_VERSION,
        platform="android",
        codec="pcm16",
        rate=rate,
        channels=1,
    )


def test_new_session_is_idle() -> None:
    session = _session()

    assert session.state is SessionState.IDLE
    assert session.hello is None
    assert session.current_speech_id is None


def test_apply_hello_reports_negotiated_audio() -> None:
    session = _session()

    message = session.apply_hello(_hello(rate=48000))

    assert message["t"] == "session_started"
    assert message["session_id"] == "test-session"
    assert message["protocol"] == PROTOCOL_VERSION
    assert message["audio"] == {"codec": "pcm16", "rate": 48000, "channels": 1}
    assert session.state is SessionState.IDLE


def test_apply_hello_twice_is_rejected() -> None:
    session = _session()
    session.apply_hello(_hello())

    with pytest.raises(ProtocolError) as excinfo:
        session.apply_hello(_hello())

    assert excinfo.value.code is ProtocolErrorCode.INVALID_STATE


def test_start_moves_to_listening() -> None:
    session = _session()

    session.start()

    assert session.state is SessionState.LISTENING


def test_start_twice_is_rejected() -> None:
    session = _session()
    session.start()

    with pytest.raises(ProtocolError) as excinfo:
        session.start()

    assert excinfo.value.code is ProtocolErrorCode.INVALID_STATE


def test_audio_rejected_before_start() -> None:
    session = _session()

    with pytest.raises(ProtocolError) as excinfo:
        session.note_audio_frame(640)

    assert excinfo.value.code is ProtocolErrorCode.INVALID_STATE
    assert session.frames_received == 0


def test_audio_counted_while_listening() -> None:
    session = _session()
    session.start()

    session.note_audio_frame(640)
    session.note_audio_frame(320)

    assert session.frames_received == 2
    assert session.bytes_received == 960


def test_audio_rejected_after_close() -> None:
    session = _session()
    session.start()
    session.close()

    with pytest.raises(ProtocolError):
        session.note_audio_frame(640)


def test_empty_text_is_rejected() -> None:
    session = _session()
    session.start()

    with pytest.raises(ProtocolError) as excinfo:
        session.accept_text("   ")

    assert excinfo.value.code is ProtocolErrorCode.BAD_MESSAGE
    assert session.state is SessionState.LISTENING


def test_text_moves_to_thinking() -> None:
    session = _session()
    session.start()

    session.accept_text("你好")

    assert session.state is SessionState.THINKING


def test_text_before_start_is_rejected() -> None:
    session = _session()

    with pytest.raises(ProtocolError) as excinfo:
        session.accept_text("你好")

    assert excinfo.value.code is ProtocolErrorCode.INVALID_STATE


def test_begin_speech_allocates_increasing_ids() -> None:
    session = _session()
    session.start()

    first = session.begin_speech()
    assert first == "s1"

    session.interrupt()
    second = session.begin_speech()
    assert second == "s2"


def test_interrupt_returns_speech_id_and_resumes_listening() -> None:
    session = _session()
    session.start()
    speech_id = session.begin_speech()

    interrupted = session.interrupt()

    assert interrupted == speech_id
    assert session.current_speech_id is None
    assert session.state is SessionState.LISTENING


def test_interrupt_without_active_speech_returns_none() -> None:
    session = _session()
    session.start()

    assert session.interrupt() is None
    assert session.state is SessionState.LISTENING


def test_interrupt_from_thinking_returns_to_listening() -> None:
    session = _session()
    session.start()
    session.accept_text("你好")

    assert session.interrupt() is None
    assert session.state is SessionState.LISTENING


def test_close_is_idempotent_and_clears_speech() -> None:
    session = _session()
    session.start()
    session.begin_speech()

    session.close()
    session.close()

    assert session.state is SessionState.CLOSED
    assert session.current_speech_id is None
