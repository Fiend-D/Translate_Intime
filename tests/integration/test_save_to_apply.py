"""Settings take effect only after 保存; overlay interactions stay immediate."""

from __future__ import annotations

import pytest

from src.models.config import HotkeyConfig
from src.utils.hold_key import HoldKeyListener


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from src.audio import stream
    from src.gui import main_window as mw
    from src.utils import config_manager

    monkeypatch.setattr(config_manager, "_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(mw, "resolve_log_dir", lambda _value: tmp_path / "logs")
    monkeypatch.setattr(mw.AudioCapture, "list_input_devices", staticmethod(lambda: []))
    monkeypatch.setattr(mw.AudioPlayer, "list_devices", staticmethod(lambda: []))
    monkeypatch.setattr(stream, "list_audio_devices", lambda: {"input": [], "output": []})
    monkeypatch.setattr(mw.GlobalHotkeys, "start", lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(HoldKeyListener, "_start_global", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_resource_monitor", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_ask_unsaved", lambda self, _action: "discard")
    win = mw.MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(win._phrase_translator, "request", lambda _request: False)
    yield win
    win._closing = True
    win._channel_ops.shutdown(wait=True)


def _saved():
    from src.utils.config_manager import load_config

    return load_config()


def test_theme_applies_only_on_save(window) -> None:
    before = window._theme
    other = next(
        window._cmb_theme.itemData(i)
        for i in range(window._cmb_theme.count())
        if window._cmb_theme.itemData(i) != before
    )
    window._cmb_theme.setCurrentIndex(window._cmb_theme.findData(other))
    assert window._theme == before and window._config.theme_mode == before
    assert window._is_dirty()
    window._on_save()
    assert window._theme == other and window._config.theme_mode == other
    assert not window._is_dirty()


def test_phrases_wait_for_save_and_discard_restores(window) -> None:
    window._phrase_panel.add_phrase("集合打龙")
    saved_texts = [p.text for p in window._config.quick_phrases.active_group().phrases]
    assert "集合打龙" not in saved_texts
    assert window._is_dirty()

    window._load_config_into_ui()  # what 放弃修改 does
    assert not window._is_dirty()
    assert all(p.text != "集合打龙" for p in window._phrase_panel.settings.active_group().phrases)

    window._phrase_panel.add_phrase("撤退")
    window._on_save()
    assert any(p.text == "撤退" for p in _saved().quick_phrases.active_group().phrases)


def test_phrase_overlay_toggle_is_immediate_and_keeps_draft(window, monkeypatch) -> None:
    monkeypatch.setattr(window, "_sync_phrase_overlay", lambda: None)
    window._phrase_panel.add_phrase("推中")
    window._toggle_phrase_overlay()
    assert window._config.quick_phrases.show_overlay is True
    assert window._phrase_panel.settings.show_overlay is True
    assert any(p.text == "推中" for p in window._phrase_panel.settings.active_group().phrases)
    assert window._is_dirty()  # the new phrase itself still waits for 保存


def test_engine_model_change_waits_for_save(window, monkeypatch) -> None:
    resets: list[dict] = []
    monkeypatch.setattr(window, "_reset_economy_engine", lambda update: resets.append(update))
    combo = window._cmb_hymt_model
    current = window._config.economy_hymt_model
    other = next(combo.itemData(i) for i in range(combo.count()) if combo.itemData(i) != current)
    combo.setCurrentIndex(combo.findData(other))
    assert window._config.economy_hymt_model == current and resets == []
    window._on_save()
    assert window._config.economy_hymt_model == other
    assert resets == [{"economy_hymt_model": other}]


def test_hotkeys_and_corpus_wait_for_save(window, monkeypatch) -> None:
    applied: list[bool] = []
    monkeypatch.setattr(window, "_apply_hotkeys", lambda: applied.append(True))
    window._hotkeys_draft = HotkeyConfig(enabled=False)
    window._corpus_hotwords = ["Roshan"]
    assert window._config.hotkeys.enabled is True
    assert "Roshan" not in window._config.hotwords
    window._on_save()
    assert window._config.hotkeys.enabled is False and applied == [True]
    assert window._config.hotwords == ["Roshan"]


def test_overlay_wheel_tweaks_save_at_once(window) -> None:
    window._on_overlay_font_requested(31)
    assert window._config.subtitle_font_size == 31
    assert window._spn_font.value() == 31
    assert not window._is_dirty()


def test_model_dir_and_mirror_apply_on_save(window, tmp_path) -> None:
    from src.utils import model_fetch, resource_paths

    models = tmp_path / "models"
    window._txt_model_dir.setText(str(models))
    window._cmb_model_mirror.setCurrentIndex(window._cmb_model_mirror.findData("global"))
    assert resource_paths.model_resource_root() != models
    assert window._is_dirty()
    try:
        window._on_save()
        assert resource_paths.model_resource_root() == models
        assert model_fetch.mirror_preference() == "global"
        assert _saved().model_dir == str(models) and _saved().model_mirror == "global"
        assert models.is_dir()
    finally:
        resource_paths.set_model_dir(None)
        model_fetch.set_mirror_preference("china")


def test_unwritable_model_dir_is_rejected(window, tmp_path, monkeypatch) -> None:
    from src.gui import main_window as mw

    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    errors: list[str] = []
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda _p, _t, text: errors.append(text))
    window._txt_model_dir.setText(str(blocker / "models"))

    window._on_save()

    assert errors and "模型目录无法写入" in errors[0]
    assert window._config.model_dir == ""
