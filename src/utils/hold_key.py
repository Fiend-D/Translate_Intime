"""Detect a key (or combo) being held down and released, globally and in-window."""

from __future__ import annotations

import contextlib
import re
import threading
from typing import Any, override

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from src.utils.hotkeys import MOUSE_TOKENS, normalize_combo
from src.utils.logger import logger

_PYNPUT_ALIASES = {"cmd": "win", "return": "enter", "escape": "esc"}
# pynput names: Windows x1/x2, X11 button8/button9
_PYNPUT_MOUSE = {
    "x1": "mouse_back",
    "button8": "mouse_back",
    "x2": "mouse_forward",
    "button9": "mouse_forward",
    "middle": "mouse_middle",
}
_QT_MOUSE = {
    Qt.MouseButton.BackButton: "mouse_back",
    Qt.MouseButton.ForwardButton: "mouse_forward",
    Qt.MouseButton.MiddleButton: "mouse_middle",
}

_QT_TOKENS: dict[int, str] = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_AltGr: "alt",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Meta: "win",
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
    Qt.Key.Key_CapsLock: "caps_lock",
}
for _i in range(1, 25):
    _QT_TOKENS[getattr(Qt.Key, f"Key_F{_i}")] = f"f{_i}"


def combo_tokens(combo: str) -> frozenset[str]:
    """``"<ctrl>+<f8>"`` → ``{"ctrl", "f8"}``; empty when the combo is invalid."""
    norm = normalize_combo(combo)
    return frozenset(part.strip("<>") for part in norm.split("+") if part) if norm else frozenset()


def pynput_token(key: Any) -> str:
    """Name a pynput key the way ``normalize_combo`` spells it."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        name = re.sub(r"_(l|r|gr)$", "", name)
        return _PYNPUT_ALIASES.get(name, name)
    char = getattr(key, "char", None)
    if isinstance(char, str) and char.isprintable() and char.strip():
        return char.lower()
    vk = getattr(key, "vk", None)
    # Windows reports Ctrl+letter as a control character; the virtual key still names it.
    if isinstance(vk, int) and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return chr(vk).lower()
    return ""


def pynput_mouse_token(button: Any) -> str:
    return _PYNPUT_MOUSE.get(str(getattr(button, "name", "")), "")


def qt_token(event: QKeyEvent) -> str:
    key = event.key()
    if key in _QT_TOKENS:
        return _QT_TOKENS[key]
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(ord("a") + key - Qt.Key.Key_A)
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(ord("0") + key - Qt.Key.Key_0)
    text = event.text()
    return text.lower() if text.isprintable() and text.strip() else ""


class HoldKeyListener(QObject):
    """Emit ``pressed`` once when every key of the combo is down, ``released`` when one lifts.

    Global events come from a pynput listener thread; in-window Qt key events are
    merged in so it still works where global hooks cannot see this app (Wayland).
    OS auto-repeat and the same key seen by both sources only count once.
    """

    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, parent: QObject | None = None, *, use_global: bool = True) -> None:
        super().__init__(parent)
        self._use_global = use_global
        self._lock = threading.Lock()
        self._combo: frozenset[str] = frozenset()
        self._down: set[str] = set()
        self._active = False
        self._enabled = True
        self._listener: Any = None
        self._mouse_listener: Any = None
        self._filter_installed = False
        self.global_ok = False

    @property
    def combo(self) -> frozenset[str]:
        return self._combo

    def set_combo(self, combo: str) -> None:
        self.stop()
        self._combo = combo_tokens(combo)
        if not self._combo:
            return
        app = QApplication.instance()
        if app is not None and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True
        if self._use_global:
            self._start_global()

    def set_enabled(self, enabled: bool) -> None:
        """Ignore keys (e.g. while the user is capturing a new combo)."""
        self._enabled = enabled
        if not enabled:
            self._reset()

    def stop(self) -> None:
        for listener in (self._listener, self._mouse_listener):
            if listener is not None:
                with contextlib.suppress(Exception):
                    listener.stop()
        self._listener = None
        self._mouse_listener = None
        self.global_ok = False
        app = QApplication.instance()
        if app is not None and self._filter_installed:
            app.removeEventFilter(self)
            self._filter_installed = False
        self._reset()

    def key_down(self, token: str) -> None:
        if not token or not self._enabled:
            return
        with self._lock:
            self._down.add(token)
            fire = not self._active and bool(self._combo) and self._combo <= self._down
            self._active = self._active or fire
        if fire:
            self.pressed.emit()

    def key_up(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._down.discard(token)
            fire = self._active and token in self._combo
            if fire:
                self._active = False
        if fire:
            self.released.emit()

    def _reset(self) -> None:
        with self._lock:
            was_active = self._active
            self._down.clear()
            self._active = False
        if was_active:
            self.released.emit()

    def _start_global(self) -> None:
        try:
            from pynput import keyboard, mouse

            if self._combo - MOUSE_TOKENS.keys():
                self._listener = keyboard.Listener(
                    on_press=lambda key: self.key_down(pynput_token(key)),
                    on_release=lambda key: self.key_up(pynput_token(key)),
                )
                self._listener.start()
            if self._combo & MOUSE_TOKENS.keys():
                self._mouse_listener = mouse.Listener(on_click=self._on_global_click)
                self._mouse_listener.start()
            self.global_ok = True
        except Exception as exc:
            self._listener = None
            logger.warning(f"按住播放的全局按键监听不可用，仅在本窗口聚焦时生效: {exc}")

    def _on_global_click(self, _x: int, _y: int, button: Any, pressed: bool) -> None:
        token = pynput_mouse_token(button)
        if pressed:
            self.key_down(token)
        else:
            self.key_up(token)

    @override
    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            # Qt delivers one press to each widget up the parent chain; the set dedupes it.
            if isinstance(event, QMouseEvent) and (token := _QT_MOUSE.get(event.button(), "")):
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.key_down(token)
                else:
                    self.key_up(token)
            return False
        if event is not None and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            key_event = event
            if isinstance(key_event, QKeyEvent) and not key_event.isAutoRepeat():
                token = qt_token(key_event)
                if event.type() == QEvent.Type.KeyPress:
                    self.key_down(token)
                else:
                    self.key_up(token)
        return False


__all__ = ["HoldKeyListener", "combo_tokens", "pynput_mouse_token", "pynput_token", "qt_token"]
