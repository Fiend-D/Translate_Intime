"""Quick-phrase panel, floating palette and clipboard input."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QGuiApplication, QMouseEvent

from src.core.phrase_input import PhraseInserter
from src.gui.phrase_overlay import PhraseOverlay
from src.gui.phrase_panel import PhrasePanel, format_phrase_lines, parse_phrase_lines
from src.models.config import (
    PHRASE_MAX_CHARS,
    QuickPhrase,
    QuickPhraseGroup,
    QuickPhraseSettings,
)


def _settings(*texts: str, translate: bool = True) -> QuickPhraseSettings:
    phrases = [QuickPhrase(text=t, translation=f"T:{t}", translated_to="en") for t in texts]
    return QuickPhraseSettings(groups=[QuickPhraseGroup(phrases=phrases)], translate=translate)


def test_phrase_model_clips_and_falls_back_to_existing_group() -> None:
    phrase = QuickPhrase(text="字" * 300)
    assert len(phrase.text) == PHRASE_MAX_CHARS
    settings = QuickPhraseSettings(groups=[], current_group="gone")
    assert settings.current_group == settings.groups[0].name
    assert phrase.output(translate=True) == phrase.text
    done = QuickPhrase(text="你好", translation="Hello")
    assert done.output(translate=True) == "Hello"
    assert done.output(translate=False) == "你好"
    assert done.output(translate=True, bilingual=True) == "你好\nHello"
    assert done.output(translate=False, bilingual=True) == "你好"
    assert phrase.output(translate=True, bilingual=True) == phrase.text


def test_import_export_round_trip() -> None:
    phrases = parse_phrase_lines("推中路\tPush mid\n\n  撤退  \n")
    assert [(p.text, p.translation) for p in phrases] == [("推中路", "Push mid"), ("撤退", "")]
    assert format_phrase_lines(phrases) == "推中路\tPush mid\n撤退\n"


def test_panel_adds_limits_and_reorders(qtbot) -> None:
    panel = PhrasePanel(QuickPhraseSettings())
    qtbot.addWidget(panel)
    emitted: list[QuickPhraseSettings] = []
    panel.settings_changed.connect(emitted.append)

    assert panel._input.maxLength() == PHRASE_MAX_CHARS
    panel._input.setText("集合打龙")
    panel._on_add()
    assert panel._input.text() == ""
    assert panel.add_phrase("集合打龙") is None  # duplicate
    panel.add_phrase("撤退")
    assert [p.text for p in emitted[-1].active_group().phrases] == ["集合打龙", "撤退"]

    item = panel._list.item(1)
    item.setSelected(True)
    assert item.checkState() == Qt.CheckState.Checked
    panel._on_toggle_enter()
    assert emitted[-1].active_group().phrases[1].auto_enter is True
    item = panel._list.item(1)
    assert item.text() == "⏎ 撤退"

    panel._list.takeItem(1)
    panel._list.insertItem(0, item)
    panel._on_rows_moved()
    assert [p.text for p in emitted[-1].active_group().phrases] == ["撤退", "集合打龙"]


def test_panel_marks_hand_written_translation_for_current_target(qtbot, monkeypatch) -> None:
    panel = PhrasePanel(QuickPhraseSettings())
    qtbot.addWidget(panel)
    panel.set_target("ja", "日本語")
    assert "日本語" in panel._chk_translate.text()
    phrase = panel.add_phrase("你好")
    assert phrase is not None
    panel._list.item(0).setSelected(True)

    from src.gui import phrase_panel as module

    monkeypatch.setattr(
        module.PhraseEditDialog, "exec", lambda self: module.QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(
        module.PhraseEditDialog, "values", lambda self: ("你好", "こんにちは", "常用", False)
    )
    panel._on_edit()
    edited = panel.settings.find(phrase.id)
    assert edited is not None
    assert (edited.translation, edited.translated_to) == ("こんにちは", "ja")


def test_overlay_elides_long_phrases_and_shows_hotkeys_in_tooltip(qtbot) -> None:
    settings = _settings("短句", "这是一句非常非常长的常用语，" * 6)
    overlay = PhraseOverlay(settings)
    qtbot.addWidget(overlay)
    overlay.resize(220, 200)
    overlay.show()
    qtbot.wait(20)
    chips = overlay.chips()
    assert len(chips) == 2
    short_main, short_sub = chips[0].display_texts()
    assert (short_main, short_sub) == ("T:短句", "短句")
    long_main, _ = chips[1].display_texts()
    assert long_main.endswith("…")
    assert "快捷键" not in chips[0].toolTip()
    overlay.set_hotkeys(["Ctrl+Shift+F1"])
    assert overlay.chips()[0].toolTip().endswith("快捷键：Ctrl+Shift+F1")
    assert "快捷键" not in overlay.chips()[1].toolTip()

    overlay.set_settings(_settings("短句", translate=False))
    assert overlay.chips()[0].display_texts() == ("短句", "")


def test_overlay_never_takes_focus_and_reports_clicks(qtbot) -> None:
    overlay = PhraseOverlay(_settings("推中"))
    qtbot.addWidget(overlay)
    overlay.show()
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    chip = overlay.chips()[0]
    with qtbot.waitSignal(overlay.phrase_clicked) as blocker:
        qtbot.mouseClick(chip, Qt.MouseButton.LeftButton)
    assert blocker.args == [chip.phrase_id]


def test_locked_overlay_does_not_move_and_switches_groups(qtbot) -> None:
    settings = QuickPhraseSettings(
        groups=[QuickPhraseGroup(name="游戏"), QuickPhraseGroup(name="日常")], overlay_locked=True
    )
    overlay = PhraseOverlay(settings)
    qtbot.addWidget(overlay)
    overlay.setGeometry(50, 50, 260, 200)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(40, 10),
        QPointF(90, 60),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.mousePressEvent(press)
    assert not overlay._dragging
    assert "🔒" in overlay._title.text()

    with qtbot.waitSignal(overlay.group_changed) as blocker:
        overlay._btn_next.click()
    assert blocker.args == ["日常"]

    with qtbot.waitSignal(overlay.lock_changed) as unlocked:
        overlay.set_locked(False)
    assert unlocked.args == [False]
    overlay.mousePressEvent(press)
    assert overlay._dragging
    overlay.mouseReleaseEvent(None)
    assert overlay.pos() == QPoint(50, 50)


def test_inserter_pastes_presses_enter_and_restores_clipboard(qtbot) -> None:
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("用户原来的剪贴板")
    sent: list[str] = []
    inserter = PhraseInserter(key_sender=sent.append, auto_paste=True)

    assert inserter.insert("Push mid", press_enter=True)
    assert clipboard.text() == "Push mid"
    qtbot.waitUntil(lambda: sent == ["paste", "enter"], timeout=2000)
    qtbot.waitUntil(lambda: clipboard.text() == "用户原来的剪贴板", timeout=3000)


def test_inserter_without_key_injection_only_copies(qtbot) -> None:
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    sent: list[str] = []
    inserter = PhraseInserter(key_sender=sent.append, auto_paste=False)
    assert inserter.insert("撤退") is False
    assert clipboard.text() == "撤退"
    qtbot.wait(150)
    assert sent == []


def test_panel_lists_only_originals_and_row_click_selects(qtbot) -> None:
    panel = PhrasePanel(_settings("推中", "撤退", "集合"))
    qtbot.addWidget(panel)
    panel.show()
    assert [panel._list.item(i).text() for i in range(3)] == ["推中", "撤退", "集合"]
    assert "T:推中" in panel._list.item(0).toolTip()

    rect = panel._list.visualItemRect(panel._list.item(1))
    qtbot.mouseClick(panel._list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert panel._selected_ids() == [panel.settings.active_group().phrases[1].id]
    assert panel._list.item(1).checkState() == Qt.CheckState.Checked
    assert panel._chk_all.checkState() == Qt.CheckState.PartiallyChecked
    qtbot.mouseClick(panel._list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert panel._selected_ids() == []

    panel._chk_all.click()
    assert len(panel._selected_ids()) == 3
    assert panel._chk_all.checkState() == Qt.CheckState.Checked
    panel._chk_all.click()
    assert panel._selected_ids() == []


def test_bilingual_switch_follows_translate(qtbot) -> None:
    panel = PhrasePanel(_settings("推中"))
    qtbot.addWidget(panel)
    emitted: list[QuickPhraseSettings] = []
    panel.settings_changed.connect(emitted.append)
    panel._chk_bilingual.setChecked(True)
    assert emitted[-1].bilingual is True
    panel._chk_translate.setChecked(False)
    assert not panel._chk_bilingual.isEnabled()
