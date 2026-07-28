"""Unit tests for audio utilities."""

import numpy as np

from src.utils.audio_utils import (
    float32_to_pcm16,
    pcm16_to_float32,
    resample,
    secure_clear,
)


def test_pcm16_to_float32_roundtrip() -> None:
    original = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
    float_data = pcm16_to_float32(original.tobytes())
    pcm_data = float32_to_pcm16(float_data)
    restored = np.frombuffer(pcm_data, dtype=np.int16)
    assert np.allclose(original, restored, atol=1)


def test_resample_changes_length() -> None:
    audio = np.sin(2 * np.pi * 440 * np.linspace(0, 1, 16000)).astype(np.float32)
    resampled = resample(audio, 16000, 22050)
    assert len(resampled) == 22050


def test_resample_same_rate_is_noop() -> None:
    audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    resampled = resample(audio, 16000, 16000)
    assert np.array_equal(audio, resampled)


def test_secure_clear_bytearray() -> None:
    buf = bytearray(b"secret audio data")
    secure_clear(buf)
    assert all(b == 0 for b in buf)


def test_secure_clear_numpy() -> None:
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    secure_clear(arr)
    assert np.all(arr == 0.0)
