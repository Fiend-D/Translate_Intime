"""Regression tests for continuous Windows loopback buffering."""

import numpy as np

from src.audio.device_guard import find_preferred_system_loopback, resolve_capture_backend
from src.audio.wasapi_process_loopback import (
    ProcessExcludeLoopback,
    is_process_exclude_available,
)


def test_fake_process_exclude_backend_is_not_advertised() -> None:
    assert is_process_exclude_available() is False


def test_partial_capture_block_waits_instead_of_padding_silence() -> None:
    capture = ProcessExcludeLoopback(sample_rate=16000)
    capture._running = True
    first = np.full(320, 0.25, dtype=np.float32)
    second = np.full(320, -0.25, dtype=np.float32)
    capture._buffer.append(first)

    assert capture.read_float32(640) is None
    assert len(capture._buffer) == 1

    capture._buffer.append(second)
    result = capture.read_float32(640)

    assert result is not None
    assert np.array_equal(result[:320], first)
    assert np.array_equal(result[320:], second)


def test_legacy_process_exclude_config_falls_back_to_real_loopback() -> None:
    devices = [
        {"id": "wasapi_proc_exclude:", "name": "legacy fake process exclude"},
        {"id": "wasapi_loopback:Speakers", "name": "[Loopback] Speakers"},
    ]

    selected = resolve_capture_backend(
        "driverless",
        configured="wasapi_proc_exclude:",
        devices=devices,
    )

    assert selected == "wasapi_loopback:Speakers"
    assert find_preferred_system_loopback(devices) == "wasapi_loopback:Speakers"
