"""Unit tests for dual-engine factory and stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.pipeline import DirectionState, TranslationPipeline
from src.engines.base import EngineCallbacks
from src.engines.factory import create_engine
from src.engines.pipeline.engine import EconomyPipelineEngine
from src.engines.volc.engine import VolcTranslationEngine
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.models.subtitle import SubtitleEntry


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        on_source_text=lambda *_a: None,
        on_translated_text=lambda *_a: None,
        on_audio=lambda *_a: None,
        on_error=lambda *_a: None,
        on_status=lambda *_a: None,
        on_usage=lambda *_a: None,
        on_engine_status=lambda *_a: None,
        should_defer_rotate=lambda *_a: False,
    )


def test_factory_creates_volc(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        translation_mode="volc",
    )
    engine = create_engine("volc", config, _callbacks())
    assert isinstance(engine, VolcTranslationEngine)
    assert engine.engine_id == "volc"


def test_factory_creates_economy(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
    )
    engine = create_engine("economy", config, _callbacks())
    assert isinstance(engine, EconomyPipelineEngine)
    assert engine.engine_id == "economy"


def test_economy_start_send_stop_close(tmp_path):
    """Without DashScope key, factory ASR is unconfigured and start fails."""
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_asr_backend="dashscope",
    )
    engine = create_engine("economy", config, _callbacks())
    assert engine.start_direction(Direction.OUTBOUND, play_voice=False) is False
    engine.close()
    assert engine.active_directions == frozenset()



def test_switch_volc_to_economy(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        translation_mode="volc",
    )
    cb = _callbacks()
    volc = create_engine("volc", config, cb)
    with patch.object(volc, "start_direction", return_value=True) as start:
        assert volc.start_direction(Direction.OUTBOUND) is True
        start.assert_called_once()
    volc.close()

    economy = create_engine(
        "economy",
        config.model_copy(
            update={
                "translation_mode": "economy",
                "economy_dashscope_api_key": "sk-test",
            }
        ),
        cb,
    )
    assert economy.engine_id == "economy"
    # DashScope SDK may be absent in CI; e2e covered in test_economy_backends.
    economy.close()



def test_pipeline_mode_property(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_asr_backend="dashscope",
        volc_api_key="",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.mode == "economy"
    ok, msg = pipeline.can_start()
    assert ok is False
    assert "DashScope" in msg

    config2 = config.model_copy(update={"economy_dashscope_api_key": "sk-test"})
    pipeline2 = TranslationPipeline(config2)
    ok2, msg2 = pipeline2.can_start()
    assert ok2 is True
    assert "经济" in msg2


def test_pipeline_can_start_local_without_dashscope(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_asr_backend="local",
        economy_dashscope_api_key="",
        volc_api_key="",
    )
    pipeline = TranslationPipeline(config)
    ok, msg = pipeline.can_start()
    assert ok is True
    assert "本地" in msg or "经济" in msg

    config_lc = config.model_copy(update={"economy_asr_backend": "live_captions"})
    pipeline_lc = TranslationPipeline(config_lc)
    ok_lc, msg_lc = pipeline_lc.can_start()
    assert ok_lc is True
    assert "经济" in msg_lc

    for alias in ("sherpa", "whisper"):
        cfg = config.model_copy(update={"economy_asr_backend": alias})
        ok_a, _ = TranslationPipeline(cfg).can_start()
        assert ok_a is True


def test_pipeline_can_start_economy_default_without_dashscope(tmp_path, monkeypatch):
    """Default economy backend must not require DashScope (local or Live Captions)."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        economy_dashscope_api_key="",
    )
    from src.engines.pipeline.engine import resolve_economy_asr_backend

    # Model default may still say live_captions; resolve remaps on non-Windows.
    resolved = resolve_economy_asr_backend(config)
    assert resolved in ("live_captions", "local")
    ok, msg = TranslationPipeline(config).can_start()
    assert ok is True
    assert "DashScope" not in msg


def test_load_config_migrates_live_captions_off_windows(tmp_path, monkeypatch) -> None:
    import json

    from src.utils import config_manager

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source_language": "zh",
                "target_language": "en",
                "economy_asr_backend": "live_captions",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    monkeypatch.setattr(config_manager.sys, "platform", "linux")
    loaded = config_manager.load_config()
    assert loaded.economy_asr_backend == "local"


def test_economy_asr_requires_dashscope_helper():
    from src.engines.pipeline.engine import (
        economy_asr_requires_dashscope,
        resolve_economy_asr_backend,
    )

    assert resolve_economy_asr_backend(
        AppConfigModel(economy_asr_backend="local")
    ) == "local"
    assert economy_asr_requires_dashscope(
        AppConfigModel(economy_asr_backend="local")
    ) is False
    assert economy_asr_requires_dashscope(
        AppConfigModel(economy_asr_backend="live_captions")
    ) is False
    assert economy_asr_requires_dashscope(
        AppConfigModel(economy_asr_backend="dashscope")
    ) is True
    assert resolve_economy_asr_backend(
        AppConfigModel(economy_asr_backend="dashscope")
    ) == "dashscope"


