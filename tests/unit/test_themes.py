"""Theme registry, system resolution, and config migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.gui.styles import (
    SYSTEM_DARK,
    SYSTEM_LIGHT,
    THEMES,
    build_stylesheet,
    resolve_theme,
    theme_choices,
)
from src.models.config import THEME_KEYS, AppConfigModel, normalize_theme_key


def test_every_theme_key_except_system_has_a_theme() -> None:
    assert set(THEMES) == set(THEME_KEYS) - {"system"}
    assert [key for _label, key in theme_choices()] == list(THEME_KEYS)


@pytest.mark.parametrize("key", sorted(THEMES))
def test_stylesheet_uses_theme_tokens_and_existing_assets(key: str) -> None:
    theme = THEMES[key]  # type: ignore[index]
    qss = build_stylesheet(theme)
    assert theme.accent in qss
    assert theme.bg in qss
    for line in qss.splitlines():
        if "url(" in line:
            path = line.split("url(", 1)[1].split(")", 1)[0]
            assert Path(path).is_file(), path


def test_locked_switch_still_shows_its_on_state() -> None:
    qss = build_stylesheet(THEMES[sorted(THEMES)[0]])  # type: ignore[index]
    # Qt applies the later rule, so checked+disabled must come after plain disabled.
    assert qss.index("QCheckBox::indicator:disabled") < qss.index(
        "QCheckBox::indicator:checked:disabled"
    )


def test_system_mode_follows_os_scheme() -> None:
    assert resolve_theme("system", system_dark=True).key == SYSTEM_DARK
    assert resolve_theme("system", system_dark=False).key == SYSTEM_LIGHT
    assert resolve_theme("violet", system_dark=False).key == "violet"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("dark", "nola_dark"), ("light", "nola_light"), ("SAND", "sand"), ("bogus", "system")],
)
def test_legacy_and_unknown_theme_values_are_normalized(stored: str, expected: str) -> None:
    assert normalize_theme_key(stored) == expected
    assert AppConfigModel(theme_mode=stored).theme_mode == expected  # type: ignore[arg-type]


def test_default_config_follows_system_and_theme_overlay() -> None:
    cfg = AppConfigModel()
    assert cfg.theme_mode == "system"
    assert cfg.subtitle_follow_theme is True
    assert cfg.status_panel_visible is True


def test_legacy_custom_subtitle_colors_disable_follow_theme() -> None:
    custom = AppConfigModel.model_validate(
        {"subtitle_text_color": "#F6F5F4", "subtitle_bg_color": "#99C1F1"}
    )
    assert custom.subtitle_follow_theme is False

    defaults = AppConfigModel.model_validate(
        {"subtitle_text_color": "#ffffff", "subtitle_bg_color": "#141418"}
    )
    assert defaults.subtitle_follow_theme is True

    explicit = AppConfigModel.model_validate(
        {"subtitle_text_color": "#F6F5F4", "subtitle_follow_theme": True}
    )
    assert explicit.subtitle_follow_theme is True
