"""Saved audio devices are re-identified by name when their index changes."""

from __future__ import annotations

import pytest


@pytest.fixture
def make_window(qtbot, tmp_path, monkeypatch):
    from src.audio import stream
    from src.gui import main_window as mw
    from src.utils import config_manager

    devices: dict[str, list[dict]] = {"in": [], "out": []}
    monkeypatch.setattr(config_manager, "_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(mw, "resolve_log_dir", lambda _value: tmp_path / "logs")
    monkeypatch.setattr(mw.GlobalHotkeys, "start", lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(mw.MainWindow, "_start_resource_monitor", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_ask_unsaved", lambda self, _action: "discard")
    monkeypatch.setattr(mw.AudioCapture, "list_input_devices", staticmethod(lambda: devices["in"]))
    monkeypatch.setattr(mw.AudioPlayer, "list_devices", staticmethod(lambda: devices["out"]))
    monkeypatch.setattr(stream, "list_audio_devices", lambda: {"input": [], "output": []})
    windows = []

    def make(cfg_updates: dict, inputs: list[dict]):
        devices["in"] = inputs
        devices["out"] = [{"index": 9, "name": "Headphones"}]
        cfg = config_manager.AppConfigModel(log_dir=str(tmp_path)).model_copy(update=cfg_updates)
        config_manager.save_config(cfg)
        win = mw.MainWindow()
        qtbot.addWidget(win)
        windows.append(win)
        return win

    yield make
    for win in windows:
        win._closing = True
        win._channel_ops.shutdown(wait=True)


def test_replugged_mic_is_found_by_name(make_window) -> None:
    win = make_window(
        {"input_device": 3, "input_device_name": "USB Mic"},
        [{"index": 2, "name": "Built-in Mic"}, {"index": 5, "name": "USB Mic"}],
    )

    assert win._cmb_input.currentData() == 5
    assert win._config.input_device == 5
    assert not win._is_dirty()


def test_unplugged_device_keeps_setting_and_blocks_start(make_window) -> None:
    win = make_window(
        {"input_device": 3, "input_device_name": "USB Mic"},
        [{"index": 2, "name": "Built-in Mic"}],
    )

    assert "未连接" in win._cmb_input.currentText()
    assert win._config.input_device == 3
    assert not win._is_dirty()
    assert win._missing_devices("mic") == ["USB Mic"]


def test_legacy_index_only_config_gains_the_device_name(make_window) -> None:
    win = make_window({"input_device": 2}, [{"index": 2, "name": "Built-in Mic"}])

    assert win._config.input_device_name == "Built-in Mic"
    assert not win._is_dirty()


def test_same_name_entries_keep_the_saved_index(make_window) -> None:
    win = make_window(
        {"loopback_device": 7, "loopback_device_name": "CABLE Output"},
        [{"index": 4, "name": "CABLE Output"}, {"index": 7, "name": "CABLE Output"}],
    )

    assert win._cmb_loopback.currentData() == 7
    assert win._config.loopback_device == 7


def test_refresh_does_not_reinit_audio_while_a_channel_runs(make_window, monkeypatch) -> None:
    from src.audio import device_listing

    calls: list[int] = []
    monkeypatch.setattr(device_listing, "rescan_portaudio", lambda: calls.append(1) or True)
    win = make_window({}, [{"index": 2, "name": "Built-in Mic"}])

    win._channel_busy["game"] += 1
    win._on_refresh_devices()
    assert calls == []

    win._channel_busy.clear()
    win._on_refresh_devices()
    assert calls == [1]


def test_fresh_config_is_not_reported_as_unsaved(make_window) -> None:
    win = make_window({}, [{"index": 2, "name": "Built-in Mic"}])

    assert not win._is_dirty()
