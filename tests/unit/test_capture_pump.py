"""PyAudio capture: native-rate fallback, off-callback delivery, stall detection."""

from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pytest

from src.core import audio_capture
from src.core.audio_capture import AudioCapture
from src.models.enums import Direction
from src.utils.audio_utils import StreamResampler


def test_chunked_resampling_matches_a_single_pass() -> None:
    sr = 44100
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)).astype(np.float32)
    whole = StreamResampler(sr, 16000).process(tone)
    stream = StreamResampler(sr, 16000)
    step = 1771
    parts = [stream.process(tone[i : i + step]) for i in range(0, len(tone), step)]

    np.testing.assert_array_equal(np.concatenate(parts), whole)


class _FakeStream:
    def __init__(self, callback) -> None:
        self.callback = callback

    def start_stream(self) -> None:
        return None

    def stop_stream(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def fake_pyaudio(monkeypatch):
    opened: list[dict] = []

    class PyAudio:
        def open(self, **kw):
            opened.append(kw)
            if kw["rate"] == 16000:
                raise OSError("[Errno -9997] Invalid sample rate")
            return _FakeStream(kw["stream_callback"])

        def get_device_info_by_index(self, _i):
            return {"defaultSampleRate": 48000.0, "maxInputChannels": 2}

        def terminate(self) -> None:
            return None

    module = types.SimpleNamespace(PyAudio=PyAudio, paInt16=8, paContinue=0)
    monkeypatch.setitem(sys.modules, "pyaudio", module)
    return opened


def test_wasapi_style_device_opens_at_native_rate_and_is_downmixed(fake_pyaudio) -> None:
    capture = AudioCapture(Direction.OUTBOUND, 3)
    got: list[bytes] = []
    callback_thread: list[str] = []
    capture.on_pcm = lambda pcm: (
        got.append(pcm),
        callback_thread.append(threading.current_thread().name),
    )
    capture.start()
    try:
        assert [o["rate"] for o in fake_pyaudio] == [16000, 48000]
        stereo = np.full((1920, 2), 8000, dtype=np.int16).tobytes()  # 40 ms @ 48 kHz
        cb = fake_pyaudio[-1]["stream_callback"]
        for _ in range(5):
            cb(stereo, 1920, {}, 0)
        deadline = time.monotonic() + 2
        while sum(len(p) for p in got) < 4 * 640 * 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        capture.stop()

    samples = np.frombuffer(b"".join(got), dtype=np.int16)
    assert 4 * 640 <= len(samples) <= 5 * 640
    assert callback_thread[0].startswith("capture-")


def test_device_that_stops_delivering_reports_an_error(fake_pyaudio, monkeypatch) -> None:
    monkeypatch.setattr(audio_capture, "_STALL_SECONDS", 0.2)
    capture = AudioCapture(Direction.INBOUND, 3)
    errors: list[Exception] = []
    capture.on_error = errors.append
    capture.start()
    deadline = time.monotonic() + 3
    while not errors and time.monotonic() < deadline:
        time.sleep(0.05)
    capture.stop()

    assert errors and "无数据" in str(errors[0])
