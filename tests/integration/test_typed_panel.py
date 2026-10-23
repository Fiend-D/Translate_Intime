"""Typed-translation panel input behaviour."""

from __future__ import annotations

from PyQt6.QtCore import QMimeData

from src.gui.typed_panel import TypedTranslatePanel


def test_paste_drops_source_colours(qtbot) -> None:
    panel = TypedTranslatePanel()
    qtbot.addWidget(panel)
    mime = QMimeData()
    mime.setHtml('<span style="color:#000000">黑色文字</span>')
    mime.setText("黑色文字")
    panel._input.insertFromMimeData(mime)
    assert panel._input.toPlainText() == "黑色文字"
    assert "color:#000000" not in panel._input.toHtml()
    panel.shutdown()
