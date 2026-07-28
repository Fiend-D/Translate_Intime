"""Key-capture line edit for customizing global hotkeys."""

from __future__ import annotations

from typing import override

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QLineEdit

from src.utils.hotkeys import combo_to_display, normalize_combo

_QT_KEY_MAP: dict[int, str] = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page_up",
    Qt.Key.Key_PageDown: "page_down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
}
for _i in range(1, 25):
    _QT_KEY_MAP[getattr(Qt.Key, f"Key_F{_i}")] = f"f{_i}"


class HotkeyEdit(QLineEdit):
    """Click then press a combo; Esc clears; Backspace clears."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._combo = ""
        self.setReadOnly(True)
        self.setPlaceholderText("点击后按下快捷键（Esc 清空）")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str) -> None:
        self._combo = normalize_combo(combo)
        self.setText(combo_to_display(self._combo) or "")

    @override
    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None:
            return
        key = event.key()
        if key in (Qt.Key.Key_Escape,):
            self.set_combo("")
            event.accept()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not event.modifiers():
            self.set_combo("")
            event.accept()
            return

        mods = event.modifiers()
        # Ignore pure modifier presses
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_AltGr,
        ):
            event.accept()
            return

        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")

        if key in _QT_KEY_MAP:
            parts.append(_QT_KEY_MAP[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(ord("a") + (key - Qt.Key.Key_A)))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(ord("0") + (key - Qt.Key.Key_0)))
        else:
            # Fallback via QKeySequence
            seq = QKeySequence(int(mods) | key).toString(
                QKeySequence.SequenceFormat.PortableText
            )
            token = seq.split("+")[-1].strip().lower() if seq else ""
            if not token or token in {"ctrl", "alt", "shift", "meta"}:
                event.accept()
                return
            parts.append(token)

        # Require at least one modifier for safety (avoid stealing typing keys)
        if len(parts) < 2:
            self.setPlaceholderText("请带修饰键，例如 Ctrl+Alt+M")
            event.accept()
            return

        self.set_combo("+".join(parts))
        event.accept()

    @override
    def focusInEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().focusInEvent(event)
        self.setPlaceholderText("按下组合键… Esc 清空")

    @override
    def focusOutEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().focusOutEvent(event)
        self.setPlaceholderText("点击后按下快捷键（Esc 清空）")
