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
        self.calls: list[str] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        del source_lang, target_lang
        self.calls.append(text)
        # Echo mapping so multi-sentence tests can observe per-sentence calls.
        if isinstance(self._out, dict):
            return self._out.get(text, self._out.get("*", text))
        if self._out == "__echo__":
            return f"T({text})"
        return self._out


class _MockTts:
    configured = True

    def __init__(self, pcm: bytes | None = None) -> None:
        self._pcm = pcm if pcm is not None else (b"\x00\x00" * 160)
        self.started = False
        self.calls: list[str] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del language
        self.calls.append(text)
        return self._pcm


def _pcm_ms(ms: int) -> bytes:
    return b"\x00\x01" * int(16000 * ms / 1000)


class _MockNllbMt(_MockMt):
    """Stand-in for NllbCt2Mt in e2e (no download)."""

    warming_up = False


class _MockKokoroTts(_MockTts):
    """Stand-in for KokoroOnnxTts in e2e (no download)."""

    warming_up = False


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)


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
    _wait_until(lambda: cb.on_translated_text.called)
    engine.close()

    cb.on_source_text.assert_called()
    src_args = cb.on_source_text.call_args_list[0][0]
    assert src_args[0] == Direction.OUTBOUND
    assert src_args[1] == "hello world"
    assert src_args[2] is False

    cb.on_translated_text.assert_called()
    tr_args = cb.on_translated_text.call_args[0]
    assert tr_args[1] == "你好WORLD"
    assert tr_args[2] is True
    cb.on_audio.assert_called()
    assert cb.on_audio.call_args[0][1] == audio


def test_economy_multi_sentence_splits_mt_and_tts(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("Hello world. How are you?")
    mt = _MockNllbMt("__echo__")
    tts = _MockKokoroTts(b"\x01\x00" * 160)
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_utterance_silence_ms=80,
        economy_utterance_min_ms=50,
        economy_utterance_max_ms=5000,
    )
    engine = EconomyPipelineEngine(
        config=config, callbacks=cb, asr=asr, mt=mt, tts=tts
    )
    assert engine.start_direction(Direction.OUTBOUND, play_voice=True) is True
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    time.sleep(0.12)
    _wait_until(lambda: cb.on_translated_text.call_count >= 2)
    engine.close()

    assert len(mt.calls) == 2
    assert mt.calls[0] == "Hello world."
    assert mt.calls[1] == "How are you?"
    assert len(tts.calls) == 2
    source_calls = [call.args for call in cb.on_source_text.call_args_list]
    translated_calls = [call.args for call in cb.on_translated_text.call_args_list]
    assert source_calls == [
        (Direction.OUTBOUND, "Hello world. How are you?", False),
        (Direction.OUTBOUND, "Hello world.", False),
        (Direction.OUTBOUND, "How are you?", False),
    ]
    assert translated_calls == [
        (Direction.OUTBOUND, "T(Hello world.)", True),
        (Direction.OUTBOUND, "T(How are you?)", True),
    ]
    assert cb.on_audio.call_count == 2


def test_start_direction_false_without_asr(tmp_path):
    cb = _callbacks()
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_asr_backend="dashscope",
    )
    engine = EconomyPipelineEngine(
        config=config,
        callbacks=cb,
        asr=UnconfiguredAsr(),
        mt=UnconfiguredMt(),
        tts=UnconfiguredTts(),
    )
    assert engine.start_direction(Direction.OUTBOUND) is False
    cb.on_error.assert_called()


def test_failed_sentence_is_cleared_without_changing_completed_pair(tmp_path):
    cb = _callbacks()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=_MockAsr("First. Last."),
        mt=_MockMt({"First.": "One.", "Last.": None}),
        tts=_MockTts(),
    )

    from src.engines.pipeline.engine import _Job

    engine._process_job(
        _Job(
            direction=Direction.OUTBOUND,
            pcm=_pcm_ms(100),
            source_lang="en",
            target_lang="zh",
            play_voice=False,
        )
    )
    engine._flush_stable_text(force=True)

    translated_calls = [call.args for call in cb.on_translated_text.call_args_list]
    assert translated_calls == [(Direction.OUTBOUND, "One.", True)]
    source_calls = [call.args for call in cb.on_source_text.call_args_list]
    assert source_calls == [
        (Direction.OUTBOUND, "First. Last.", False),
        (Direction.OUTBOUND, "First.", False),
        (Direction.OUTBOUND, "Last.", False),
        (Direction.OUTBOUND, "", True),
    ]


def test_asr_phrases_are_aggregated_before_translation(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("What I")
    mt = _MockMt("__echo__")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=asr,
        mt=mt,
        tts=_MockTts(),
    )

    from src.engines.pipeline.engine import _Job

    job = _Job(
        direction=Direction.INBOUND,
        pcm=_pcm_ms(100),
        source_lang="en",
        target_lang="zh",
        play_voice=False,
    )
    for phrase in ("What I", "want to tell you", "is a complete story"):
        asr._text = phrase
        engine._process_job(job)

    assert not mt.calls
    partials = [call.args[1] for call in cb.on_source_text.call_args_list]
    assert partials[-1] == "What I want to tell you is a complete story"

    engine._flush_stable_text(force=True)

    assert mt.calls == ["What I want to tell you is a complete story"]
    assert cb.on_translated_text.call_args.args == (
        Direction.INBOUND,
        "T(What I want to tell you is a complete story)",
        True,
    )


