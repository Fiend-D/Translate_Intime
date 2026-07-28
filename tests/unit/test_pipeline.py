"""Volc-only pipeline unit tests."""

from src.core.pipeline import DirectionState, TranslationPipeline
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.models.subtitle import SubtitleEntry


def test_requires_volc_credentials(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.wants_volc() is False


def test_has_credentials_with_api_key(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.wants_volc() is True
    assert pipeline.mode == "volc"


def test_volc_subtitle_emit_streaming(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
    pipeline = TranslationPipeline(config)
    pipeline._directions[Direction.OUTBOUND] = DirectionState(direction=Direction.OUTBOUND)

    received: list[SubtitleEntry] = []
    pipeline.subtitle_ready.connect(received.append)

    pipeline._on_volc_source(Direction.OUTBOUND, "hello", False)
    pipeline._on_volc_translated(Direction.OUTBOUND, "你好", False)
    assert received
    assert received[-1].is_final is False
    assert received[-1].original_text == "hello"
    assert received[-1].translated_text == "你好"

    pipeline._on_volc_translated(Direction.OUTBOUND, "你好呀", True)
    assert received[-1].is_final is True
    assert received[-1].translated_text == "你好呀"
