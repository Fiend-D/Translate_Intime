"""Microphone and loopback audio capture.

Supports:
- integer PortAudio / PyAudio device indices
- string PulseAudio monitor names / WASAPI loopback IDs via AudioStream
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from queue import Empty, Queue
from typing import Any

import numpy as np

from src.core.exceptions import EngineRuntimeError
from src.models.enums import Direction
from src.models.internal import AudioChunk
from src.utils.logger import logger

CHUNK_DURATION_MS = 40  # smaller chunks → lower capture batching latency


class AudioCapture:
    """Captures audio from a device and emits AudioChunk objects."""

    def __init__(
        self,
        direction: Direction,
        device: int | str | None,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self.direction = direction
        self.device = device
        # Backward-compatible alias used by older call sites
        self.device_index = device if isinstance(device, int) else None
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue: Queue[AudioChunk] = Queue()
        self._running = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pyaudio = None
        self._stream = None
        self._audio_stream = None
        # Optional low-latency sink (e.g. Volc). When set, PCM bypasses the Qt tick queue.
        self.on_pcm: Callable[[bytes], None] | None = None

    def list_devices(self) -> list[dict[str, Any]]:
        return self.list_input_devices()

    @staticmethod
    def list_input_devices() -> list[dict[str, Any]]:
        """Return PortAudio input devices (integer indices)."""
        import pyaudio

        audio = pyaudio.PyAudio()
        try:
            devices = []
            for i in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    devices.append(
                        {
                            "index": i,
                            "name": info.get("name", ""),
                            "channels": info.get("maxInputChannels", 0),
                            "sample_rate": int(info.get("defaultSampleRate", 16000)),
                        }
                    )
            return devices
        finally:
            audio.terminate()

    def start(self) -> None:
        """Start the capture stream."""
        with self._lock:
            if self._running:
                return
            try:
                # Must be True before spawning the Pulse/WASAPI poll thread,
                # otherwise _poll_stream_loop sees False and exits immediately.
                self._running = True
                if isinstance(self.device, str):
                    self._start_stream_backend()
                else:
                    self._start_pyaudio_backend()
                logger.info(
                    f"Audio capture started for {self.direction.value} "
                    f"(device={self.device!r})"
                )
            except Exception as exc:
                self._running = False
                self._cleanup_backends()
                raise EngineRuntimeError(
                    f"Failed to start audio capture for {self.direction.value}: {exc}"
                ) from exc

    def _start_pyaudio_backend(self) -> None:
        import pyaudio

        self._pyaudio = pyaudio.PyAudio()
        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.device if isinstance(self.device, int) else None,
            frames_per_buffer=int(self.sample_rate * CHUNK_DURATION_MS / 1000),
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def _start_stream_backend(self) -> None:
        from src.audio.stream import AudioStream

        chunk_size = max(256, int(self.sample_rate * CHUNK_DURATION_MS / 1000))
        self._audio_stream = AudioStream(
            device=self.device,
            sample_rate=self.sample_rate,
            channels=self.channels,
            chunk_size=chunk_size,
        )
        self._audio_stream.open_input()
        self._thread = threading.Thread(
            target=self._poll_stream_loop,
            name=f"capture-{self.direction.value}",
            daemon=True,
        )
        self._thread.start()

    def _poll_stream_loop(self) -> None:
        assert self._audio_stream is not None
        while self._running:
            try:
                samples = self._audio_stream.read_chunk()
            except Exception as exc:
                logger.warning(f"AudioStream read failed ({self.direction.value}): {exc}")
                break
            if samples is None:
                import time as _time
                _time.sleep(0.005)
                continue
            pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16).tobytes()
            self._emit_pcm(pcm)

    def _emit_pcm(self, pcm: bytes) -> None:
        """Deliver PCM to low-latency sink and/or the tick queue."""
        if self.on_pcm is not None:
            try:
                self.on_pcm(pcm)
            except Exception as exc:
                logger.debug(f"on_pcm sink failed ({self.direction.value}): {exc}")
            return
        self._queue.put(
            AudioChunk(
                direction=self.direction,
                sample_rate=self.sample_rate,
                channels=self.channels,
                data=pcm,
            )
        )

    def stop(self) -> None:
        """Stop the capture stream."""
        with self._lock:
            self._running = False
            self._cleanup_backends()
            logger.info(f"Audio capture stopped for {self.direction.value}")

    def _cleanup_backends(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop_stream()
                self._stream.close()
            self._stream = None
        if self._pyaudio is not None:
            with contextlib.suppress(Exception):
                self._pyaudio.terminate()
            self._pyaudio = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._audio_stream is not None:
            with contextlib.suppress(Exception):
                self._audio_stream.close()
            self._audio_stream = None

    def _callback(
        self,
        in_data: bytes,
        frame_count: int,
        time_info: dict[str, float],
        status_flags: int,
    ) -> tuple[bytes | None, int]:
        import pyaudio

        del frame_count, time_info, status_flags
        self._emit_pcm(in_data)
        return (None, pyaudio.paContinue)

    def get_chunks(self) -> list[AudioChunk]:
        """Drain all currently queued chunks."""
        chunks: list[AudioChunk] = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except Empty:
                break
        return chunks

    def is_running(self) -> bool:
        with self._lock:
            return self._running
