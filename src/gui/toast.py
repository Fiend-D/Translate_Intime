"""Short-lived on-screen toast for hotkey / status feedback."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QLabel, QWidget


class FloatingToast(QWidget):
    """Centered translucent toast that auto-hides."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        font = QFont()
        font.setFamilies(["SF Pro Display", "PingFang SC", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        self._label.setFont(font)
        self._label.setStyleSheet(
            "color: white; background: rgba(20,20,22,0.82); "
            "border-radius: 14px; padding: 14px 22px;"
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, *, ms: int = 1400) -> None:
        self._label.setText(text)
        self._label.adjustSize()
        w = max(220, self._label.width() + 8)
        h = max(48, self._label.height() + 8)
        self._label.setGeometry(0, 0, w, h)
        self.resize(w, h)

        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - w // 2,
                geo.center().y() - h // 2 - 40,
            )
        self.show()
        self.raise_()
        self._timer.start(ms)


_toast: FloatingToast | None = None


def show_toast(text: str, *, ms: int = 1400) -> None:
    global _toast
    if _toast is None:
        _toast = FloatingToast()
    _toast.show_message(text, ms=ms)
