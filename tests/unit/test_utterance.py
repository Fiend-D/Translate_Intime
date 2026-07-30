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


def test_pending_pcm_does_not_create_false_speech_gap():
    buf = UtteranceBuffer(end_silence_ms=60, min_ms=50, max_ms=5000)
    buf.push(_pcm_ms(100))
    time.sleep(0.04)
    buf.mark_capture_active()
    time.sleep(0.04)
    assert buf.poll() is None
    time.sleep(0.04)
    assert buf.poll() == _pcm_ms(100)


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


def _quiet_pcm_ms(ms: int, sample_rate: int = 16000) -> bytes:
    samples = int(sample_rate * ms / 1000)
    return b"\x00\x00" * samples


def test_max_ms_force_flush_continuous_loud_audio():
    """Continuous loud game audio must hard-flush at max_ms without a quiet gap."""
    buf = UtteranceBuffer(
        end_silence_ms=5000,
        min_ms=50,
        max_ms=300,
        soft_split_ms=10_000,
        soft_split_quiet_ms=280,
        tail_rms_threshold=0.003,
    )
    # Push loud chunks that never quiet — soft-split must not fire; max_ms must.
    total = 0
    flushed = None
    while total < 500:
        chunk = _pcm_ms(50)
        buf.push(chunk)
        total += 50
        flushed = buf.poll()
        if flushed is not None:
            break
    assert flushed is not None
    assert len(flushed) >= len(_pcm_ms(300))


def test_soft_split_requires_quiet_window():
    """Soft-split only after soft_split_ms AND a long quiet window."""
    buf = UtteranceBuffer(
        end_silence_ms=5000,
        min_ms=50,
        max_ms=20000,
        soft_split_ms=600,
        soft_split_quiet_ms=200,
        tail_rms_threshold=0.003,
    )
    # Loud audio past soft_split threshold but quiet window too short / loud.
    buf.push(_pcm_ms(500))
    buf.push(_pcm_ms(150))  # still loud tail
    assert buf.poll() is None
    # Append long quiet window → soft split.
    buf.push(_quiet_pcm_ms(220))
    chunk = buf.poll()
    assert chunk is not None