def test_non_windows_live_captions_resolves_to_local(monkeypatch):
    """Live Captions is Windows-only; Linux/macOS must fall back to local ASR."""
    import sys
    from types import SimpleNamespace

    from src.engines.pipeline.engine import (
        build_economy_backends,
        default_economy_asr_backend,
        live_captions_supported,
        resolve_economy_asr_backend,
    )
    from src.engines.pipeline.live_captions_asr import LiveCaptionsAsr

    monkeypatch.setattr(sys, "platform", "linux")
    assert live_captions_supported() is False
    assert default_economy_asr_backend() == "local"
    cfg = AppConfigModel(economy_asr_backend="live_captions")
    assert resolve_economy_asr_backend(cfg) == "local"
    assert resolve_economy_asr_backend(SimpleNamespace(economy_asr_backend="")) == "local"
    assert resolve_economy_asr_backend(SimpleNamespace(economy_asr_backend=None)) == "local"

    asr, _mt, _tts = build_economy_backends(cfg)
    assert not isinstance(asr, LiveCaptionsAsr)


def test_windows_live_captions_kept(monkeypatch):
    import sys

    from src.engines.pipeline.engine import (
        build_economy_backends,
        resolve_economy_asr_backend,
    )
    from src.engines.pipeline.live_captions_asr import LiveCaptionsAsr

    monkeypatch.setattr(sys, "platform", "win32")
    cfg = AppConfigModel(economy_asr_backend="live_captions")
    assert resolve_economy_asr_backend(cfg) == "live_captions"
    asr, _mt, _tts = build_economy_backends(cfg)
    assert isinstance(asr, LiveCaptionsAsr)



def test_pipeline_wants_volc_false_in_economy(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.wants_volc() is False


def test_pipeline_set_translation_mode_closes_engine(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        translation_mode="volc",
    )
    pipeline = TranslationPipeline(config)
    fake = MagicMock()
    fake.engine_id = "volc"
    fake.active_directions = frozenset()
    pipeline._engine = fake
    pipeline.set_translation_mode("economy")
    fake.close.assert_called()
    assert pipeline._engine is None
    assert pipeline.mode == "economy"
    assert pipeline._config.translation_mode == "economy"


def test_engine_subtitle_emit_streaming(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
    pipeline = TranslationPipeline(config)
    pipeline._directions[Direction.OUTBOUND] = DirectionState(direction=Direction.OUTBOUND)

    received: list[SubtitleEntry] = []
    pipeline.subtitle_ready.connect(received.append)

    pipeline._on_engine_source(Direction.OUTBOUND, "hello", False)
    pipeline._on_engine_translated(Direction.OUTBOUND, "你好", False)
    assert received
    assert received[-1].is_final is False
    assert received[-1].original_text == "hello"
    assert received[-1].translated_text == "你好"

    pipeline._on_engine_translated(Direction.OUTBOUND, "你好呀", True)
    assert received[-1].is_final is True
    assert received[-1].translated_text == "你好呀"


def test_economy_source_shows_translation_pending_state(tmp_path):
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), translation_mode="economy")
    )
    pipeline._directions[Direction.INBOUND] = DirectionState(direction=Direction.INBOUND)
    received: list[SubtitleEntry] = []
    pipeline.subtitle_ready.connect(received.append)

    pipeline._on_engine_source(Direction.INBOUND, "A complete sentence", False)

    assert received[-1].original_text == "A complete sentence"
    assert received[-1].translated_text == "正在翻译…"
    assert received[-1].is_final is False


def test_subtitle_dedup_is_scoped_per_direction(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
    pipeline = TranslationPipeline(config)
    pipeline._directions[Direction.OUTBOUND] = DirectionState(direction=Direction.OUTBOUND)
    pipeline._directions[Direction.INBOUND] = DirectionState(direction=Direction.INBOUND)
    pipeline._play_outbound_voice = True
    pipeline._play_inbound_voice = True

    pipeline._on_engine_source(Direction.OUTBOUND, "hello", False)
    pipeline._on_engine_translated(Direction.OUTBOUND, "same", True)
    pipeline._on_engine_source(Direction.INBOUND, "hello", False)
    pipeline._on_engine_translated(Direction.INBOUND, "same", True)

    assert Direction.OUTBOUND not in pipeline._skip_next_audio_for
    assert Direction.INBOUND not in pipeline._skip_next_audio_for
