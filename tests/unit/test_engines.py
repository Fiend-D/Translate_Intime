"""Unit tests for dual-engine factory and stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.engines.base import EngineCallbacks
from src.engines.factory import create_engine
from src.engines.pipeline.engine import EconomyPipelineEngine
from src.engines.volc.engine import VolcTranslationEngine
from src.core.pipeline import DirectionState, TranslationPipeline
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
