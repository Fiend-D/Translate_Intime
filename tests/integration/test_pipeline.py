"""Pipeline smoke tests (no live network)."""

import pytest

from src.core.exceptions import EngineLoadError
from src.core.pipeline import TranslationPipeline
from src.models.config import AppConfigModel
from src.models.enums import Direction


@pytest.fixture
def pipeline(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="",
        translation_mode="volc",
    )
    return TranslationPipeline(config)


def test_start_without_credentials_raises(pipeline):
    with pytest.raises(EngineLoadError, match="火山"):
        pipeline.start_channel(Direction.OUTBOUND, play_voice=False)


def test_mode_follows_config(pipeline):
    assert pipeline.mode == "volc"


def test_economy_can_start_without_volc_key(tmp_path):
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="",
        translation_mode="economy",
    )
    p = TranslationPipeline(config)
    ok, msg = p.can_start()
    assert ok is True
    assert "经济" in msg
