"""UtteranceBuffer silence / max flush behavior."""

from __future__ import annotations

import time

from src.engines.pipeline.utterance import UtteranceBuffer


def _pcm_ms(ms: int, sample_rate: int = 16000) -> bytes:
    samples = int(sample_rate * ms / 1000)
    return b"\x00\x01" * samples


def test_silence_flush_after_gap():
    buf = UtteranceBuffer(end_silence_ms=100, min_ms=50, max_ms=5000)
    buf.push(_pcm_ms(80))
    assert buf.poll() is None
    time.sleep(0.12)
    chunk = buf.poll()
    assert chunk is not None
    assert len(chunk) == len(_pcm_ms(80))
    assert buf.poll() is None


def test_no_flush_before_min_ms():
    buf = UtteranceBuffer(end_silence_ms=50, min_ms=400, max_ms=5000)
    buf.push(_pcm_ms(100))
    time.sleep(0.08)
    assert buf.poll() is None


def test_max_ms_force_flush():
    buf = UtteranceBuffer(end_silence_ms=5000, min_ms=50, max_ms=200)
    buf.push(_pcm_ms(250))
    chunk = buf.poll()
    assert chunk is not None
    assert len(chunk) == len(_pcm_ms(250))


def test_flush_manual():
    buf = UtteranceBuffer()
    assert buf.flush() is None
    buf.push(_pcm_ms(100))
    out = buf.flush()
    assert out is not None
    assert buf.flush() is None
