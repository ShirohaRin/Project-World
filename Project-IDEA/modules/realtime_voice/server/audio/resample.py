"""Dependency-free PCM16 resampling for voice audio.

Voice-grade only: enough for recognition input and for playback, with no
third-party dependency. If a higher-quality path is needed later, soxr can be
slotted in behind the same function without touching callers.
"""

from __future__ import annotations

import sys
from array import array
from math import ceil
from typing import Final

INTERNAL_SAMPLE_RATE: Final[int] = 16_000
"""Sample rate every inbound recognition stream is normalized to."""

_BYTES_PER_SAMPLE: Final[int] = 2
_INT16_MIN: Final[int] = -32_768
_INT16_MAX: Final[int] = 32_767


def pcm16_duration_ms(payload_size: int, sample_rate: int) -> float:
    """Return the duration in milliseconds of a mono PCM16 payload."""
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    return payload_size / _BYTES_PER_SAMPLE / sample_rate * 1000.0


def resample_pcm16(data: bytes, *, src_rate: int, dst_rate: int) -> bytes:
    """Resample mono PCM16 audio from ``src_rate`` to ``dst_rate``.

    Downsampling first passes a moving average, which suppresses the aliasing
    that would otherwise fold high frequencies back onto the speech band;
    linear interpolation then produces the target rate. Rates that differ by a
    non-integer factor get the same treatment with a rounded filter length.
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if src_rate == dst_rate or not data:
        return data
    if len(data) % _BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must have an even byte length")

    samples = _to_samples(data)
    if len(samples) < 2:
        return data

    if src_rate > dst_rate:
        samples = _moving_average(samples, ceil(src_rate / dst_rate))
    return _to_bytes(_linear_resample(samples, src_rate, dst_rate))


def _to_samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _to_bytes(samples: array) -> bytes:
    if sys.byteorder == "little":
        return samples.tobytes()
    swapped = array("h", samples)
    swapped.byteswap()
    return swapped.tobytes()


def _moving_average(samples: array, window: int) -> array:
    """Causal moving average used as a cheap anti-alias filter.

    Causal rather than centred: the filter shifts the signal by ``window - 1``
    samples (a fraction of a millisecond at voice rates) and in exchange the
    running sum keeps the cost at one add and one subtract per sample.
    """
    if window <= 1:
        return samples
    out = array("h", bytes(_BYTES_PER_SAMPLE * len(samples)))
    running = 0
    for index, value in enumerate(samples):
        running += value
        if index >= window:
            running -= samples[index - window]
            out[index] = _clamp(round(running / window))
        else:
            out[index] = _clamp(round(running / (index + 1)))
    return out


def _linear_resample(samples: array, src_rate: int, dst_rate: int) -> array:
    src_len = len(samples)
    dst_len = max(1, round(src_len * dst_rate / src_rate))
    step = src_rate / dst_rate
    last = src_len - 1
    out = array("h", bytes(_BYTES_PER_SAMPLE * dst_len))
    for index in range(dst_len):
        position = index * step
        base = int(position)
        if base >= last:
            # 采样点落在末尾之后：复制最后一个样本，避免越界读取
            out[index] = samples[last]
            continue
        fraction = position - base
        value = samples[base] + (samples[base + 1] - samples[base]) * fraction
        out[index] = _clamp(round(value))
    return out


def _clamp(value: int) -> int:
    return max(_INT16_MIN, min(_INT16_MAX, value))
