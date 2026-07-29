"""Utterance segmentation for economy-mode PCM streams."""

from __future__ import annotations

import time


class UtteranceBuffer:
    """Buffer PCM16 @16 kHz mono and flush on silence gap or max length."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        end_silence_ms: int = 450,
        min_ms: int = 400,
        max_ms: int = 12000,
    ) -> None:
        self._sample_rate = max(1, int(sample_rate))
        self._end_silence_ms = max(0, int(end_silence_ms))
        self._min_ms = max(0, int(min_ms))
        self._max_ms = max(self._min_ms, int(max_ms))
        self._buf = bytearray()
        self.last_push_at: float = 0.0

    def _duration_ms(self) -> float:
        # PCM16 mono: 2 bytes per sample
        samples = len(self._buf) // 2
        return (samples / self._sample_rate) * 1000.0

    def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._buf.extend(pcm)
        self.last_push_at = time.monotonic()

    def poll(self) -> bytes | None:
        if not self._buf:
            return None
        duration = self._duration_ms()
        if duration >= self._max_ms:
            return self.flush()
        if self.last_push_at <= 0:
            return None
        gap_ms = (time.monotonic() - self.last_push_at) * 1000.0
        if gap_ms >= self._end_silence_ms and duration >= self._min_ms:
            return self.flush()
        return None

    def flush(self) -> bytes | None:
        if not self._buf:
            return None
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def clear(self) -> None:
        self._buf.clear()
        self.last_push_at = 0.0
