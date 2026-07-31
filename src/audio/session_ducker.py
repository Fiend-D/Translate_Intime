"""Temporarily duck other Windows audio sessions while translated speech plays."""

from __future__ import annotations

import os
import platform
import threading
from typing import Any, Literal

from src.utils.logger import logger

Mode = Literal["duck", "mute", "mix"]
_SessionState = tuple[float, bool]


def _get_audio_sessions() -> list[Any]:
    if platform.system() != "Windows":
        return []
    from pycaw.pycaw import AudioUtilities

    return list(AudioUtilities.GetAllSessions())


def _session_key(session: Any) -> str:
    return str(
        getattr(session, "InstanceIdentifier", "")
        or getattr(session, "Identifier", "")
        or f"pid:{getattr(session, 'ProcessId', 0)}"
    )


class SessionDucker:
    """Save, reduce, and restore volumes for audio sessions outside this process."""

    def __init__(self, *, mode: Mode = "duck", duck_gain: float = 0.2) -> None:
        self._mode: Mode = mode
        self._duck_gain = max(0.0, min(1.0, float(duck_gain)))
        self._is_active = False
        self._saved: dict[str, _SessionState] = {}
        self._lock = threading.Lock()
        self._warned_unavailable = False

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def duck_gain(self) -> float:
        return self._duck_gain

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def configure(self, *, mode: Mode, duck_gain: float) -> None:
        next_gain = max(0.0, min(1.0, float(duck_gain)))
        if self._is_active and (mode != self._mode or next_gain != self._duck_gain):
            self.release()
        self._mode = mode
        self._duck_gain = next_gain

    def pulse(self) -> None:
        """Apply ducking and remember each affected session's original state."""
        if self._mode == "mix":
            self.release()
            return
        try:
            sessions = _get_audio_sessions()
        except Exception as exc:
            if not self._warned_unavailable:
                self._warned_unavailable = True
                logger.warning(f"原文闪避不可用，将保持原音量: {exc}")
            return
        if not sessions:
            if platform.system() == "Windows" and not self._warned_unavailable:
                self._warned_unavailable = True
                logger.warning("未找到可调整的 Windows 音频会话，原文闪避未生效")
            return

        current_pid = os.getpid()
        affected = 0
        with self._lock:
            for session in sessions:
                if int(getattr(session, "ProcessId", 0) or 0) == current_pid:
                    continue
                volume = getattr(session, "SimpleAudioVolume", None)
                if volume is None:
                    continue
                key = _session_key(session)
                try:
                    if key not in self._saved:
                        self._saved[key] = (
                            float(volume.GetMasterVolume()),
                            bool(volume.GetMute()),
                        )
                    original_gain, _original_muted = self._saved[key]
                    if self._mode == "mute":
                        volume.SetMute(1, None)
                    else:
                        volume.SetMasterVolume(min(original_gain, self._duck_gain), None)
                    affected += 1
                except Exception as exc:
                    logger.debug(f"调整音频会话失败 ({key}): {exc}")
            self._is_active = affected > 0

    def release(self) -> None:
        """Restore all sessions that were changed by :meth:`pulse`."""
        with self._lock:
            saved = self._saved
            self._saved = {}
            self._is_active = False
        if not saved:
            return
        try:
            sessions = _get_audio_sessions()
        except Exception as exc:
            logger.warning(f"恢复原文音量失败: {exc}")
            return

        for session in sessions:
            state = saved.get(_session_key(session))
            if state is None:
                continue
            volume = getattr(session, "SimpleAudioVolume", None)
            if volume is None:
                continue
            original_gain, original_muted = state
            try:
                volume.SetMasterVolume(original_gain, None)
                volume.SetMute(int(original_muted), None)
            except Exception as exc:
                logger.debug(f"恢复音频会话失败 ({_session_key(session)}): {exc}")

    def get_current_gain(self) -> float:
        if self._mode == "mute":
            return 0.0
        if self._mode == "duck":
            return self._duck_gain
        return 1.0

    def close(self) -> None:
        self.release()
