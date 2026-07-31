"""Unit tests for NLLB language mapping and economy backend factories (no downloads)."""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.engines.base import EngineCallbacks
from src.engines.pipeline.engine import EconomyPipelineEngine, build_economy_backends
from src.engines.pipeline.mt import AutoMt
from src.engines.pipeline.nllb_mt import (
    DEFAULT_NLLB_MODEL,
    NllbCt2Mt,
    model_slug,
    nllb_lang_code,
)
from src.engines.pipeline.tts import AutoTts
from src.models.config import AppConfigModel
from src.models.enums import Direction


def test_nllb_lang_code_mapping() -> None:
    assert nllb_lang_code("zh") == "zho_Hans"
    assert nllb_lang_code("en") == "eng_Latn"
    assert nllb_lang_code("ja") == "jpn_Jpan"
    assert nllb_lang_code("ko") == "kor_Hang"
    assert nllb_lang_code("zh-CN") == "zho_Hans"
    assert nllb_lang_code("EN") == "eng_Latn"
    assert nllb_lang_code("fr") is None
    assert nllb_lang_code("") is None


def test_nllb_model_slug() -> None:
    assert model_slug("JustFrederik/nllb-200-distilled-600M-ct2-int8").startswith("nllb-200")
    assert "/" not in model_slug(DEFAULT_NLLB_MODEL)


def test_model_files_ready_false_with_only_tokenizer(tmp_path) -> None:
    cache = tmp_path / "nllb"
    cache.mkdir()
    (cache / "config.json").write_text("{}", encoding="utf-8")
    (cache / "README.md").write_text("nllb", encoding="utf-8")
    mt = NllbCt2Mt(
        model_id=DEFAULT_NLLB_MODEL,
        cache_dir=cache,
        auto_download=False,
    )
    assert mt._model_files_ready() is False


def test_model_files_ready_true_with_model_bin(tmp_path) -> None:
    cache = tmp_path / "nllb"
    cache.mkdir()
    (cache / "config.json").write_text("{}", encoding="utf-8")
    (cache / "model.bin").write_bytes(b"\0" * (1_000_000 + 1))
    mt = NllbCt2Mt(
        model_id=DEFAULT_NLLB_MODEL,
        cache_dir=cache,
        auto_download=False,
    )
    assert mt._model_files_ready() is True


def test_nllb_translate_none_while_not_ready(tmp_path) -> None:
    mt = NllbCt2Mt(
        model_id=DEFAULT_NLLB_MODEL,
        cache_dir=tmp_path / "nllb",
        auto_download=False,
    )
    mt.start()
    # Do not wait for background thread — translate must be safe and return None.
    assert mt.translate("hello", source_lang="en", target_lang="zh") is None
    assert mt.translate("bonjour", source_lang="fr", target_lang="zh") is None
    mt.stop()


