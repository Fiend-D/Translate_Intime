"""Unit tests for SpeechGate and pre-roll buffering."""

from __future__ import annotations

import array

from src.core.audio_pre_roll import AudioPreRoll
from src.core.speech_gate import SpeechGate


class _PendingSilero:
    def __init__(self) -> None:
        self.started = False

    def start_loading(self) -> None:
        self.started = True

    def is_available(self) -> bool:
        return False


def _pcm(value: int, samples: int = 320) -> bytes:
    return array.array("h", [value] * samples).tobytes()


def test_audio_pre_roll_keeps_recent_bytes() -> None:
    pre = AudioPreRoll(sample_rate=16000, preroll_ms=20)
    pre.push(b"a" * 200)
    pre.push(b"b" * 600)

    assert pre.size_bytes == 640
    assert pre.drain() == b"a" * 40 + b"b" * 600
    assert pre.drain() == b""


def test_rms_accepts_loud_audio() -> None:
    gate = SpeechGate(backend="rms", sensitivity="medium", open_ms=20, hangover_ms=20)

    assert gate.accept(_pcm(0)) is False
    assert gate.accept(_pcm(2000)) is True
    assert gate.stats().open is True


def test_preroll_returned_on_rising_edge() -> None:
    gate = SpeechGate(
        backend="rms",
        sensitivity="medium",
        open_ms=20,
        hangover_ms=20,
        preroll_ms=60,
    )
    silence = _pcm(0)
    loud = _pcm(3000)

    assert gate.process(silence).passed is False
    result = gate.process(loud)

    assert result.passed is True
    assert result.opened_now is True
    assert result.preroll == silence


def test_auto_backend_uses_rms_while_silero_loads() -> None:
    silero = _PendingSilero()
    gate = SpeechGate(backend="auto", silero_engine=silero)

    assert silero.started is True
    assert gate.backend == "rms"
    assert gate.accept(_pcm(3000)) is True

