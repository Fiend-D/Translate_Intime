"""Channel start/stop must not block the Qt thread on slow engine connects."""

from __future__ import annotations

import threading
import time

import pytest

from src.models.config import AppConfigModel
from src.models.enums import Direction


class _SlowPipeline:
    def __init__(self, *, fail: bool = False, fail_on: Direction | None = None) -> None:
        self.fail = fail
        self.fail_on = fail_on
        self.mode = "volc"
        self.loading_models: tuple[str, ...] = ()
        self.active: set[Direction] = set()
        self.started_on: list[str] = []
        self.closed_on: list[str] = []
        self._config = AppConfigModel()

    def process_tick(self) -> None:
        return None

    def can_start(self) -> tuple[bool, str]:
        return True, ""

    def start_channel(self, direction: Direction, *, play_voice: bool) -> None:
        self.started_on.append(threading.current_thread().name)
        time.sleep(0.4)
        if self.fail or direction == self.fail_on:
            raise RuntimeError("连接超时")
        self.active.add(direction)

    def close_engine(self) -> None:
        self.closed_on.append(threading.current_thread().name)

    def stop_channel(self, direction: Direction) -> None:
        time.sleep(0.2)
        self.active.discard(direction)

    def stop(self) -> None:
        self.active.clear()

    def is_channel_active(self, direction: Direction) -> bool:
        return direction in self.active

    def active_channels(self) -> list[Direction]:
        return sorted(self.active, key=lambda d: d.value)


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
    monkeypatch.setattr(mw.MainWindow, "_start_resource_monitor", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_ask_unsaved", lambda self, _action: "discard")
    win = mw.MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "_is_dirty", lambda: False)
    monkeypatch.setattr(win, "_validate_devices_for_start", lambda _c: True)
    monkeypatch.setattr(win, "_sync_overlays_visibility", lambda **_k: None)
    win._config = win._config.model_copy(update={"translation_mode": "volc"})
    yield win
    win._closing = True
    win._channel_ops.shutdown(wait=True)


def test_start_runs_off_ui_thread_and_updates_buttons(window, qtbot) -> None:
    pipeline = _SlowPipeline()
    window._pipeline = pipeline
    window._ensure_pipeline = lambda: pipeline

    began = time.monotonic()
    window._on_channel_toggle("game")
    assert time.monotonic() - began < 0.2
    assert not window._btn_game.isEnabled()

    qtbot.waitUntil(lambda: window._btn_game.isEnabled(), timeout=3000)
    assert pipeline.is_channel_active(Direction.INBOUND)
    assert pipeline.started_on[0].startswith("channel-op")
    assert "停止" in window._btn_game.text()

    window._on_channel_toggle("game")
    qtbot.waitUntil(lambda: not pipeline.active, timeout=3000)
    qtbot.waitUntil(lambda: window._btn_game.isEnabled(), timeout=3000)
    assert "开始" in window._btn_game.text()


def test_repeated_errors_share_one_dialog(window, qtbot) -> None:
    pipeline = _SlowPipeline(fail=True)
    window._pipeline = pipeline
    window._ensure_pipeline = lambda: pipeline

    window._on_channel_toggle("game")
    qtbot.waitUntil(lambda: window._error_box is not None, timeout=3000)
    window._on_error("第二个错误")
    window._on_error("第三个错误")

    assert window._error_box is not None
    assert window._error_box.text() == "第三个错误"
    assert window._error_repeats == 2
    assert window._pipeline is None


def test_unplugged_device_stops_channel_and_resets_buttons(window, qtbot) -> None:
    pipeline = _SlowPipeline()
    pipeline.active.add(Direction.INBOUND)
    window._pipeline = pipeline
    window._refresh_channel_buttons()

    window._on_channel_lost(Direction.INBOUND.value, "游戏声音音频设备已断开")

    qtbot.waitUntil(lambda: not pipeline.active, timeout=3000)
    qtbot.waitUntil(lambda: window._btn_game.isEnabled(), timeout=3000)
    assert "开始" in window._btn_game.text()
    assert window._error_box is not None


def _use(window, pipeline: _SlowPipeline) -> None:
    window._pipeline = pipeline
    window._ensure_pipeline = lambda: pipeline


