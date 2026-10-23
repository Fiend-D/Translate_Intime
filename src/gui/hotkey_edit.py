"""Key-capture line edit for customizing global hotkeys."""

from __future__ import annotations

from typing import cast, override

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QKeySequence, QMouseEvent
from PyQt6.QtWidgets import QLineEdit, QWidget

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

QT_MOUSE_TOKENS: dict[Qt.MouseButton, str] = {
    Qt.MouseButton.BackButton: "mouse_back",
    Qt.MouseButton.ForwardButton: "mouse_forward",
    Qt.MouseButton.MiddleButton: "mouse_middle",
}


def _modifier_parts(mods: Qt.KeyboardModifier) -> list[str]:
    parts: list[str] = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("win")
    return parts


class HotkeyEdit(QLineEdit):
    """Click then press a combo; Esc clears; Backspace clears."""

    capturing_changed = pyqtSignal(bool)
    combo_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        allow_single_key: bool = False,
        allow_mouse: bool = False,
    ) -> None:
        super().__init__(parent)
        self._combo = ""
        # Hold-to-play keys are usually one key (F8, `); global hotkeys need a modifier.
        self._allow_single_key = allow_single_key
        # Side/middle mouse buttons; GlobalHotKeys can't listen to the mouse.
        self._allow_mouse = allow_mouse
        self._idle_hint = (
            "点击后按下按键或鼠标侧键/中键（Esc 清空）"
            if allow_mouse
            else "点击后按下快捷键（Esc 清空）"
        )
        self.setReadOnly(True)
        self.setPlaceholderText(self._idle_hint)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str) -> None:
        previous = self._combo
        self._combo = normalize_combo(combo)
        self.setText(combo_to_display(self._combo) or "")
        if self._combo != previous:
            self.combo_changed.emit(self._combo)

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

        parts = _modifier_parts(mods)

        if key in _QT_KEY_MAP:
            parts.append(_QT_KEY_MAP[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(ord("a") + (key - Qt.Key.Key_A)))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(ord("0") + (key - Qt.Key.Key_0)))
        else:
            # Fallback via QKeySequence
            seq = QKeySequence(cast(int, mods.value) | key).toString(
                QKeySequence.SequenceFormat.PortableText
            )
            token = seq.split("+")[-1].strip().lower() if seq else ""
            if not token or token in {"ctrl", "alt", "shift", "meta"}:
                event.accept()
                return
            parts.append(token)

        # Require at least one modifier for safety (avoid stealing typing keys)
        if len(parts) < 2 and not self._allow_single_key:
            self.setPlaceholderText("请带修饰键，例如 Ctrl+Alt+M")
            event.accept()
            return

        self.set_combo("+".join(parts))
        event.accept()

    @override
    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        token = QT_MOUSE_TOKENS.get(event.button()) if event is not None else None
        if event is None or not self._allow_mouse or token is None:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.set_combo("+".join([*_modifier_parts(event.modifiers()), token]))
        event.accept()

    @override
    def focusInEvent(self, event: QFocusEvent | None) -> None:
        super().focusInEvent(event)
        self.setPlaceholderText(
            "按下按键或鼠标侧键… Esc 清空" if self._allow_mouse else "按下按键… Esc 清空"
        )
        self.capturing_changed.emit(True)

    @override
    def focusOutEvent(self, event: QFocusEvent | None) -> None:
        super().focusOutEvent(event)
        self.setPlaceholderText(self._idle_hint)
        self.capturing_changed.emit(False)
