"""Hold-to-play music: key listener and main-window playback timing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from src.gui.hotkey_edit import HotkeyEdit
from src.utils.hold_key import HoldKeyListener, combo_tokens, pynput_mouse_token, pynput_token
from src.utils.hotkeys import combo_to_display


def test_combo_tokens_and_pynput_names() -> None:
    assert combo_tokens("<ctrl>+<f8>") == {"ctrl", "f8"}
    assert combo_tokens("") == frozenset()
    assert pynput_token(SimpleNamespace(name="ctrl_l")) == "ctrl"
    assert pynput_token(SimpleNamespace(name="alt_gr")) == "alt"
    assert pynput_token(SimpleNamespace(name=None, char="V")) == "v"
    assert pynput_token(SimpleNamespace(name=None, char="\x16", vk=0x56)) == "v"


def test_listener_fires_once_per_hold(qtbot) -> None:
    hold = HoldKeyListener(use_global=False)
    hold.set_combo("<ctrl>+<f8>")
    events: list[str] = []
    hold.pressed.connect(lambda: events.append("down"))
    hold.released.connect(lambda: events.append("up"))

    hold.key_down("f8")
    assert events == []  # combo not complete yet
    hold.key_down("ctrl")
    hold.key_down("ctrl")  # auto-repeat / second source
    hold.key_down("f8")
    assert events == ["down"]
    hold.key_up("a")  # unrelated key
    hold.key_up("f8")
    hold.key_up("ctrl")
    assert events == ["down", "up"]

    hold.key_down("ctrl")
    hold.key_down("f8")
    hold.set_enabled(False)  # e.g. user started capturing a new key
    assert events == ["down", "up", "down", "up"]
    hold.key_down("f8")
    assert events[-1] == "up"
    hold.stop()


def _mouse(kind: QMouseEvent.Type, button: Qt.MouseButton) -> QMouseEvent:
    return QMouseEvent(
        kind,
        QPointF(5, 5),
        QPointF(5, 5),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def test_edit_captures_mouse_side_button(qtbot) -> None:
    edit = HotkeyEdit(allow_single_key=True, allow_mouse=True)
    qtbot.addWidget(edit)
    edit.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.BackButton))
    assert edit.combo() == "<mouse_back>"
    assert edit.text() == combo_to_display("<mouse_back>") == "鼠标侧键(后)"

    plain = HotkeyEdit()
    qtbot.addWidget(plain)
    plain.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.BackButton))
    assert plain.combo() == ""


def test_listener_handles_mouse_buttons(qtbot) -> None:
    assert pynput_mouse_token(SimpleNamespace(name="x1")) == "mouse_back"
    assert pynput_mouse_token(SimpleNamespace(name="button9")) == "mouse_forward"
    assert pynput_mouse_token(SimpleNamespace(name="left")) == ""

    hold = HoldKeyListener(use_global=False)
    hold.set_combo("<mouse_forward>")
    events: list[str] = []
    hold.pressed.connect(lambda: events.append("down"))
    hold.released.connect(lambda: events.append("up"))
    hold._on_global_click(0, 0, SimpleNamespace(name="x2"), True)
    press = _mouse(QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.ForwardButton)
    hold.eventFilter(None, press)  # same click seen in-window too
    assert events == ["down"]
    release = _mouse(QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.ForwardButton)
    hold.eventFilter(None, release)
    assert events == ["down", "up"]
    hold.stop()


class _FakeMusic:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.is_loaded = False
        self.is_playing = False
        self.duration_sec = 10.0
        self.calls: list[str] = []

    def stop(self) -> None:
        self.calls.append("stop")
        self.is_playing = False


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

    tracks = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    win._music_tracks = tracks
    win._cmb_music_track.blockSignals(True)
    win._cmb_music_track.clear()
    win._cmb_music_track.addItems([t.stem for t in tracks])
    win._cmb_music_track.blockSignals(False)
    music = _FakeMusic()
    monkeypatch.setattr(win, "_music", music)
    plays: list[bool] = []
    loads: list[int] = []

    def fake_start(*, show_conflict_warn: bool = True, quiet: bool = False) -> bool:
        plays.append(quiet)
        music.is_playing = True
        return True

    def fake_load(index: int, *, autoplay: bool = False) -> bool:
        loads.append(index)
        win._music_load_generation += 1
        win._music_pending_autoplay = autoplay
        return True

    monkeypatch.setattr(win, "_start_music_playback", fake_start)
    monkeypatch.setattr(win, "_load_music_index", fake_load)
    win._cmb_music_mode.setCurrentIndex(win._cmb_music_mode.findData("hold"))
    win._edt_music_hold.set_combo("<f8>")
    win._edt_music_hold.combo_changed.emit("<f8>")
    win._spn_music_delay.setValue(300)
    assert win._music_hold.combo == frozenset()  # nothing applies before 保存
    win._on_save()
    yield SimpleNamespace(win=win, music=music, plays=plays, loads=loads, tracks=tracks)
    win._closing = True
    win._channel_ops.shutdown(wait=True)


def test_hold_settings_are_saved(window) -> None:
    cfg = window.win._config
    assert (cfg.music_play_mode, cfg.music_hold_key, cfg.music_hold_delay_ms) == (
        "hold",
        "<f8>",
        300,
    )
    assert window.win._music_hold.combo == {"f8"}


def test_short_tap_plays_nothing(window, qtbot) -> None:
    hold = window.win._music_hold
    hold.key_down("f8")
    qtbot.wait(100)
    hold.key_up("f8")
    qtbot.wait(350)
    assert window.plays == [] and window.loads == []


def test_hold_plays_selected_track_and_release_stops(window, qtbot) -> None:
    win, music = window.win, window.music
    music.is_loaded, music.path = True, window.tracks[0]
    win._music_hold.key_down("f8")
    qtbot.waitUntil(lambda: window.plays == [True], timeout=1000)
    assert window.loads == []
    win._music_hold.key_up("f8")
    assert music.is_playing is False and music.calls[-1] == "stop"


def test_release_during_load_cancels_autoplay(window, qtbot) -> None:
    win = window.win
    win._cmb_music_track.blockSignals(True)
    win._cmb_music_track.setCurrentIndex(1)
    win._cmb_music_track.blockSignals(False)
    win._music_hold.key_down("f8")
    qtbot.waitUntil(lambda: window.loads == [1], timeout=1000)
    win._music_hold.key_up("f8")
    assert win._music_pending_autoplay is False
    assert win._music_hold_load_gen == win._music_load_generation


def test_free_mode_ignores_hold_key(window, qtbot) -> None:
    win = window.win
    win._cmb_music_mode.setCurrentIndex(win._cmb_music_mode.findData("free"))
    assert win._config.music_play_mode == "hold"
    win._on_save()
    assert win._config.music_play_mode == "free"
    assert win._music_hold.combo == frozenset()
    win._music_hold.key_down("f8")
    qtbot.wait(400)
    assert window.plays == [] and window.loads == []
