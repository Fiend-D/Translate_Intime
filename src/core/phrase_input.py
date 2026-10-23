"""Type a quick phrase into whatever input box has keyboard focus (clipboard + Ctrl+V)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import suppress

from PyQt6.QtCore import QMimeData, QObject, QTimer
from PyQt6.QtGui import QClipboard, QGuiApplication

from src.utils.logger import logger

# Give the clipboard owner change time to land before the target app reads it.
_PASTE_DELAY_MS = 60
_ENTER_DELAY_MS = 120
# Restore the user's clipboard once the target app has surely pasted.
_RESTORE_DELAY_MS = 800

KeySender = Callable[[str], None]  # "paste" | "enter"


def auto_paste_supported() -> bool:
    """Wayland forbids injecting keys into other apps; there we only copy."""
    if sys.platform.startswith("linux"):
        return os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"
    return True


def _pynput_sender() -> KeySender:
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    # A phrase hotkey (Ctrl+Shift+F1) may still be held; Shift+Ctrl+V or Alt+Ctrl+V
    # would not paste.
    held = (Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr, Key.shift, Key.shift_l, Key.shift_r)

    def send(action: str) -> None:
        for key in held:
            with suppress(Exception):
                keyboard.release(key)
        if action == "paste":
            with keyboard.pressed(Key.ctrl):
                keyboard.press("v")
                keyboard.release("v")
        elif action == "enter":
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)

    return send


def _copy_mime(source: QMimeData | None) -> QMimeData:
    copy = QMimeData()
    if source is not None:
        for fmt in source.formats():
            copy.setData(fmt, source.data(fmt))
    return copy


class PhraseInserter(QObject):
    """Paste text into the focused input of another application."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        key_sender: KeySender | None = None,
        auto_paste: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._auto_paste = auto_paste_supported() if auto_paste is None else auto_paste
        self._sender = key_sender
        self._saved: QMimeData | None = None
        self._inserted = ""
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_clipboard)

    @property
    def auto_paste(self) -> bool:
        return self._auto_paste

    def insert(self, text: str, *, press_enter: bool = False) -> bool:
        """Put ``text`` into the focused input; returns False when it was only copied."""
        text = text.strip()
        if not text:
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        if not self._restore_timer.isActive():
            # Back-to-back phrases keep the clipboard the user had before the first.
            self._saved = _copy_mime(clipboard.mimeData())
        self._restore_timer.stop()
        clipboard.setText(text, QClipboard.Mode.Clipboard)
        self._inserted = text
        sender = self._key_sender()
        if sender is None:
            self._saved = None
            return False
        QTimer.singleShot(_PASTE_DELAY_MS, lambda: self._send(sender, "paste"))
        if press_enter:
            QTimer.singleShot(
                _PASTE_DELAY_MS + _ENTER_DELAY_MS, lambda: self._send(sender, "enter")
            )
        self._restore_timer.start(_RESTORE_DELAY_MS)
        return True

    def _key_sender(self) -> KeySender | None:
        if not self._auto_paste:
            return None
        if self._sender is None:
            try:
                self._sender = _pynput_sender()
            except Exception as exc:
                logger.warning(f"无法模拟按键，常用语改为仅复制: {exc}")
                self._auto_paste = False
                return None
        return self._sender

    def _send(self, sender: KeySender, action: str) -> None:
        try:
            sender(action)
        except Exception as exc:
            logger.warning(f"常用语模拟按键失败: {exc}")

    def _restore_clipboard(self) -> None:
        saved, self._saved = self._saved, None
        clipboard = QGuiApplication.clipboard()
        if saved is None or clipboard is None:
            return
        # The user copied something else meanwhile; keep theirs.
        if clipboard.text() != self._inserted:
            return
        clipboard.setMimeData(saved, QClipboard.Mode.Clipboard)


__all__ = ["PhraseInserter", "auto_paste_supported"]
