"""Downlink jitter buffer tests."""

from __future__ import annotations

import pytest

from modules.realtime_voice.server.audio.jitter import DownlinkJitterBuffer


def test_in_order_chunks_release_immediately() -> None:
    buffer = DownlinkJitterBuffer()

    assert buffer.push(1, b"a") == [b"a"]
    assert buffer.push(2, b"b") == [b"b"]
    assert buffer.pending == 0
    assert buffer.next_seq == 3


def test_out_of_order_chunks_are_reordered() -> None:
    buffer = DownlinkJitterBuffer()

    assert buffer.push(2, b"b") == []
    assert buffer.push(4, b"d") == []
    assert buffer.pending == 2

    # 2 号块一到就补齐了 1-2，4 号块还要等 3 号
    assert buffer.push(1, b"a") == [b"a", b"b"]
    assert buffer.pending == 1
    assert buffer.push(3, b"c") == [b"c", b"d"]
    assert buffer.pending == 0


def test_buffer_waits_for_the_configured_start_sequence() -> None:
    buffer = DownlinkJitterBuffer(start_seq=1)

    assert buffer.next_seq == 1
    assert buffer.push(2, b"b") == []
    assert buffer.push(1, b"a") == [b"a", b"b"]
    assert buffer.next_seq == 3


def test_duplicate_sequence_is_idempotent() -> None:
    buffer = DownlinkJitterBuffer()
    buffer.push(2, b"b")

    assert buffer.push(2, b"b") == []
    assert buffer.push(1, b"a") == [b"a", b"b"]


def test_late_chunk_is_dropped() -> None:
    buffer = DownlinkJitterBuffer()
    buffer.push(1, b"a")

    assert buffer.push(1, b"stale") == []
    assert buffer.pending == 0


def test_chunk_beyond_the_window_is_dropped() -> None:
    buffer = DownlinkJitterBuffer(max_pending=3)
    buffer.push(1, b"a")

    # 3、4 号块未到，5 号块已超出窗口，直接丢弃而不是无限堆积
    assert buffer.push(5, b"e") == []
    assert buffer.pending == 0


def test_advance_past_gap_releases_buffered_chunks() -> None:
    buffer = DownlinkJitterBuffer()
    buffer.push(1, b"a")
    buffer.push(4, b"d")
    buffer.push(5, b"e")
    assert buffer.pending == 2

    # 2、3 号块永久缺失时，不能一直等下去
    assert buffer.advance_past_gap() == [b"d", b"e"]
    assert buffer.next_seq == 6


def test_advance_past_gap_without_pending_is_a_no_op() -> None:
    buffer = DownlinkJitterBuffer()
    buffer.push(1, b"a")

    assert buffer.advance_past_gap() == []


def test_reset_restores_the_start_sequence() -> None:
    buffer = DownlinkJitterBuffer(start_seq=1)
    buffer.push(2, b"b")

    buffer.reset()

    assert buffer.pending == 0
    assert buffer.next_seq == 1
    assert buffer.push(1, b"a") == [b"a"]


def test_invalid_window_is_rejected() -> None:
    with pytest.raises(ValueError):
        DownlinkJitterBuffer(max_pending=0)


def test_negative_start_sequence_is_rejected() -> None:
    with pytest.raises(ValueError):
        DownlinkJitterBuffer(start_seq=-1)
