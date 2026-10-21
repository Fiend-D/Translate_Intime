"""Skip a failing remote backend for a while instead of paying its timeout per sentence."""

from __future__ import annotations

import threading
import time


class CircuitBreaker:
    """Open after ``threshold`` consecutive failures; probe again after a growing cooldown."""

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_s: float = 60.0,
        max_cooldown_s: float = 300.0,
    ) -> None:
        self._threshold = threshold
        self._base_cooldown = cooldown_s
        self._max_cooldown = max_cooldown_s
        self._cooldown = cooldown_s
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """True when the backend may be tried (closed, or one half-open probe)."""
        with self._lock:
            now = time.monotonic()
            if self._failures < self._threshold:
                return True
            if now < self._open_until:
                return False
            # Half-open: let this call through; a failure re-opens immediately.
            self._open_until = now + self._cooldown
            return True

    def record(self, ok: bool) -> bool:
        """Record an outcome; returns True when this call just opened the breaker."""
        with self._lock:
            if ok:
                self._failures = 0
                self._cooldown = self._base_cooldown
                self._open_until = 0.0
                return False
            self._failures += 1
            if self._failures < self._threshold:
                return False
            opened = self._failures == self._threshold
            if not opened:
                self._cooldown = min(self._cooldown * 2, self._max_cooldown)
            self._open_until = time.monotonic() + self._cooldown
            return opened

    @property
    def cooldown_s(self) -> float:
        return self._cooldown
