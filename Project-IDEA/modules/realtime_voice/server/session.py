"""Voice session state machine for the realtime voice module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from ..contract.protocols import (
    ClientHello,
    ProtocolError,
    ProtocolErrorCode,
    ServerMessageType,
    server_message,
)


class SessionState(StrEnum):
    """Lifecycle states of a single voice session."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


# Allowed transitions. Every state change goes through _transition(), so an
# illegal move surfaces as an `invalid_state` error instead of silently
# corrupting the session.
_TRANSITIONS: Final[dict[SessionState, frozenset[SessionState]]] = {
    SessionState.IDLE: frozenset({SessionState.LISTENING, SessionState.CLOSED}),
    SessionState.LISTENING: frozenset(
        {SessionState.THINKING, SessionState.SPEAKING, SessionState.CLOSED}
    ),
    SessionState.THINKING: frozenset(
        {SessionState.LISTENING, SessionState.SPEAKING, SessionState.CLOSED}
    ),
    SessionState.SPEAKING: frozenset({SessionState.LISTENING, SessionState.CLOSED}),
    SessionState.CLOSED: frozenset(),
}


@dataclass(slots=True)
class VoiceSession:
    """Mutable state of one WebSocket voice session.

    Audio is only counted here in M0. Recognition, dialogue and synthesis are
    wired in during M1-M5; the state machine and the speech-id bookkeeping are
    final already because every later stage depends on them.
    """

    session_id: str
    state: SessionState = SessionState.IDLE
    hello: ClientHello | None = None
    frames_received: int = 0
    bytes_received: int = 0
    speech_counter: int = 0
    current_speech_id: str | None = None

    @property
    def audio_rate(self) -> int:
        """Sample rate declared in `hello`, or the internal rate before it."""
        return self.hello.rate if self.hello is not None else INTERNAL_SAMPLE_RATE

    def _transition(self, target: SessionState) -> None:
        if target not in _TRANSITIONS[self.state]:
            raise ProtocolError(
                ProtocolErrorCode.INVALID_STATE,
                f"cannot move from '{self.state}' to '{target}'",
            )
        self.state = target

    def apply_hello(self, hello: ClientHello) -> dict[str, Any]:
        """Record the negotiated audio format and build `session_started`."""
        if self.hello is not None:
            raise ProtocolError(
                ProtocolErrorCode.INVALID_STATE,
                "hello has already been received for this session",
            )
        self.hello = hello
        return server_message(
            ServerMessageType.SESSION_STARTED,
            session_id=self.session_id,
            protocol=hello.protocol_version,
            audio={"codec": hello.codec, "rate": hello.rate, "channels": hello.channels},
        )

    def start(self) -> None:
        """Move from idle to listening, enabling audio intake."""
        self._transition(SessionState.LISTENING)

    def accept_text(self, text: str) -> None:
        """Accept a text turn and move to thinking."""
        if not text.strip():
            raise ProtocolError(ProtocolErrorCode.BAD_MESSAGE, "text must not be empty")
        self._transition(SessionState.THINKING)

    def note_audio_frame(self, payload_size: int) -> None:
        """Count an inbound audio frame.

        Audio is accepted while listening, and while speaking because that is
        how the server detects the user talking over the assistant (M5).
        """
        if self.state not in (SessionState.LISTENING, SessionState.SPEAKING):
            raise ProtocolError(
                ProtocolErrorCode.INVALID_STATE,
                f"audio is not accepted in state '{self.state}'",
            )
        self.frames_received += 1
        self.bytes_received += payload_size

    def begin_speech(self) -> str:
        """Open a new assistant turn and return its speech id."""
        self.speech_counter += 1
        self.current_speech_id = f"s{self.speech_counter}"
        self._transition(SessionState.SPEAKING)
        return self.current_speech_id

    def interrupt(self) -> str | None:
        """Invalidate the running assistant turn.

        Returns the speech id the client must drop, or None when nothing was
        speaking. Dropping the id is what keeps late audio from an old turn
        out of the client's playback queue.
        """
        interrupted = self.current_speech_id
        self.current_speech_id = None
        if self.state in (SessionState.THINKING, SessionState.SPEAKING):
            self._transition(SessionState.LISTENING)
        return interrupted

    def close(self) -> None:
        """Force the session closed; safe to call more than once."""
        self.state = SessionState.CLOSED
        self.current_speech_id = None
