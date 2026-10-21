"""Mouse-wheel guard on settings widgets."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QListView, QScrollArea, QVBoxLayout, QWidget

from src.gui.main_window import _WheelBlockFilter


def _wheel_down() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_over_combo_scrolls_page_instead_of_changing_value(qtbot) -> None:
    area = QScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    layout.addWidget(combo)
    layout.addSpacing(2000)
    area.setWidget(content)
    area.resize(200, 200)
    qtbot.addWidget(area)
    area.show()
    wheel_filter = _WheelBlockFilter()
    app = QApplication.instance()
    assert app is not None
    app.installEventFilter(wheel_filter)
    try:
        QApplication.sendEvent(combo, _wheel_down())
    finally:
        app.removeEventFilter(wheel_filter)

    assert combo.currentIndex() == 0
    bar = area.verticalScrollBar()
    assert bar is not None and bar.value() > 0


def test_wheel_inside_separate_window_is_not_blocked() -> None:
    popup_owner = QComboBox()
    popup = QListView()
    popup.setWindowFlags(Qt.WindowType.Popup)
    popup.setParent(popup_owner, Qt.WindowType.Popup)

    assert _WheelBlockFilter().eventFilter(popup, _wheel_down()) is False
