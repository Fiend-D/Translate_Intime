"""Global hotkey support via pynput, with Qt in-window fallback."""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

from src.utils.logger import logger

# action_id -> default pynput combo (empty = disabled)
DEFAULT_HOTKEYS: dict[str, str] = {
    "toggle_mic": "<ctrl>+<alt>+m",
    "toggle_game": "<ctrl>+<alt>+g",
    "stop_all": "<ctrl>+<alt>+s",
    "toggle_mic_overlay": "<ctrl>+<alt>+1",
    "toggle_game_overlay": "<ctrl>+<alt>+2",
    "toggle_all_overlays": "<ctrl>+<alt>+h",
    "music_play_pause": "<ctrl>+<alt>+p",
    "music_stop": "<ctrl>+<alt>+x",
    "music_prev": "<ctrl>+<alt>+<page_up>",
    "music_next": "<ctrl>+<alt>+<page_down>",
    "music_toggle_sidebar": "<ctrl>+<alt>+b",
    "dota_coach_ask": "<ctrl>+<alt>+c",
}

HOTKEY_LABELS: dict[str, str] = {
    "toggle_mic": "开/关麦克风通道",
    "toggle_game": "开/关游戏字幕通道",
    "stop_all": "停止全部通道",
    "toggle_mic_overlay": "开/关麦克风浮层",
    "toggle_game_overlay": "开/关游戏浮层",
    "toggle_all_overlays": "开/关全部浮层",
    "music_play_pause": "音乐：播放/暂停",
    "music_stop": "音乐：停止",
    "music_prev": "音乐：上一首",
    "music_next": "音乐：下一首",
    "music_toggle_sidebar": "音乐：显示/隐藏侧栏",
    "dota_coach_ask": "Dota教练：待命/取消（说完发建议）",
}

_MOD_ORDER = ("ctrl", "alt", "shift", "cmd", "win")
_MOD_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "command": "cmd",
    "super": "win",
    "meta": "win",
    "option": "alt",
}


class HotkeyBridge(QObject):
    """Receives hotkey events on the listener thread and emits on the Qt thread."""

    activated = pyqtSignal(str)  # action_id


def normalize_combo(raw: str) -> str:
    """Normalize user/Qt text to pynput GlobalHotKeys format, or '' if empty/invalid."""
    text = (raw or "").strip()
    if not text:
        return ""

    parts = [p.strip() for p in text.lower().replace(" ", "").split("+") if p.strip()]
    if not parts:
        return ""

    mods: list[str] = []
    key = ""
    for part in parts:
        token = part.strip("<>").lower()
        token = _MOD_ALIASES.get(token, token)
        if token in _MOD_ORDER:
            if token not in mods:
                mods.append(token)
        else:
            key = token

    if not key:
        return ""

    mods_sorted = [m for m in _MOD_ORDER if m in mods]
    chunks = [f"<{m}>" for m in mods_sorted]
    if re.fullmatch(r"f\d{1,2}", key) or key in {
        "space",
        "enter",
        "return",
        "tab",
        "esc",
        "escape",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "page_up",
        "page_down",
        "insert",
    }:
        if key == "return":
            key = "enter"
        if key == "escape":
            key = "esc"
        chunks.append(f"<{key}>")
    else:
        chunks.append(key)
    return "+".join(chunks)


def combo_to_display(combo: str) -> str:
    """Human-readable label, e.g. Ctrl+Alt+M."""
    norm = normalize_combo(combo)
    if not norm:
        return ""
    parts = []
    for part in norm.split("+"):
        token = part.strip("<>")
        label = {
            "ctrl": "Ctrl",
            "alt": "Alt",
            "shift": "Shift",
            "cmd": "Cmd",
            "win": "Win",
            "space": "Space",
            "enter": "Enter",
            "tab": "Tab",
            "esc": "Esc",
            "backspace": "Backspace",
            "delete": "Delete",
            "page_up": "PgUp",
            "page_down": "PgDown",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "home": "Home",
            "end": "End",
            "insert": "Ins",
        }.get(token, token.upper() if len(token) == 1 else token.title())
        parts.append(label)
    return "+".join(parts)


def combo_to_qt(combo: str) -> str:
    """QKeySequence portable string, e.g. Ctrl+Alt+M."""
    return combo_to_display(combo)