def test_failed_start_keeps_pipeline_of_channel_still_starting(window, qtbot) -> None:
    pipeline = _SlowPipeline(fail_on=Direction.OUTBOUND)
    _use(window, pipeline)

    window._on_channel_toggle("mic")
    window._on_channel_toggle("game")
    qtbot.waitUntil(lambda: not window._channel_busy, timeout=3000)

    assert window._pipeline is pipeline
    assert pipeline.is_channel_active(Direction.INBOUND)


def test_stop_all_also_stops_channel_that_was_starting(window, qtbot) -> None:
    pipeline = _SlowPipeline()
    _use(window, pipeline)

    window._on_channel_toggle("game")
    window._on_stop()
    qtbot.waitUntil(lambda: not window._channel_busy, timeout=3000)

    assert not pipeline.active


def test_setting_change_resets_engine_after_pending_start(window, qtbot) -> None:
    pipeline = _SlowPipeline()
    _use(window, pipeline)

    window._on_channel_toggle("game")
    assert window._reset_engine_for_setting({"device_preference": "cpu"}) is True
    qtbot.waitUntil(lambda: not window._channel_busy, timeout=3000)

    assert not pipeline.active
    assert pipeline.closed_on and pipeline.closed_on[0].startswith("channel-op")
    assert pipeline._config.device_preference == "cpu"


def test_typed_translate_and_transcripts_are_embedded_pages(window) -> None:
    from src.gui import main_window as mw

    window._nav_group_buttons.button(mw._PAGE_TYPED).click()
    assert window._pages.currentIndex() == mw._PAGE_TYPED
    assert window._page_title.text() == "书信"

    window._nav_group_buttons.button(mw._PAGE_TRANSCRIPTS).click()
    assert window._pages.currentIndex() == mw._PAGE_TRANSCRIPTS
    assert window._transcript_panel.isVisibleTo(window)
    assert not hasattr(window, "_btn_unlock")


def test_merged_overlay_serves_both_channels(window) -> None:
    window._chk_subtitle_merged.setChecked(True)
    game = window._ensure_overlay(Direction.INBOUND)
    mic = window._ensure_overlay(Direction.OUTBOUND)
    assert game is mic
    assert game.key == "merged"
    assert window._overlay_windows() == [game]

    window._drop_overlay(Direction.INBOUND)
    assert window._overlays == {Direction.OUTBOUND: game}
    window._drop_overlay(Direction.OUTBOUND)
    assert window._overlays == {}

    window._chk_subtitle_merged.setChecked(False)
    assert window._config.subtitle_merged is True  # waits for 保存
    window._on_save()
    assert window._ensure_overlay(Direction.INBOUND) is not window._ensure_overlay(
        Direction.OUTBOUND
    )
    window._close_overlays()


def test_phrase_hotkey_types_translation_of_nth_phrase(window, monkeypatch) -> None:
    from src.models.config import QuickPhrase, QuickPhraseGroup, QuickPhraseSettings

    settings = QuickPhraseSettings(
        groups=[
            QuickPhraseGroup(
                phrases=[
                    QuickPhrase(text="撤退", translation="Retreat", translated_to="en"),
                    QuickPhrase(text="推中", auto_enter=True),
                ]
            )
        ]
    )
    window._config = window._config.model_copy(update={"quick_phrases": settings})
    typed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        window._phrase_inserter,
        "insert",
        lambda text, *, press_enter=False: typed.append((text, press_enter)) or True,
    )
    window._send_phrase_slot(1)
    window._send_phrase_slot(2)
    window._send_phrase_slot(3)
    assert typed == [("Retreat", False), ("推中", True)]


def test_merged_mode_links_the_two_show_switches(window) -> None:
    assert window._config.subtitle_merged is True
    assert window._chk_show_mic.text() == window._chk_show_game.text() == "显示字幕浮层"
    window._chk_show_mic.setChecked(False)
    assert window._chk_show_game.isChecked() is False
    window._chk_show_game.setChecked(True)
    assert window._chk_show_mic.isChecked() is True

    window._chk_subtitle_merged.setChecked(False)
    window._on_save()
    assert window._chk_show_mic.text() == "显示浮层"
    window._chk_show_mic.setChecked(False)
    assert window._chk_show_game.isChecked() is True
