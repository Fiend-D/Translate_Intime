"""Economy engine end-to-end with injected mock backends (no network)."""

from __future__ import annotations

import threading
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
    warming_up = False

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
    warming_up = False

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


class _BlockingTts(_MockTts):
    warming_up = False

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        self.entered.set()
        self.release.wait(timeout=2.0)
        return super().synthesize(text, language=language)


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
    engine = EconomyPipelineEngine(config=config, callbacks=cb, asr=asr, mt=mt, tts=tts)
    assert engine.start_direction(Direction.OUTBOUND, play_voice=True) is True
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    time.sleep(0.12)
    _wait_until(lambda: cb.on_audio.called)
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


def test_economy_worker_recognizes_two_consecutive_utterances(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("First sentence.")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_utterance_silence_ms=50,
            economy_utterance_min_ms=50,
        ),
        callbacks=cb,
        asr=asr,
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    assert engine.start_direction(Direction.OUTBOUND) is True

    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    _wait_until(lambda: cb.on_translated_text.call_count >= 1)
    asr._text = "Second sentence."
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    _wait_until(lambda: cb.on_translated_text.call_count >= 2)
    engine.close()

    translated = [call.args[1] for call in cb.on_translated_text.call_args_list]
    assert translated == ["T(First sentence.)", "T(Second sentence.)"]


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
    engine = EconomyPipelineEngine(config=config, callbacks=cb, asr=asr, mt=mt, tts=tts)
    assert engine.start_direction(Direction.OUTBOUND, play_voice=True) is True
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    time.sleep(0.12)
    _wait_until(lambda: cb.on_audio.call_count >= 2)
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


def test_slow_tts_does_not_block_following_asr(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("First sentence.")
    tts = _BlockingTts()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=asr,
        mt=_MockMt("__echo__"),
        tts=tts,
    )
    from src.engines.pipeline.engine import _Job, _PendingText

    engine._translate_stable_text(
        Direction.OUTBOUND,
        _PendingText(
            text="First sentence.",
            source_lang="en",
            target_lang="zh",
            play_voice=True,
            updated_at=time.monotonic(),
            created_at=time.monotonic(),
            pcm=_pcm_ms(100),
            chunk_count=1,
        ),
    )
    assert tts.entered.wait(timeout=1.0)

    asr._text = "Second sentence."
    engine._process_job(_Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False))

    cb.on_source_text.assert_called_with(Direction.OUTBOUND, "Second sentence.", False)
    tts.release.set()
    engine.close()


def test_sentences_are_requeued_when_mt_starts_loading_mid_batch(tmp_path):
    from src.engines.pipeline.engine import _PendingText

    class _SwitchingMt:
        configured = True
        warming_up = False

        def start(self) -> None: ...

        def stop(self) -> None: ...

        def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
            self.warming_up = True
            return None

    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(""),
        mt=_SwitchingMt(),
        tts=_MockTts(),
    )
    now = time.monotonic()
    engine._translate_stable_text(
        Direction.OUTBOUND,
        _PendingText(
            text="Hello world. How are you?",
            source_lang="en",
            target_lang="zh",
            play_voice=False,
            updated_at=now,
            created_at=now,
            pcm=b"",
            chunk_count=1,
        ),
    )

    assert engine._pending_text[Direction.OUTBOUND].text == "Hello world. How are you?"
    engine.close()


def test_stopped_tts_worker_does_not_emit_late_audio(tmp_path):
    cb = _callbacks()
    tts = _BlockingTts()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=tts,
    )
    engine._enqueue_tts(Direction.OUTBOUND, "late", "en")
    assert tts.entered.wait(timeout=1.0)

    engine._tts_stop_event.set()
    tts.release.set()
    assert engine._tts_worker is not None
    engine._tts_worker.join(timeout=1.0)

    cb.on_audio.assert_not_called()
    engine.close()


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

    assert text == ("WHAT I WANT TO TELL YOU IS A COMPLETE STORY ABOUT LOCAL SPEECH RECOGNITION")

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


def test_asr_warmup_retains_audio_and_retries_when_ready(tmp_path):
    cb = _callbacks()
    asr = _MockAsr("retained speech")
    asr.configured = False
    asr.warming_up = True
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=asr,
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    job = _Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False)
    engine._process_job(job)

    assert not asr.calls
    assert engine._deferred_asr_jobs[Direction.OUTBOUND].pcm == job.pcm

    asr.configured = True
    asr.warming_up = False
    engine._retry_deferred_asr_jobs()

    assert len(asr.calls) == 1
    assert Direction.OUTBOUND not in engine._deferred_asr_jobs
    cb.on_source_text.assert_called_with(Direction.OUTBOUND, "retained speech", False)