def test_overlapping_asr_phrases_are_deduplicated(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )

    text = engine._join_recognized_text(
        "WHAT I WANT TO TELL YOU IS A COMPLETE STORY",
        "A COMPLETE STORY ABOUT LOCAL SPEECH RECOGNITION",
    )

    assert text == (
        "WHAT I WANT TO TELL YOU IS A COMPLETE STORY "
        "ABOUT LOCAL SPEECH RECOGNITION"
    )

    text = engine._join_recognized_text(text, "RECOGNITION SHOULD PRES")
    text = engine._join_recognized_text(text, "PRESERVE FULL SENTENCES")
    assert text.endswith("RECOGNITION SHOULD PRESERVE FULL SENTENCES")


def test_asr_overlap_keeps_new_sentence_punctuation(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )

    text = engine._join_recognized_text("Hello world", "world. How are")
    text = engine._join_recognized_text(text, "How are you?")

    assert text == "Hello world. How are you?"


def test_cjk_asr_phrases_merge_by_character_overlap(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )

    text = engine._join_recognized_text("这是一个", "一个完整句子")
    text = engine._join_recognized_text(text, "完整句子应该一起翻译。")

    assert text == "这是一个完整句子应该一起翻译。"


def test_growing_asr_hypothesis_keeps_latest_punctuation(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )

    assert engine._join_recognized_text("Hello world", "Hello world.") == "Hello world."
    assert engine._join_recognized_text("你好世界", "你好世界。") == "你好世界。"


def test_economy_queue_drops_oldest_when_full(tmp_path):
    cb = _callbacks()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )
    for i in range(engine._queue.maxsize):
        engine._enqueue(Direction.OUTBOUND, bytes([i]))

    engine._enqueue(Direction.OUTBOUND, b"newest")

    queued = list(engine._queue.queue)
    assert len(queued) == engine._queue.maxsize
    assert queued[0].pcm == b"\x01"
    assert queued[-1].pcm == b"newest"


def test_loading_models_reports_each_warming_backend(tmp_path):
    asr = _MockAsr()
    mt = _MockMt()
    tts = _MockTts()
    asr.warming_up = True
    mt.warming_up = True
    tts.warming_up = True
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=asr,
        mt=mt,
        tts=tts,
    )

    assert engine.loading_models == ("本地 ASR", "NLLB 翻译", "Kokoro 语音")


def test_inbound_incomplete_phrase_waits_across_audio_bursts(tmp_path, monkeypatch):
    cb = _callbacks()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_sentence_pause_ms=900,
        ),
        callbacks=cb,
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    job = _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False)
    engine._buffer_recognized_text(job, "This is an incomplete")
    now = 101.5
    engine._flush_stable_text()
    assert not engine._mt.calls
    now = 102.5
    engine._flush_stable_text()
    assert engine._mt.calls == ["This is an incomplete"]


def test_continuous_pcm_does_not_postpone_stable_text_translation(
    tmp_path, monkeypatch
):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_sentence_pause_ms=900,
        ),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    engine._buffer_recognized_text(
        _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False),
        "Recognition is stable without punctuation",
    )

    # Raw capture is still active, but the ASR hypothesis has not changed.
    now = 102.5
    engine._buffers[Direction.INBOUND].mark_capture_active()
    engine._flush_stable_text()

    assert engine._mt.calls == ["Recognition is stable without punctuation"]


def test_continuous_asr_updates_hit_short_max_wait(tmp_path, monkeypatch):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_sentence_pause_ms=900,
            economy_sentence_max_wait_ms=2800,
        ),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    job = _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False)
    engine._buffer_recognized_text(job, "This sentence starts")
    now = 102.0
    engine._buffer_recognized_text(job, "and continues with more context")
    now = 102.9
    engine._flush_stable_text()

    assert engine._mt.calls == ["This sentence starts and continues with more context"]


def test_two_inbound_asr_chunks_translate_without_waiting_for_third(
    tmp_path, monkeypatch
):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_sentence_pause_ms=900,
            economy_sentence_max_wait_ms=2800,
        ),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    job = _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False)
    engine._buffer_recognized_text(job, "This sentence starts")
    now = 102.0
    engine._buffer_recognized_text(job, "and now has enough context")
    engine._flush_stable_text()

    assert engine._mt.calls == ["This sentence starts and now has enough context"]


def test_short_final_asr_pass_does_not_erase_accumulated_text(tmp_path):
    asr = _MockAsr("only the ending fragment")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=asr,
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    full_text = "This is the complete beginning and only the ending fragment"
    engine._buffer_recognized_text(
        _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False),
        full_text,
    )
    engine._flush_stable_text(force=True)

    assert engine._mt.calls == [full_text]


def test_stopping_direction_keeps_pending_text_for_worker_flush(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    engine._buffer_recognized_text(
        _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False),
        "Translate the final sentence",
    )
    engine.stop_direction(Direction.INBOUND)
    engine._flush_stable_text(force=True)

    assert engine._mt.calls == ["Translate the final sentence"]


def test_terminal_punctuation_commits_quickly(tmp_path, monkeypatch):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    engine._buffer_recognized_text(
        _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False),
        "This is complete.",
    )
    now = 100.4
    engine._flush_stable_text()
    assert engine._mt.calls == ["This is complete."]


def test_stopping_last_direction_keeps_models_loaded_until_close(tmp_path):
    asr = _MockAsr()
    mt = _MockMt()
    tts = _MockTts()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=asr,
        mt=mt,
        tts=tts,
    )

    assert engine.start_direction(Direction.INBOUND) is True
    engine.stop_direction(Direction.INBOUND)
    assert asr.started is True
    assert mt.started is True
    assert tts.started is True

    engine.close()
    assert asr.started is False
    assert mt.started is False
    assert tts.started is False
