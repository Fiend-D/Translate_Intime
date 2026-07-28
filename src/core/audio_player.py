"""Audio playback and output routing using sounddevice."""

import threading
from queue import Queue
from typing import Any

import numpy as np
import sounddevice as sd

from src.utils.audio_utils import bytes_to_pcm16_array, secure_clear
from src.utils.logger import logger

_TARGET_SR = 22050


class AudioPlayer:
    """Plays PCM16 audio bytes to a selected output device."""

    def __init__(self, device_id: int | str | None = None) -> None:
        self.device_id = device_id
        self._queue: Queue[bytes] = Queue()
        self._running = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        """Return a list of available audio output devices."""
        devices = []
        for i, info in enumerate(sd.query_devices()):
            if info.get("max_output_channels", 0) > 0:
                devices.append(
                    {
                        "index": i,
                        "name": info.get("name", ""),
                        "channels": info.get("max_output_channels", 0),
                        "sample_rate": int(info.get("default_samplerate", 44100)),
                    }
                )
        return devices

    def start(self) -> None:
        """Start the playback thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()
        logger.info("Audio player started")

    def stop(self) -> None:
        """Stop the playback thread and clear queued audio."""
        self._running = False
        while not self._queue.empty():
            data = self._queue.get()
            secure_clear(bytearray(data))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Audio player stopped")

    def play(self, pcm16_bytes: bytes) -> None:
        """Enqueue PCM16 mono audio for playback."""
        if pcm16_bytes:
            self._queue.put(pcm16_bytes)

    def _playback_loop(self) -> None:
        while self._running:
            try:
                data = self._queue.get(timeout=0.1)
            except Exception:
                continue
            try:
                self._play_immediate(data)
            except Exception as exc:
                logger.error(f"Audio playback error: {exc}")
            finally:
                secure_clear(bytearray(data))

    def set_device(self, device_id: int | str | None) -> None:
        """Change the output device."""
        self.device_id = device_id

    def _play_immediate(self, pcm16_bytes: bytes) -> None:
        samples = bytes_to_pcm16_array(pcm16_bytes).astype(np.float32) / 32768.0
        device = self.device_id
        # sounddevice accepts int index; string Pulse sink via env is handled by caller
        if isinstance(device, str):
            import os

            os.environ["PULSE_SINK"] = device
            device = None
        sd.play(
            samples,
            samplerate=_TARGET_SR,
            device=device,
            blocking=True,
        )
        sd.wait()
