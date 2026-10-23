"""Qt integration tests for subtitle overlay window."""

import pytest

from src.gui.subtitle_overlay import Caption, SubtitleOverlay
from src.models.enums import Direction


@pytest.fixture
def overlay(qtbot):
    w = SubtitleOverlay(Direction.OUTBOUND)
    qtbot.addWidget(w)
    w.show()
    return w


def test_overlay_set_text(overlay):
    overlay.set_text("你好", "Hello")
    assert overlay._original.text() == "你好"
    assert overlay._translated.text() == "Hello"


def test_partial_goes_to_live_line_without_replacing_caption(overlay):
    overlay.set_text("hello there", "你好", is_final=True)
    overlay.set_text("and then we", "正在翻译…", is_final=False)

    assert overlay._translated.text() == "你好"
    assert overlay._original.text() == "hello there"
    assert overlay._live.text() == "and then we"
    assert not overlay._live.isHidden()

    overlay.set_text("and then we", "然后我们", is_final=False)
    assert overlay._live.text() == "然后我们"


def test_next_caption_waits_for_reading_time(overlay, qtbot):
    overlay.set_text("a", "第一句话比较长需要时间读完", is_final=True)
    overlay.set_text("b", "第二句", is_final=True)

    assert overlay._translated.text() == "第一句话比较长需要时间读完"
    assert overlay._live.isHidden()

    qtbot.waitUntil(lambda: overlay._translated.text() == "第二句", timeout=4000)
    assert list(overlay._history) == [
        Caption("a", "第一句话比较长需要时间读完", Direction.OUTBOUND)
    ]


def test_caption_shows_immediately_after_reading_time(overlay):
    overlay.set_text("a", "第一句", is_final=True)
    overlay._dwell_timer.stop()
    overlay.set_text("b", "第二句", is_final=True)
    assert overlay._translated.text() == "第二句"


def test_backlog_overflow_skips_to_history(overlay):
    overlay.set_history_lines(20)
    overlay.set_text("", "line-0", is_final=True)
    for i in range(1, 8):
        overlay.set_text("", f"line-{i}", is_final=True)

    assert overlay._translated.text() == "line-0"
    assert [c.translated for c in overlay._queue] == ["line-5", "line-6", "line-7"]
    assert [c.translated for c in overlay._history] == [f"line-{i}" for i in range(1, 5)]


def test_reading_time_bounds():
    from src.gui.subtitle_overlay import reading_time_s

    assert reading_time_s("好") == pytest.approx(1.2)
    assert reading_time_s("这" * 100) == pytest.approx(4.0)
    assert reading_time_s("这是一句十个字的话。") > reading_time_s("This has ten.")


def test_overlay_lock_toggles_mouse_transparency(overlay):
    assert not overlay.is_locked()
    overlay.set_locked(True)
    assert overlay.is_locked()
    overlay.set_locked(False)
    assert not overlay.is_locked()


def test_overlay_geometry(overlay):
    overlay.setGeometry(50, 60, 400, 100)
    assert overlay.get_geometry() == (50, 60, 400, 100)


def test_taller_window_shows_more_history(overlay, qtbot):
    overlay.set_history_lines(20)
    overlay.set_show_original(False)
    overlay.set_font_size(18)
    # Seed finalized lines into history via successive finals
    overlay.set_text("", "line-0", is_final=True)
    for i in range(1, 12):
        overlay.set_text("", f"line-{i}", is_final=True)
    while overlay._queue:
        overlay._advance_queue()

    overlay.setGeometry(50, 60, 480, 120)
    qtbot.wait(20)
    short_count = len(overlay._visible_history_entries())

    overlay.setGeometry(50, 60, 480, 420)
    qtbot.wait(20)
    tall_count = len(overlay._visible_history_entries())

    assert tall_count > short_count
    assert overlay._translated.text() == "line-11"


