"""Temporarily duck other apps' audio while translated speech plays.

Windows uses per-app sessions from the volume mixer (pycaw); Linux adjusts
PulseAudio/PipeWire sink inputs through ``pactl``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import threading
from typing import Any, Literal

from src.utils.logger import logger

Mode = Literal["duck", "mute", "mix"]
_SessionState = tuple[float, bool]


_com_state = threading.local()


def _ensure_com() -> None:
    """pycaw is COM: every calling thread (audio, TTS, channel worker) needs its own init."""
    if getattr(_com_state, "ready", False):
        return
    import comtypes

    # Already initialised on this thread (e.g. as STA by Qt) raises; that is fine.
    with contextlib.suppress(OSError):
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    _com_state.ready = True


_PULSE_NORM = 65536
_PACTL_TIMEOUT_SEC = 2.0
# PipeWire's ALSA plugin sets no application.process.id, so our own TTS stream looks
# like any other app; tag it to keep it out of ducking.
SELF_STREAM_PROP = "translator_intime.self"
_SELF_APP_NAME = "Translator InTime"


def tag_own_audio_streams() -> None:
    """Mark streams this process opens (call before PortAudio opens any stream).

    The distinct name also gives the app its own stream-restore entry instead of
    sharing (and inheriting mute from) every other ``python3.12`` program.
    """
    if platform.system() != "Linux":
        return
    os.environ.setdefault(
        "PIPEWIRE_PROPS",
        f'{{ {SELF_STREAM_PROP}=1 application.name="{_SELF_APP_NAME}" }}',
    )
    os.environ.setdefault("PULSE_PROP", f"{SELF_STREAM_PROP}=1 application.name='{_SELF_APP_NAME}'")


def _pactl(*args: str) -> str:
    return subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        timeout=_PACTL_TIMEOUT_SEC,
        check=True,
    ).stdout


# Pulse volumes are cubic: raw 20% is about -42 dB, far deeper than duck_gain=0.2 means.
def _pulse_to_amplitude(raw: float) -> float:
    return max(0.0, raw) ** 3


def _amplitude_to_pulse(gain: float) -> float:
    return float(max(0.0, float(gain)) ** (1 / 3))


class _PulseVolume:
    """pycaw ``SimpleAudioVolume`` look-alike for one PulseAudio/PipeWire sink input."""

    def __init__(self, index: int, gain: float, muted: bool) -> None:
        self._index = str(index)
        self._gain = gain
        self._muted = muted

    def GetMasterVolume(self) -> float:  # noqa: N802
        return self._gain

    def GetMute(self) -> bool:  # noqa: N802
        return self._muted

    def SetMasterVolume(self, gain: float, _ctx: Any) -> None:  # noqa: N802
        raw = max(0, round(_amplitude_to_pulse(gain) * _PULSE_NORM))
        _pactl("set-sink-input-volume", self._index, str(raw))

    def SetMute(self, muted: int, _ctx: Any) -> None:  # noqa: N802
        _pactl("set-sink-input-mute", self._index, "1" if muted else "0")


class _PulseSession:
    def __init__(self, entry: dict[str, Any]) -> None:
        props = entry.get("properties") or {}
        self.InstanceIdentifier = f"pulse:{entry['index']}"
        own = str(props.get(SELF_STREAM_PROP) or "") == "1"
        self.ProcessId = os.getpid() if own else int(props.get("application.process.id") or 0)
        channels = [int(c.get("value", 0)) for c in (entry.get("volume") or {}).values()]
        gain = _pulse_to_amplitude(max(channels) / _PULSE_NORM) if channels else 1.0
        self.SimpleAudioVolume = _PulseVolume(int(entry["index"]), gain, bool(entry.get("mute")))


def _get_pulse_sessions() -> list[Any]:
    if shutil.which("pactl") is None:
        raise RuntimeError("未找到 pactl（需要 PulseAudio 或 PipeWire-Pulse）")
    return [_PulseSession(e) for e in json.loads(_pactl("-f", "json", "list", "sink-inputs"))]


def _get_audio_sessions() -> list[Any]:
    system = platform.system()
    if system == "Linux":
        return _get_pulse_sessions()
    if system != "Windows":
        return []
    _ensure_com()
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
            logger.warning(f"恢复原文音量失败，稍后重试: {exc}")
            # Keep the originals: dropping them would leave the game ducked for good.
            with self._lock:
                for key, original in saved.items():
                    self._saved.setdefault(key, original)
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
