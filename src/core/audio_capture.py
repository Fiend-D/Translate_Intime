"""Microphone and loopback audio capture.

Supports:
- integer PortAudio / PyAudio device indices
- string PulseAudio monitor names / WASAPI loopback IDs via AudioStream
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from queue import Empty, Full, Queue
from typing import Any

import numpy as np

from src.core.exceptions import EngineRuntimeError
from src.models.enums import Direction
from src.models.internal import AudioChunk
from src.utils.audio_utils import StreamResampler
from src.utils.logger import logger

CHUNK_DURATION_MS = 40  # smaller chunks → lower capture batching latency
# PortAudio keeps calling back (silence included) while a device is alive.
_STALL_SECONDS = 3.0
_RAW_QUEUE_MAX = 100  # ~4 s of 40 ms blocks


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
        self._pyaudio: Any | None = None
        self._stream: Any | None = None
        self._audio_stream: Any | None = None
        self._raw: Queue[bytes] = Queue(maxsize=_RAW_QUEUE_MAX)
        self._device_channels = channels
        self._resampler: StreamResampler | None = None
        # Optional low-latency sink (e.g. Volc). When set, PCM bypasses the Qt tick queue.
        self.on_pcm: Callable[[bytes], None] | None = None
        self.on_error: Callable[[Exception], None] | None = None

    def list_devices(self) -> list[dict[str, Any]]:
        return self.list_input_devices()

    @staticmethod
    def list_input_devices() -> list[dict[str, Any]]:
        """Return deduplicated capture devices (integer indices).

        Collapses PortAudio's MME/DirectSound/WASAPI/WDM-KS duplicates and keeps
        the longest (untruncated) name so Voicemeeter/VB-Cable labels are
        readable.  Falls back to plain PyAudio enumeration on import errors.
        """
        try:
            from src.audio.device_listing import list_input_devices

            return list_input_devices()
        except Exception:
            import pyaudio

            audio = pyaudio.PyAudio()
            try:
                devices: list[dict[str, Any]] = []
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
                    f"Audio capture started for {self.direction.value} (device={self.device!r})"
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
        index = self.device if isinstance(self.device, int) else None
        rate, channels = self.sample_rate, self.channels
        try:
            self._stream = self._open_pyaudio(rate, channels, index)
        except OSError as exc:
            # WASAPI shared mode only accepts the device mix format (often 48 kHz stereo).
            info = (
                self._pyaudio.get_device_info_by_index(index)
                if index is not None
                else self._pyaudio.get_default_input_device_info()
            )
            rate = int(info.get("defaultSampleRate") or 48000)
            channels = max(1, min(2, int(info.get("maxInputChannels") or 1)))
            if (rate, channels) == (self.sample_rate, self.channels):
                raise
            logger.info(
                f"设备不支持 {self.sample_rate} Hz（{exc}），改用 {rate} Hz/{channels} 声道并重采样"
            )
            self._stream = self._open_pyaudio(rate, channels, index)
        self._device_channels = channels
        self._resampler = (
            StreamResampler(rate, self.sample_rate) if rate != self.sample_rate else None
        )
        self._thread = threading.Thread(
            target=self._pyaudio_pump, name=f"capture-{self.direction.value}", daemon=True
        )
        self._thread.start()
        self._stream.start_stream()

    def _open_pyaudio(self, rate: int, channels: int, index: int | None) -> Any:
        import pyaudio

        assert self._pyaudio is not None
        return self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=int(rate * CHUNK_DURATION_MS / 1000),
            stream_callback=self._callback,
            start=False,
        )

    def _pyaudio_pump(self) -> None:
        """Convert and deliver PyAudio blocks off the real-time callback thread."""
        last_data = time.monotonic()
        while self._running:
            try:
                raw = self._raw.get(timeout=0.5)
            except Empty:
                if self._running and time.monotonic() - last_data > _STALL_SECONDS:
                    self._fail(OSError("音频设备无数据（可能已拔出或被系统禁用）"))
                    return
                continue
            last_data = time.monotonic()
            self._emit_pcm(self._to_target_format(raw))

    def _to_target_format(self, raw: bytes) -> bytes:
        if self._device_channels == self.channels and self._resampler is None:
            return raw
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if self._device_channels > 1:
            samples = samples.reshape(-1, self._device_channels).mean(axis=1)
        if self._resampler is not None:
            samples = self._resampler.process(samples)
        return bytes(np.clip(samples * 32767.0, -32768, 32767).astype(np.int16).tobytes())

    def _fail(self, exc: Exception) -> None:
        logger.warning(f"Audio capture failed ({self.direction.value}): {exc}")
        self._running = False
        if self.on_error is not None:
            with contextlib.suppress(Exception):
                self.on_error(exc)

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
                self._fail(exc)
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
            if self._thread is not threading.current_thread():
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
        # Real-time thread: VAD/AEC/engine work here would cause input overflows.
        try:
            self._raw.put_nowait(in_data)
        except Full:
            with contextlib.suppress(Empty):
                self._raw.get_nowait()
            with contextlib.suppress(Full):
                self._raw.put_nowait(in_data)
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