def test_nllb_download_restores_hf_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://user-endpoint.example")
    fake_hub = SimpleNamespace(
        snapshot_download=MagicMock(side_effect=RuntimeError("download failed"))
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    mt = NllbCt2Mt(
        model_id=DEFAULT_NLLB_MODEL,
        cache_dir=tmp_path / "nllb",
        auto_download=True,
    )

    assert mt._download_model() is False
    assert os.environ["HF_ENDPOINT"] == "https://user-endpoint.example"


def test_config_economy_defaults() -> None:
    cfg = AppConfigModel(translation_mode="economy")
    assert cfg.economy_mt_backend == "nllb"
    assert cfg.economy_tts_backend == "kokoro"
    assert "nllb-200" in cfg.economy_nllb_model
    assert cfg.economy_kokoro_voice_en == "af_bella"
    assert cfg.economy_kokoro_voice_zh == "zf_xiaoxiao"
    assert cfg.economy_kokoro_speed == 0.92
    assert cfg.economy_sentence_min_chars == 4
    assert cfg.economy_sentence_pause_ms == 900
    assert cfg.economy_sentence_max_wait_ms == 2800
    assert cfg.economy_utterance_soft_split_ms == 6000
    assert cfg.economy_utterance_soft_split_quiet_ms == 280


def test_build_economy_backends_prefers_nllb_kokoro(tmp_path) -> None:
    cfg = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_asr_backend="dashscope",
        economy_dashscope_api_key="sk-test",
    )
    with (
        patch("src.engines.pipeline.engine.AutoMt") as mock_mt,
        patch("src.engines.pipeline.engine.AutoTts") as mock_tts,
        patch("src.engines.pipeline.engine.DashScopeAsr") as mock_asr,
    ):
        mock_mt.return_value = MagicMock(configured=False, warming_up=True)
        mock_tts.return_value = MagicMock(configured=False, warming_up=True)
        mock_asr.return_value = MagicMock(configured=True)
        asr, mt, tts = build_economy_backends(cfg)
    mock_mt.assert_called()
    prefer = mock_mt.call_args.kwargs.get("prefer") or mock_mt.call_args[1].get("prefer")
    assert prefer == "nllb"
    mock_tts.assert_called()
    tts_prefer = mock_tts.call_args.kwargs.get("prefer")
    assert tts_prefer == "kokoro"
    assert asr is mock_asr.return_value
    assert mt is mock_mt.return_value
    assert tts is mock_tts.return_value


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

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        del pcm, language
        return self._text


class _WarmingMt:
    configured = False
    warming_up = True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        del text, source_lang, target_lang
        return None


class _MockTts:
    configured = True
    warming_up = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del text, language
        return b"\x00\x00" * 160


def _pcm_ms(ms: int) -> bytes:
    return b"\x00\x01" * int(16000 * ms / 1000)


def test_economy_mt_warming_emits_source_and_status(tmp_path) -> None:
    cb = _callbacks()
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_utterance_silence_ms=80,
        economy_utterance_min_ms=50,
        economy_utterance_max_ms=5000,
    )
    engine = EconomyPipelineEngine(
        config=config,
        callbacks=cb,
        asr=_MockAsr("hello world"),
        mt=_WarmingMt(),
        tts=_MockTts(),
    )
    assert engine.start_direction(Direction.OUTBOUND, play_voice=False) is True
    engine.send_pcm(Direction.OUTBOUND, _pcm_ms(100))
    time.sleep(0.12)
    deadline = time.time() + 2.0
    while time.time() < deadline and not cb.on_source_text.called:
        time.sleep(0.05)
    engine.close()

    cb.on_source_text.assert_called()
    cb.on_translated_text.assert_not_called()
    status_msgs = [c.args[0] for c in cb.on_status.call_args_list if c.args]
    assert any("本地翻译模型加载中" in str(m) or "加载中" in str(m) for m in status_msgs)


def test_auto_mt_skips_fallback_while_nllb_warming() -> None:
    nllb = MagicMock()
    nllb.configured = False
    nllb.warming_up = True
    nllb.translate.return_value = None
    argos = MagicMock()
    argos.translate.return_value = "from-argos"
    mm = MagicMock()
    mm.translate.return_value = "from-mm"
    auto = AutoMt(prefer="auto", nllb=nllb, argos=argos, mymemory=mm)
    auto.start()
    assert auto.translate("hi", source_lang="en", target_lang="zh") is None
    argos.translate.assert_not_called()
    mm.translate.assert_not_called()


def test_auto_tts_prefers_kokoro() -> None:
    kokoro = MagicMock()
    kokoro.configured = True
    kokoro.warming_up = False
    kokoro.synthesize.return_value = b"\x01\x00"
    edge = MagicMock()
    edge.synthesize.return_value = b"\x02\x00"
    auto = AutoTts(prefer="auto", kokoro=kokoro, edge=edge)
    auto.start()
    assert auto.synthesize("hi", language="en") == b"\x01\x00"
    edge.synthesize.assert_not_called()
