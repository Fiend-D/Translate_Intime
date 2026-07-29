"""Volc / dual-engine pipeline unit tests."""

from src.core.pipeline import TranslationPipeline
from src.models.config import AppConfigModel


def test_requires_volc_credentials(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="",
        translation_mode="volc",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.wants_volc() is False
    ok, _ = pipeline.can_start()
    assert ok is False


def test_has_credentials_with_api_key(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        translation_mode="volc",
    )
    pipeline = TranslationPipeline(config)
    assert pipeline.wants_volc() is True
    assert pipeline.mode == "volc"
    ok, _ = pipeline.can_start()
    assert ok is True
