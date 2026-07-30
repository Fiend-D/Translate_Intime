"""Qt integration tests for subtitle overlay window."""

import pytest

from src.gui.subtitle_overlay import SubtitleOverlay
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


def test_final_translation_has_reading_grace(overlay, qtbot):
    overlay.set_text("hello", "你好", is_final=True)
    assert overlay._was_final is False

    qtbot.waitUntil(lambda: overlay._was_final, timeout=1500)

    assert overlay._was_final is True


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

    overlay.setGeometry(50, 60, 480, 120)
    qtbot.wait(20)
    short_text = overlay._history_label.text()
    short_count = len([ln for ln in short_text.split("\n") if ln.strip()]) if short_text else 0

    overlay.setGeometry(50, 60, 480, 420)
    qtbot.wait(20)
    tall_text = overlay._history_label.text()
    tall_count = len([ln for ln in tall_text.split("\n") if ln.strip()]) if tall_text else 0

    assert tall_count > short_count
    assert overlay._translated.text() == "line-11"


def test_font_scales_with_window_width(overlay, qtbot):
    overlay.set_font_size(22)
    overlay.setGeometry(40, 40, 360, 130)
    qtbot.wait(20)
    small = overlay._font_size

    overlay.setGeometry(40, 40, 900, 130)
    qtbot.wait(20)
    large = overlay._font_size

    assert large > small
    assert 12 <= small <= 72
    assert 12 <= large <= 72
