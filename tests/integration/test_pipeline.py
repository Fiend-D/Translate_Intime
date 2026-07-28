"""Volc-only pipeline smoke tests (no live network)."""

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
    )
    return TranslationPipeline(config)


def test_start_without_credentials_raises(pipeline):
    with pytest.raises(EngineLoadError, match="火山"):
        pipeline.start_channel(Direction.OUTBOUND, play_voice=False)


def test_mode_is_always_volc(pipeline):
    assert pipeline.mode == "volc"