def _diagnose_pynput_error(exc: BaseException) -> str:
    msg = str(exc)
    lower = msg.lower()
    py = sys.executable
    if isinstance(exc, ModuleNotFoundError) or (
        isinstance(exc, ImportError) and "pynput" in lower and "no module" in lower
    ):
        return (
            f"当前解释器未安装 pynput（{py}）。"
            f"请执行: {py} -m pip install pynput"
        )
    if "display" in lower or "x connection" in lower or "not supported" in lower:
        return (
            "pynput 无法连接键盘后端（常见于 Wayland，或 DISPLAY 异常）。"
            "已回退为「窗口聚焦时」快捷键；全局热键需 X11 会话或安装可用的输入后端。"
        )
    return f"pynput 初始化失败: {exc}"


class GlobalHotkeys:
    """Register / reconfigure global hotkeys. Handlers must be thread-safe or use bridge."""

    def __init__(self, bridge: HotkeyBridge | None = None) -> None:
        self._bridge = bridge or HotkeyBridge()
        self._listener: Any = None
        self._action_by_combo: dict[str, str] = {}
        self._enabled = True
        self.global_ok = False
        self.last_error = ""

    @property
    def bridge(self) -> HotkeyBridge:
        return self._bridge

    def apply(self, mapping: dict[str, str], *, enabled: bool = True) -> bool:
        """Replace all bindings. Returns True if global (pynput) listener started."""
        self.stop()
        self._enabled = enabled
        self._action_by_combo = {}
        self.global_ok = False
        self.last_error = ""
        if not enabled:
            return False

        for action, raw in mapping.items():
            combo = normalize_combo(raw)
            if not combo:
                continue
            if combo in self._action_by_combo:
                logger.warning(
                    f"快捷键冲突 {combo_to_display(combo)}："
                    f"{self._action_by_combo[combo]} vs {action}，后者覆盖"
                )
            self._action_by_combo[combo] = action

        if not self._action_by_combo:
            return False
        return self.start()

    def start(self) -> bool:
        if self._listener is not None or not self._action_by_combo:
            return self._listener is not None
        try:
            from pynput import keyboard
        except Exception as exc:
            self.last_error = _diagnose_pynput_error(exc)
            logger.warning(self.last_error)
            return False

        def make_handler(combo: str) -> Callable[[], None]:
            def _handler() -> None:
                action = self._action_by_combo.get(combo)
                if action:
                    self._bridge.activated.emit(action)

            return _handler

        try:
            hotkeys = {combo: make_handler(combo) for combo in self._action_by_combo}
            self._listener = keyboard.GlobalHotKeys(hotkeys)
            self._listener.start()
            self.global_ok = True
            labels = ", ".join(
                f"{HOTKEY_LABELS.get(a, a)}={combo_to_display(c)}"
                for c, a in self._action_by_combo.items()
            )
            logger.info(f"全局快捷键已启用: {labels}")
            return True
        except Exception as exc:
            self._listener = None
            self.last_error = _diagnose_pynput_error(exc)
            logger.warning(self.last_error)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(Exception):
                self._listener.stop()
            self._listener = None
        self.global_ok = False

    def pause(self) -> None:
        """Temporarily stop the listener without clearing bindings."""
        self.stop()

    def resume(self) -> bool:
        """Restart listener with previously applied bindings."""
        if not self._enabled or not self._action_by_combo:
            return False
        return self.start()


class AppHotkeys:
    """In-window QShortcut fallback (works when the main window is focused)."""

    def __init__(self, parent: QWidget, on_action: Callable[[str], None]) -> None:
        self._parent = parent
        self._on_action = on_action
        self._shortcuts: list[QShortcut] = []

    def apply(self, mapping: dict[str, str], *, enabled: bool = True) -> None:
        self.clear()
        if not enabled:
            return
        for action, raw in mapping.items():
            seq = combo_to_qt(raw)
            if not seq:
                continue
            sc = QShortcut(QKeySequence(seq), self._parent)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(lambda a=action: self._on_action(a))
            self._shortcuts.append(sc)

    def set_active(self, active: bool) -> None:
        for sc in self._shortcuts:
            sc.setEnabled(active)

    def clear(self) -> None:
        for sc in self._shortcuts:
            with contextlib.suppress(Exception):
                sc.setEnabled(False)
                sc.deleteLater()
        self._shortcuts.clear()
