"""Audio ducking / ducking control for session audio playback."""

from __future__ import annotations

from typing import Literal


Mode = Literal["duck", "mute", "mix"]


class SessionDucker:
    """Controls audio ducking behavior during translation sessions."""

    def __init__(self, *, mode: Mode = "duck", duck_gain: float = 0.2) -> None:
        self._mode: Mode = mode
        self._duck_gain = max(0.0, min(1.0, float(duck_gain)))
        self._is_active = False

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def duck_gain(self) -> float:
        return self._duck_gain

    def configure(self, *, mode: Mode, duck_gain: float) -> None:
        self._mode = mode
        self._duck_gain = max(0.0, min(1.0, float(duck_gain)))

    def pulse(self) -> None:
        self._is_active = True

    def get_current_gain(self) -> float:
        if self._mode == "mute":
            return 0.0
        if self._mode == "duck":
            return self._duck_gain
        return 1.0

    def close(self) -> None:
        self._is_active = False
