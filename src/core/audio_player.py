"""Audio playback and output routing using sounddevice."""

import contextlib
import threading
import time
from collections.abc import Callable
from queue import Queue
from typing import Any

import numpy as np

from src.utils.audio_utils import (
    bytes_to_pcm16_array,
    enhance_clarity,
    resample,
    secure_clear,
)
from src.utils.logger import logger

_TTS_SR = 16000


def _sounddevice() -> Any:
    """Import PortAudio bindings only when playback/device access is requested."""
    import sounddevice

    return sounddevice


class AudioPlayer:
    """Plays PCM16 audio bytes to a selected output device."""

    def __init__(self, device_id: int | str | None = None) -> None:
        self.device_id = device_id
        self._queue: Queue[bytes] = Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stream: Any | None = None
        self._stream_key: tuple[object, int, int] | None = None
        self._stream_lock = threading.Lock()
        self._device_sr = _TTS_SR
        self._device_ch = 2
        self._device_name: str = ""
        self._query_device()
        # 回调：每次一段播放结束后调用，传入该段真实播放时长（秒）
        # 用途：外部可以据此延长麦克风回灌静音窗口
        self.on_segment_finished: Callable[[float], None] | None = None

    def _query_device(self) -> None:
        """查询目标设备的实际采样率和声道数，优先升级到 WASAPI 2ch 版本。"""
        try:
            sd = _sounddevice()
            dev = self.device_id
            if isinstance(dev, str):
                # Linux Pulse sink 名称，无法直接查询
                return
            if dev is None:
                dev = sd.default.device[1]
            info = sd.query_devices(dev)
            api_idx = info.get("hostapi", -1)
            ch = int(info.get("max_output_channels", 2))
            name = info.get("name", "")

            # 如果选中的是 MME/DirectSound 且声道 > 2，查找同名的 WASAPI 2ch 版本
            api_name = ""
            if api_idx >= 0:
                with contextlib.suppress(Exception):
                    api_name = sd.query_hostapis(api_idx).get("name", "")

            if api_name in ("MME", "Windows DirectSound") and ch > 2:
                wasapi_dev = self._find_wasapi_device(name)
                if wasapi_dev is not None:
                    logger.info(
                        f"设备升级: [{dev}] {api_name} {ch}ch → "
                        f"[{wasapi_dev}] WASAPI 2ch"
                    )
                    self.device_id = wasapi_dev
                    dev = wasapi_dev
                    info = sd.query_devices(dev)

            self._device_sr = int(info.get("default_samplerate", _TTS_SR))
            self._device_ch = min(max(int(info.get("max_output_channels", 2)), 1), 2)
            self._device_name = str(info.get("name", ""))
        except Exception:
            pass

    @staticmethod
    def _find_wasapi_device(name: str) -> int | None:
        """查找同名设备的 WASAPI 2ch 版本。"""
        try:
            sd = _sounddevice()
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_output_channels", 0) != 2:
                    continue
                if d.get("name", "") != name:
                    continue
                api_idx = d.get("hostapi", -1)
                if api_idx < 0:
                    continue
                api_name = sd.query_hostapis(api_idx).get("name", "")
                if api_name == "Windows WASAPI":
                    return i
        except Exception:
            pass
        return None

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        """Return a list of available audio output devices.

        De-duplicates PortAudio's MME/DirectSound/WASAPI/WDM-KS variants per
        endpoint, picks the highest-quality host-API variant, and keeps the
        longest (untruncated) name so Voicemeeter/VB-Cable names are readable.
        """
        try:
            from src.audio.device_listing import list_output_devices

            return list_output_devices()
        except Exception:
            # Fallback: plain sounddevice enumeration if the helper is missing.
            sd = _sounddevice()
            devices: list[dict[str, Any]] = []
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
        logger.info(
            f"Audio player started: device={self.device_id!r} "
            f"sr={self._device_sr} ch={self._device_ch}"
        )

    def stop(self) -> None:
        """Stop the playback thread and clear queued audio."""
        self._running = False
        self.clear_queue()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_stream()
        logger.info("Audio player stopped")

    def clear_queue(self) -> None:
        """Drop pending playback and close the active stream best-effort."""
        while not self._queue.empty():
            data = self._queue.get()
            secure_clear(bytearray(data))
        self._close_stream()

    @property
    def is_playing(self) -> bool:
        """True if there's pending audio in queue or an active stream."""
        if not self._queue.empty():
            return True
        with self._stream_lock:
            return self._stream is not None and self._stream.active

    @property
    def queue_size(self) -> int:
        """Number of pending PCM chunks in the queue."""
        return self._queue.qsize()

    def play(self, pcm16_bytes: bytes) -> None:
        """Enqueue PCM16 mono audio for playback."""
        if pcm16_bytes:
            self._queue.put(pcm16_bytes)

    def _playback_loop(self) -> None:
        while self._running or not self._queue.empty():
            try:
                data = self._queue.get(timeout=0.1)
            except Exception:
                continue
            t0 = time.time()
            try:
                self._play_immediate(data)
            except Exception as exc:
                logger.error(f"Audio playback error: {exc}")
            finally:
                # 通知外部该段真实播放耗时（覆盖段间开销/重采样延迟）
                if self.on_segment_finished is not None:
                    with contextlib.suppress(Exception):
                        self.on_segment_finished(time.time() - t0)
                secure_clear(bytearray(data))

    def set_device(self, device_id: int | str | None) -> None:
        """Change the output device."""
        self.device_id = device_id
        self._query_device()
        self._close_stream()

    def _is_virtual_device(self) -> bool:
        """True if output device is a virtual cable (VB-Cable / Voicemeeter).

        These devices route audio to meeting software whose noise gate / AGC
        degrades low-level TTS, so enhance_clarity is needed.
        Physical speakers / headphones skip it to preserve natural TTS timbre.
        """
        name = (self._device_name or "").lower()
        if not name:
            return False
        markers = ("cable", "voicemeeter", "vb-audio", "virtual")
        return any(m in name for m in markers)

    def _play_immediate(self, pcm16_bytes: bytes) -> None:
        # int16 → float32
        samples = bytes_to_pcm16_array(pcm16_bytes).astype(np.float32) / 32768.0

        # 仅对虚拟声卡 (VB-Cable / Voicemeeter) 应用 enhance_clarity:
        # 这类设备的对端 (会议软件) 会做降噪/AGC, 需要最大化电平 + 高频补偿.
        # 直接输出到物理音响/耳机时跳过, 保留 Kokoro TTS 的自然音质.
        if self._is_virtual_device():
            samples = enhance_clarity(samples, _TTS_SR)

        # 重采样到设备实际采样率（避免 PortAudio 低质量重采样）
        if self._device_sr != _TTS_SR:
            samples = resample(samples, _TTS_SR, self._device_sr)

        # 单声道转立体声（匹配设备声道数，避免 VB-Cable 16ch 映射错误）
        if self._device_ch == 2 and samples.ndim == 1:
            samples = np.column_stack([samples, samples])
        elif self._device_ch == 1 and samples.ndim == 1:
            samples = samples.reshape(-1, 1)

        with self._stream_lock:
            stream = self._ensure_stream_locked()
            stream.write(np.ascontiguousarray(samples, dtype=np.float32))

    def _resolve_device(self) -> int | None:
        device = self.device_id
        if isinstance(device, str):
            import os

            os.environ["PULSE_SINK"] = device
            return None
        return device

    def _ensure_stream_locked(self) -> Any:
        sd = _sounddevice()
        device = self._resolve_device()
        key = (device, self._device_sr, self._device_ch)
        if self._stream is not None and self._stream_key == key and self._stream.active:
            return self._stream
        self._close_stream_locked()
        self._stream = sd.OutputStream(
            samplerate=self._device_sr,
            channels=self._device_ch,
            dtype="float32",
            device=device,
        )
        self._stream.start()
        self._stream_key = key
        logger.info(
            f"Audio output stream opened: device={self.device_id!r} "
            f"sr={self._device_sr} ch={self._device_ch}"
        )
        return self._stream

    def _close_stream(self) -> None:
        with self._stream_lock:
            self._close_stream_locked()

    def _close_stream_locked(self) -> None:
        stream = self._stream
        self._stream = None
        self._stream_key = None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
            with contextlib.suppress(Exception):
                stream.close()
