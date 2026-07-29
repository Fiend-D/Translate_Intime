"""Unit tests for configuration validation."""

import pytest

from src.models.config import AppConfigModel


def test_default_config_is_valid() -> None:
    config = AppConfigModel()
    assert config.source_language == "zh"
    assert config.target_language == "en"
    assert config.use_volc is True
    assert config.translation_mode == "volc"


def test_translation_mode_economy_sets_use_volc_false() -> None:
    config = AppConfigModel(translation_mode="economy")
    assert config.translation_mode == "economy"
    assert config.use_volc is False


def test_languages_must_differ() -> None:
    with pytest.raises(ValueError, match="must be different"):
        AppConfigModel(source_language="zh", target_language="zh")


def test_invalid_font_size() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_font_size=5)


def test_invalid_opacity() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_opacity=1.5)


def test_window_positions_validation() -> None:
    config = AppConfigModel(subtitle_window_positions={"outbound": (10, 20, 300, 100)})
    assert "outbound" in config.subtitle_window_positions


def test_invalid_window_position_key() -> None:
    with pytest.raises(ValueError, match="Invalid window position keys"):
        AppConfigModel(subtitle_window_positions={"unknown": (0, 0, 100, 100)})