def test_mt_warmup_retains_pending_text_until_ready(tmp_path):
    cb = _callbacks()
    mt = _MockMt("__echo__")
    mt.configured = False
    mt.warming_up = True
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=_MockAsr("recognized sentence"),
        mt=mt,
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    job = _Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False)
    engine._buffer_recognized_text(job, "recognized sentence")
    engine._flush_stable_text(force=True)

    assert Direction.OUTBOUND in engine._pending_text
    assert not mt.calls

    mt.configured = True
    mt.warming_up = False
    engine._flush_stable_text(force=True)

    assert Direction.OUTBOUND not in engine._pending_text
    assert mt.calls == ["recognized sentence"]


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

    assert engine.loading_models == ("本地 ASR", "NLLB 翻译")

    engine._ensure_tts_started()
    assert engine.loading_models == ("本地 ASR", "NLLB 翻译", "Kokoro-82M")


def test_voice_model_only_loads_for_channels_that_speak(tmp_path):
    tts = _MockTts()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=tts,
    )

    assert engine.start_direction(Direction.INBOUND, play_voice=False) is True
    assert tts.started is False

    assert engine.start_direction(Direction.OUTBOUND, play_voice=True) is True
    assert tts.started is True
    engine.close()


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
    # The next phrase is still being captured: not a sentence end yet.
    engine._buffers[Direction.INBOUND].push(_pcm_ms(500))
    now = 101.5
    engine._flush_stable_text()
    assert not engine._mt.calls

    engine._buffers[Direction.INBOUND].flush()
    engine._buffer_recognized_text(job, "sentence that goes on")
    now = 102.0
    engine._flush_stable_text()
    assert not engine._mt.calls
    now = 103.0
    engine._flush_stable_text()
    assert engine._mt.calls == ["This is an incomplete sentence that goes on"]


def test_continuous_pcm_does_not_postpone_stable_text_translation(tmp_path, monkeypatch):
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


def test_recent_speech_pcm_postpones_unpunctuated_commit(tmp_path, monkeypatch):
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
    engine._buffer_recognized_text(job, "and keeps going")
    # Speech PCM arrived just now (between VAD-cut chunks): the speaker has not paused.
    engine._last_pcm_at[Direction.INBOUND] = 102.5
    now = 102.8
    engine._flush_stable_text()
    assert not engine._mt.calls

    now = 103.5
    engine._flush_stable_text()
    assert engine._mt.calls == ["This sentence starts and keeps going"]


def test_punctuated_sentence_commits_without_long_pause(tmp_path, monkeypatch):
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
        _Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False), "It is done."
    )
    now = 100.4
    engine._flush_stable_text()
    assert engine._mt.calls == ["It is done."]


def test_old_default_sentence_max_wait_is_migrated() -> None:
    assert AppConfigModel(economy_sentence_max_wait_ms=2800).economy_sentence_max_wait_ms == 7000
    assert AppConfigModel(economy_sentence_max_wait_ms=3600).economy_sentence_max_wait_ms == 3600


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


def test_stopping_direction_enqueues_unflushed_audio_tail(tmp_path):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(
            log_dir=str(tmp_path),
            translation_mode="economy",
            economy_utterance_min_ms=400,
        ),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )
    engine._active.add(Direction.OUTBOUND)
    engine._play_voice[Direction.OUTBOUND] = False
    pcm = _pcm_ms(100)

    engine.send_pcm(Direction.OUTBOUND, pcm)
    assert engine._queue.empty()
    engine.stop_direction(Direction.OUTBOUND)

    queued = engine._queue.get_nowait()
    assert queued is not None
    assert queued.pcm == pcm


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


def test_committing_sentence_clears_asr_overlap_before_next_sentence(tmp_path, monkeypatch):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr("First sentence."),
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    now = 100.0
    monkeypatch.setattr("src.engines.pipeline.engine.time.monotonic", lambda: now)
    first_pcm = _pcm_ms(100)
    job = _Job(Direction.OUTBOUND, first_pcm, "en", "zh", False)
    engine._process_job(job)
    assert engine._asr_audio_tail[Direction.OUTBOUND] == first_pcm

    now = 100.4
    engine._flush_stable_text()

    assert engine._asr_audio_tail[Direction.OUTBOUND] == b""


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

    assert engine.start_direction(Direction.INBOUND, play_voice=True) is True
    engine.stop_direction(Direction.INBOUND)
    assert asr.started is True
    assert mt.started is True
    assert tts.started is True

    engine.close()
    assert asr.started is False
    assert mt.started is False
    assert tts.started is False


def test_single_chunk_sentence_skips_second_asr_pass(tmp_path):
    asr = _MockAsr("Hello there.")
    mt = _MockMt("__echo__")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=asr,
        mt=mt,
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    engine._process_job(_Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False))
    engine._flush_stable_text(force=True)

    assert len(asr.calls) == 1
    assert mt.calls == ["Hello there."]


def test_multi_chunk_sentence_runs_final_asr_pass(tmp_path):
    asr = _MockAsr("Hello there")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=asr,
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    from src.engines.pipeline.engine import _Job

    engine._process_job(_Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False))
    asr._text = "my friend."
    engine._process_job(_Job(Direction.OUTBOUND, _pcm_ms(100), "en", "zh", False))
    asr._text = "Hello there my friend."
    engine._flush_stable_text(force=True)

    assert len(asr.calls) == 3
    assert engine._mt.calls == ["Hello there my friend."]


