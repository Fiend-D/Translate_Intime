"""Unit tests for TTS barge-in helpers."""

from __future__ import annotations

import array
import time

from src.core.pipeline import TranslationPipeline
from src.core.speech_gate import SpeechGate
from src.models.config import AppConfigModel
from src.models.enums import Direction


def _pcm(value: int, samples: int = 320) -> bytes:
    return array.array("h", [value] * samples).tobytes()


class _FakeEngine:
    def __init__(self) -> None:
        self.engine_id = "volc"
        self.active_directions = frozenset({Direction.OUTBOUND})
        self.sent: list[bytes] = []

    def send_pcm(self, direction: Direction, data: bytes) -> None:
        assert direction == Direction.OUTBOUND
        self.sent.append(data)

    def close(self) -> None:
        return None


class _FakePlayer:
    def __init__(self) -> None:
        self.cleared = False
        self.is_playing = False
        self.queue_size = 0

    def clear_queue(self) -> None:
        self.cleared = True


def test_barge_in_clears_tts_and_flushes_buffer(tmp_path) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        vad_backend="rms",
        vad_barge_in_ms=40,
    )
    pipeline = TranslationPipeline(config)
    pipeline._engine = _FakeEngine()
    pipeline._player = _FakePlayer()
    pipeline._vad_outbound = SpeechGate(
        backend="rms",
        sensitivity="medium",
        open_ms=20,
        hangover_ms=80,
        preroll_ms=60,
    )
    pipeline._tts_playing_until = time.time() + 5.0

    silence = _pcm(0)
    loud = _pcm(3000)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, silence)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, loud)
    pipeline._barge_in_started_at[Direction.OUTBOUND] = time.time() - 0.1
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, loud)

    assert pipeline._player.cleared is True
    assert pipeline._tts_playing_until <= time.time()
    assert pipeline._engine.sent
    assert silence in pipeline._engine.sent[0]
    assert loud in pipeline._engine.sent[0]