def test_font_scales_with_window_size(overlay, qtbot):
    overlay.set_font_size(22)
    overlay.setGeometry(40, 40, 560, 130)
    qtbot.wait(20)
    assert overlay._font_size == 22

    overlay.setGeometry(40, 40, 1120, 260)
    qtbot.wait(20)
    assert overlay._font_size == 44
    margins = overlay.layout().contentsMargins()
    assert (margins.left(), margins.top()) == (40, 28)

    overlay.setGeometry(40, 40, 280, 80)
    qtbot.wait(20)
    assert overlay._font_size == 11


def test_widening_alone_does_not_zoom_text(overlay, qtbot):
    overlay.set_font_size(22)
    overlay.setGeometry(40, 40, 1400, 130)
    qtbot.wait(20)
    assert overlay._font_size == 22


def test_overlay_has_no_channel_title(overlay):
    from PyQt6.QtWidgets import QLabel

    texts = {label.text() for label in overlay.findChildren(QLabel)}
    assert "麦克风" not in texts
    assert "游戏语音" not in texts


def test_opacity_only_affects_background(overlay):
    overlay.set_opacity(0.0)
    assert overlay._opacity == 0.0
    assert "rgba(255,255,255,255)" in overlay._translated.styleSheet()
    overlay.set_opacity(0.4)
    assert overlay._opacity == 0.4
    assert "rgba(255,255,255,255)" in overlay._translated.styleSheet()


def test_menu_requests_route_through_callbacks(qtbot):
    fonts: list[int] = []
    opacities: list[float] = []
    w = SubtitleOverlay(
        Direction.INBOUND,
        font_size=70,
        on_font_size_requested=fonts.append,
        on_opacity_requested=opacities.append,
    )
    qtbot.addWidget(w)
    w._request_font_size(74)
    w._request_opacity(0.15)
    assert fonts == [72]
    assert opacities == [0.15]


def test_single_channel_overlay_has_no_speaker_tags(overlay):
    overlay.set_text("a<b", "x & y", speaker=Direction.OUTBOUND)
    assert overlay._translated.text() == "x &amp; y"
    assert "我" not in overlay._translated.text()


def test_merged_overlay_tags_each_speaker(qtbot):
    w = SubtitleOverlay(None, history_lines=5)
    qtbot.addWidget(w)
    assert w.key == "merged"
    w.set_text("Enemy mid", "中路有敌人", speaker=Direction.INBOUND)
    assert "对方" in w._translated.text()
    w._dwell_timer.stop()
    w.set_text("收到", "Copy that", speaker=Direction.OUTBOUND)
    assert "我" in w._translated.text()
    assert "对方" in w._history_label.text()

    w.set_text("push", "推", is_final=False, speaker=Direction.INBOUND)
    w.set_text("wait", "等等", is_final=False, speaker=Direction.OUTBOUND)
    live = w._live.text()
    assert "推" in live and "等等" in live
    w.clear_speaker(Direction.INBOUND)
    assert "推" not in w._live.text()
    assert "等等" in w._live.text()


def test_locked_overlay_fades_when_idle_and_wakes_on_speech(qtbot):
    w = SubtitleOverlay(None, locked=True)
    qtbot.addWidget(w)
    w.show()
    w._idle_timer.setInterval(20)
    w._fade.setDuration(10)
    w.set_text("a", "第一句")
    w._dwell_timer.stop()
    w._wake()
    qtbot.waitUntil(lambda: w.windowOpacity() < 0.05, timeout=2000)

    w.set_text("b", "第二句")
    assert w.windowOpacity() == 1.0


def test_unlocked_overlay_never_fades(qtbot):
    w = SubtitleOverlay(None, locked=False)
    qtbot.addWidget(w)
    w.set_text("a", "第一句")
    assert not w._idle_timer.isActive()
    w.set_locked(True)
    w.set_auto_hide(False)
    assert not w._idle_timer.isActive()