def test_cancel_pending_audio_drops_in_flight_and_queued_tts(tmp_path):
    cb = _callbacks()
    tts = _BlockingTts()
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=cb,
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=tts,
    )
    engine._enqueue_tts(Direction.OUTBOUND, "in flight", "en")
    assert tts.entered.wait(timeout=1.0)
    engine._enqueue_tts(Direction.OUTBOUND, "queued", "en")

    engine.cancel_pending_audio(Direction.OUTBOUND)
    tts.release.set()
    _wait_until(lambda: engine._tts_queue.unfinished_tasks == 0)

    cb.on_audio.assert_not_called()
    assert tts.calls == ["in flight"]

    engine._enqueue_tts(Direction.OUTBOUND, "after interrupt", "en")
    _wait_until(lambda: cb.on_audio.called)
    engine.close()

    assert tts.calls == ["in flight", "after interrupt"]


def test_trim_repeated_head_keeps_new_words() -> None:
    from src.engines.pipeline.engine import _trim_repeated_head

    assert _trim_repeated_head("我们先去推中路", "推中路然后打肉山") == "然后打肉山"
    assert _trim_repeated_head("push mid now.", "Mid now, then Roshan") == "then Roshan"
    assert _trim_repeated_head("完全不同", "另一句话") == "另一句话"
    assert _trim_repeated_head("abcd", "abcd") == "abcd"


def test_committed_duplicate_is_skipped_within_window(monkeypatch) -> None:
    from src.engines.pipeline import engine as engine_mod

    clock = [100.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: clock[0])
    eng = engine_mod.EconomyPipelineEngine.__new__(engine_mod.EconomyPipelineEngine)
    eng._last_committed = {}
    d = Direction.INBOUND
    assert eng._dedupe_committed(d, "Enemy missing mid!") == "Enemy missing mid!"
    assert eng._dedupe_committed(d, "enemy missing mid") is None
    clock[0] += 10.0
    assert eng._dedupe_committed(d, "Enemy missing mid!") == "Enemy missing mid!"


def test_each_channel_uses_its_own_asr(tmp_path):
    from src.engines.pipeline.engine import _Job

    mic_asr = _MockAsr("我说的话")
    game_asr = _MockAsr("game line")
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr={Direction.OUTBOUND: mic_asr, Direction.INBOUND: game_asr},
        mt=_MockMt("__echo__"),
        tts=_MockTts(),
    )
    assert engine.start_direction(Direction.INBOUND)
    assert game_asr.started and not mic_asr.started

    engine._process_job(_Job(Direction.OUTBOUND, _pcm_ms(100), "zh", "en", False))
    engine._process_job(_Job(Direction.INBOUND, _pcm_ms(100), "en", "zh", False))

    assert [lang for _pcm, lang in mic_asr.calls] == ["zh"]
    assert [lang for _pcm, lang in game_asr.calls] == ["en"]
    engine.close()
    assert not game_asr.started


def test_build_channel_asr_shares_instances_by_model(tmp_path, monkeypatch):
    from src.engines.pipeline import engine as engine_mod

    built: list[str] = []

    def fake_local(_config, _device, model):
        built.append(model)
        return _MockAsr(model)

    monkeypatch.setattr(engine_mod, "_build_local_asr", fake_local)
    base = {"log_dir": str(tmp_path), "economy_asr_backend": "local"}

    split = engine_mod.build_channel_asr(
        AppConfigModel(**base, economy_asr_mic_model="mic-model", economy_asr_game_model="auto")
    )
    assert split[Direction.OUTBOUND] is not split[Direction.INBOUND]
    assert built == ["mic-model", "auto"]

    built.clear()
    same = engine_mod.build_channel_asr(
        AppConfigModel(**base, economy_asr_mic_model="auto", economy_asr_game_model="auto")
    )
    assert same[Direction.OUTBOUND] is same[Direction.INBOUND]
    assert built == ["auto"]

    cloud = engine_mod.build_channel_asr(
        AppConfigModel(log_dir=str(tmp_path), economy_asr_backend="qwen_flash")
    )
    assert cloud[Direction.OUTBOUND] is cloud[Direction.INBOUND]


def test_worker_survives_housekeeping_failure(tmp_path, monkeypatch):
    engine = EconomyPipelineEngine(
        config=AppConfigModel(log_dir=str(tmp_path), translation_mode="economy"),
        callbacks=_callbacks(),
        asr=_MockAsr(),
        mt=_MockMt(),
        tts=_MockTts(),
    )
    calls = 0

    def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transcript locked")

    monkeypatch.setattr(engine, "_flush_stable_text", flaky)
    assert engine.start_direction(Direction.INBOUND, play_voice=False) is True

    deadline = time.monotonic() + 3
    while calls < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    engine.close()

    assert calls >= 3
