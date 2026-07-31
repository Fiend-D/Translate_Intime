"""Utterance segmentation for economy-mode PCM streams."""

from __future__ import annotations

import threading
import time

import numpy as np


class UtteranceBuffer:
    """Buffer PCM16 @16 kHz mono and flush on silence gap or max length.

    Optimization: instead of waiting for ``end_silence_ms`` of *no PCM push*
    (which never happens on continuous audio like game video soundtrack),
    we also inspect the RMS energy of a recent quiet window. If that window
    stays quiet long enough after ``soft_split_ms``, we treat it as end of
    phrase and flush. This cuts latency for the inbound (game) channel while
    avoiding mid-sentence cuts on continuous game audio.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        end_silence_ms: int = 300,
        min_ms: int = 300,
        max_ms: int = 8000,
        soft_split_ms: int = 6000,
        soft_split_quiet_ms: int = 280,
        tail_rms_threshold: float = 0.003,
    ) -> None:
        self._sample_rate = max(1, int(sample_rate))
        self._end_silence_ms = max(0, int(end_silence_ms))
        self._min_ms = max(0, int(min_ms))
        self._max_ms = max(self._min_ms, int(max_ms))
        self._soft_split_ms = max(self._min_ms, int(soft_split_ms))
        quiet_ms = max(50, int(soft_split_quiet_ms))
        self._quiet_samples = max(64, int(quiet_ms * self._sample_rate / 1000.0))
        self._tail_rms_threshold = float(tail_rms_threshold)
        self._buf = bytearray()
        self._lock = threading.RLock()
        self.last_push_at: float = 0.0

    def _duration_ms(self) -> float:
        samples = len(self._buf) // 2
        return (samples / self._sample_rate) * 1000.0

    def _quiet_window_rms(self) -> float:
        """RMS of the last ``soft_split_quiet_ms`` of buffered audio."""
        need = self._quiet_samples * 2
        if len(self._buf) < need:
            return 1.0
        window = self._buf[-need:]
        arr = np.frombuffer(bytes(window), dtype=np.int16).astype(np.float32) / 32768.0
        if arr.size == 0:
            return 1.0
        return float(np.sqrt(np.mean(np.square(arr))))

    def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            self._buf.extend(pcm)
            self.last_push_at = time.monotonic()

    def mark_capture_active(self) -> None:
        """Keep a buffered utterance alive while PCM is temporarily held upstream."""
        with self._lock:
            if self._buf:
                self.last_push_at = time.monotonic()

    def poll(self) -> bytes | None:
        with self._lock:
            if not self._buf:
                return None
            duration = self._duration_ms()
            # Hard cap — always flush when we hit the absolute ceiling.
            if duration >= self._max_ms:
                return self.flush()
            if self.last_push_at <= 0:
                return None
            gap_ms = (time.monotonic() - self.last_push_at) * 1000.0
            # Push-gap silence (VAD has stopped delivering data).
            if gap_ms >= self._end_silence_ms and duration >= self._min_ms:
                return self.flush()
            # Continuous audio path (e.g. game soundtrack with background music).
            # Require a longer quiet window (soft_split_quiet_ms) so we do not
            # cut mid-phrase on brief dips in game audio.
            if (
                duration >= self._soft_split_ms
                and duration >= self._min_ms
                and self._quiet_window_rms() < self._tail_rms_threshold
            ):
                return self.flush()
            return None

    def flush(self) -> bytes | None:
        with self._lock:
            if not self._buf:
                return None
            out = bytes(self._buf)
            self._buf.clear()
            self.last_push_at = 0.0
            return out

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self.last_push_at = 0.0
