"""Downlink jitter buffer for synthesized audio."""

from __future__ import annotations

from typing import Final

DEFAULT_START_SEQ: Final[int] = 1
DEFAULT_MAX_PENDING: Final[int] = 64


class DownlinkJitterBuffer:
    """Release synthesized chunks in sequence order.

    Chunks arrive from the synthesis side in completion order rather than
    sequence order. The buffer holds a chunk until its predecessors have been
    released, drops anything older than the last released chunk, and bounds
    the pending window so one missing chunk cannot make it grow forever.

    The server numbers the chunks it generates, so the first expected sequence
    is known up front (`start_seq`) rather than guessed from whichever chunk
    arrives first.
    """

    def __init__(
        self,
        *,
        start_seq: int = DEFAULT_START_SEQ,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        if start_seq < 0:
            raise ValueError("start_seq must not be negative")
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._start_seq = start_seq
        self._max_pending = max_pending
        self._pending: dict[int, bytes] = {}
        self._next_seq = start_seq

    @property
    def pending(self) -> int:
        """Number of chunks waiting for an earlier chunk."""
        return len(self._pending)

    @property
    def next_seq(self) -> int:
        """Sequence number currently being waited for."""
        return self._next_seq

    def push(self, seq: int, payload: bytes) -> list[bytes]:
        """Add one chunk and return the chunks now ready, in sequence order."""
        if seq < self._next_seq:
            # 迟到块：它所属的位置已经释放过了，直接丢弃
            return []
        if seq - self._next_seq >= self._max_pending:
            # 超出窗口：宁可丢弃新块，也不让待发队列无界增长
            return []
        self._pending[seq] = payload
        return self._drain()

    def advance_past_gap(self) -> list[bytes]:
        """Skip the chunk being waited for and release what follows.

        Called when a chunk never arrives; without this, a single loss would
        stall the stream until the pending window filled up.
        """
        if not self._pending:
            return []
        self._next_seq = min(self._pending)
        return self._drain()

    def reset(self) -> None:
        """Drop everything buffered; called when a turn is interrupted."""
        self._pending.clear()
        self._next_seq = self._start_seq

    def _drain(self) -> list[bytes]:
        ready: list[bytes] = []
        while self._next_seq in self._pending:
            ready.append(self._pending.pop(self._next_seq))
            self._next_seq += 1
        return ready
