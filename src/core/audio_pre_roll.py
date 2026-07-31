"""Small PCM16 pre-roll ring buffer."""

from __future__ import annotations

from collections import deque


class AudioPreRoll:
    """Stores the latest PCM16 mono audio so VAD rising edges keep speech onset."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        preroll_ms: int = 300,
        capacity_samples: int | None = None,
    ) -> None:
        samples = capacity_samples
        if samples is None:
            samples = int(sample_rate * max(0, preroll_ms) / 1000)
        self._capacity_bytes = max(0, int(samples) * 2)
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def push(self, pcm: bytes) -> None:
        if not pcm or self._capacity_bytes <= 0:
            return
        data = bytes(pcm)
        if len(data) >= self._capacity_bytes:
            self._chunks.clear()
            self._chunks.append(data[-self._capacity_bytes :])
            self._size = self._capacity_bytes
            return
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self._capacity_bytes and self._chunks:
            excess = self._size - self._capacity_bytes
            first = self._chunks[0]
            if len(first) <= excess:
                self._size -= len(first)
                self._chunks.popleft()
            else:
                self._chunks[0] = first[excess:]
                self._size -= excess

    def drain(self) -> bytes:
        if not self._chunks:
            return b""
        data = b"".join(self._chunks)
        self.clear()
        return data

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    @property
    def size_bytes(self) -> int:
        return self._size
