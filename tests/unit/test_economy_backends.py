"""Economy engine end-to-end with injected mock backends (no network)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.engines.base import EngineCallbacks
from src.engines.pipeline.asr import UnconfiguredAsr
from src.engines.pipeline.engine import EconomyPipelineEngine
from src.engines.pipeline.mt import UnconfiguredMt
from src.engines.pipeline.tts import UnconfiguredTts
from src.models.config import AppConfigModel
from src.models.enums import Direction


def _callbacks():
    return EngineCallbacks(
        on_source_text=MagicMock(),
        on_translated_text=MagicMock(),
        on_audio=MagicMock(),
        on_error=MagicMock(),
        on_status=MagicMock(),
        on_usage=MagicMock(),
        on_engine_status=MagicMock(),
        should_defer_rotate=lambda *_a: False,
    )


class _MockAsr:
    configured = True

    def __init__(self, text: str = "hello") -> None:
        self._text = text
        self.started = False
        self.calls: list[tuple[bytes, str]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        self.calls.append((pcm, language))
        return self._text


class _MockMt:
    configured = True

    def __init__(self, out: str = "你好") -> None:
        self._out = out
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        del text, source_lang, target_lang
        return self._out


class _MockTts:
    configured = True

    def __init__(self, pcm: bytes | None = None) -> None:
        self._pcm = pcm if pcm is not None else (b"\x00\x00" * 160)
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del text, language
        return self._pcm


def _pcm_ms(ms: int) -> bytes:
    return b"\x00\x01" * int(16000 * ms / 1000)


class _MockNllbMt(_MockMt):
    """Stand-in for NllbCt2Mt in e2e (no download)."""

    warming_up = False


class _MockKokoroTts(_MockTts):
    """Stand-in for KokoroOnnxTts in e2e (no download)."""

    warming_up = False


def test_economy_e2e_one_utterance(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("hello world")
    mt = _MockNllbMt("你好世界")
    audio = b"\x01\x00" * 320
    tts = _MockKokoroTts(audio)
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_mt_backend="nllb",
        economy_tts_backend="kokoro",
        economy_utterance_silence_ms=80,
        economy_utterance_min_ms=50,
        economy_utterance_max_ms=5000,
        glossary={"世界": "WORLD"},
    )
    engine = EconomyPipelineEngine(
        config=config, callbacks=cb, asr=asr, mt=mt, tts=tts
    )
    assert engine.start_direction(Direction.OUTBOUND, play_voice=True) is True
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    time.sleep(0.12)
    deadline = time.time() + 2.0
    while time.time() < deadline and not cb.on_translated_text.called:
        time.sleep(0.05)
    engine.close()

    cb.on_source_text.assert_called()
    src_args = cb.on_source_text.call_args[0]
    assert src_args[0] == Direction.OUTBOUND
    assert src_args[1] == "hello world"
    assert src_args[2] is True

    cb.on_translated_text.assert_called()
    tr_args = cb.on_translated_text.call_args[0]
    assert tr_args[1] == "你好WORLD"
    cb.on_audio.assert_called()
    assert cb.on_audio.call_args[0][1] == audio


def test_start_direction_false_without_asr(tmp_path):
    cb = _callbacks()
    config = AppConfigModel(log_dir=str(tmp_path / "logs"), translation_mode="economy")
    engine = EconomyPipelineEngine(
        config=config,
        callbacks=cb,
        asr=UnconfiguredAsr(),
        mt=UnconfiguredMt(),
        tts=UnconfiguredTts(),
    )
    assert engine.start_direction(Direction.OUTBOUND) is False
    cb.on_error.assert_called()
