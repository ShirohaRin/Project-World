"""Resampler tests."""

from __future__ import annotations

import array
import math
import sys

import pytest

from modules.realtime_voice.server.audio.resample import (
    INTERNAL_SAMPLE_RATE,
    pcm16_duration_ms,
    resample_pcm16,
)


def _samples(data: bytes) -> list[int]:
    values = array.array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return list(values)


def _samples_from(values: list[int]) -> bytes:
    packed = array.array("h", values)
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def _sine(rate: int, freq: float, ms: int, amplitude: int = 20000) -> bytes:
    count = rate * ms // 1000
    return _samples_from(
        [int(amplitude * math.sin(2 * math.pi * freq * index / rate)) for index in range(count)]
    )


def test_internal_rate_is_16k() -> None:
    assert INTERNAL_SAMPLE_RATE == 16000


@pytest.mark.parametrize(
    ("size", "rate", "expected"),
    [
        (640, 16000, 20.0),
        (1920, 48000, 20.0),
        (960, 24000, 20.0),
    ],
)
def test_duration_helper(size: int, rate: int, expected: float) -> None:
    assert pcm16_duration_ms(size, rate) == pytest.approx(expected)


def test_duration_helper_rejects_bad_rate() -> None:
    with pytest.raises(ValueError):
        pcm16_duration_ms(640, 0)


def test_same_rate_returns_input_unchanged() -> None:
    data = _sine(16000, 200.0, 20)

    assert resample_pcm16(data, src_rate=16000, dst_rate=16000) is data


def test_empty_input_returns_empty() -> None:
    assert resample_pcm16(b"", src_rate=48000, dst_rate=16000) == b""


def test_downsample_48k_to_16k_thirds_the_sample_count() -> None:
    data = _sine(48000, 200.0, 20)

    result = resample_pcm16(data, src_rate=48000, dst_rate=16000)

    assert len(_samples(data)) == 960
    assert len(_samples(result)) == 320


def test_upsample_16k_to_48k_triples_the_sample_count() -> None:
    data = _sine(16000, 200.0, 20)

    result = resample_pcm16(data, src_rate=16000, dst_rate=48000)

    assert len(_samples(result)) == 960


def test_upsample_16k_to_24k_uses_half_step_interpolation() -> None:
    data = _sine(16000, 200.0, 20)

    result = resample_pcm16(data, src_rate=16000, dst_rate=24000)

    assert len(_samples(result)) == 480


def test_downsample_24k_to_16k_uses_non_integer_ratio() -> None:
    data = _sine(24000, 200.0, 20)

    result = resample_pcm16(data, src_rate=24000, dst_rate=16000)

    assert len(_samples(result)) == 320


def test_constant_signal_is_preserved_by_downsampling() -> None:
    data = _samples_from([1000] * 960)

    result = _samples(resample_pcm16(data, src_rate=48000, dst_rate=16000))

    assert all(abs(value - 1000) <= 1 for value in result)


def test_speech_band_tone_survives_downsampling() -> None:
    data = _sine(48000, 200.0, 40)

    result = _samples(resample_pcm16(data, src_rate=48000, dst_rate=16000))

    # 200 Hz 远低于新的奈奎斯特频率，抗混叠滤波不应把它削掉
    assert max(abs(value) for value in result) > 18000


def test_low_frequency_tone_round_trips_through_up_and_down() -> None:
    original = _sine(16000, 200.0, 40)

    upsampled = resample_pcm16(original, src_rate=16000, dst_rate=48000)
    restored = resample_pcm16(upsampled, src_rate=48000, dst_rate=16000)

    source = _samples(original)
    target = _samples(restored)
    assert len(target) == len(source)
    # 允许 2 个样本的重采样时延误差
    worst = max(
        abs(source[index] - target[index])
        for index in range(2, len(source) - 2)
    )
    assert worst < 800


def test_output_stays_within_int16_range() -> None:
    data = _samples_from([32767, -32768] * 480)

    result = _samples(resample_pcm16(data, src_rate=48000, dst_rate=16000))

    assert all(-32768 <= value <= 32767 for value in result)


def test_odd_byte_length_is_rejected() -> None:
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00\x00\x00", src_rate=48000, dst_rate=16000)


@pytest.mark.parametrize(("src", "dst"), [(0, 16000), (48000, 0), (-1, 16000)])
def test_invalid_rates_are_rejected(src: int, dst: int) -> None:
    with pytest.raises(ValueError):
        resample_pcm16(_sine(16000, 200.0, 1), src_rate=src, dst_rate=dst)
