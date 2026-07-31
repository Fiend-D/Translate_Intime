"""Regression tests for shortcuts that conflict with terminal control keys."""

from src.models.config import HotkeyConfig
from src.utils.config_manager import _migrate_hotkey_defaults
from src.utils.hotkeys import DEFAULT_HOTKEYS, normalize_combo


def test_dota_coach_default_does_not_emit_console_interrupt() -> None:
    expected = "<ctrl>+<alt>+k"

    assert HotkeyConfig().dota_coach_ask == expected
    assert DEFAULT_HOTKEYS["dota_coach_ask"] == expected
    assert normalize_combo(expected) != "<ctrl>+<alt>+c"


def test_old_dota_coach_default_is_migrated() -> None:
    data = {"hotkeys": {"dota_coach_ask": "<ctrl>+<alt>+c"}}

    _migrate_hotkey_defaults(data)

    assert data["hotkeys"]["dota_coach_ask"] == "<ctrl>+<alt>+k"


def test_custom_dota_coach_binding_is_preserved() -> None:
    data = {"hotkeys": {"dota_coach_ask": "<ctrl>+<shift>+f9"}}

    _migrate_hotkey_defaults(data)

    assert data["hotkeys"]["dota_coach_ask"] == "<ctrl>+<shift>+f9"
