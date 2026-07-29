"""Audio playback and output routing using sounddevice."""

import threading
import time
from queue import Queue
from typing import Any, Callable

import numpy as np
import sounddevice as sd

from src.utils.audio_utils import (
    bytes_to_pcm16_array,
    enhance_clarity,
    resample,
    secure_clear,
)
from src.utils.logger import logger

_TTS_SR = 16000


class AudioPlayer:
    """Plays PCM16 audio bytes to a selected output device."""

    def __init__(self, device_id: int | str | None = None) -> None:
        self.device_id = device_id
        self._queue: Queue[bytes] = Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._device_sr = _TTS_SR
        self._device_ch = 2
        self._query_device()
        # 回调：每次一段播放结束后调用，传入该段真实播放时长（秒）
        # 用途：外部可以据此延长麦克风回灌静音窗口
        self.on_segment_finished: Callable[[float], None] | None = None

    def _query_device(self) -> None:
        """查询目标设备的实际采样率和声道数，优先升级到 WASAPI 2ch 版本。"""
        try:
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
                try:
                    api_name = sd.query_hostapis(api_idx).get("name", "")
                except Exception:
                    pass

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
        except Exception:
            pass

    @staticmethod
    def _find_wasapi_device(name: str) -> int | None:
        """查找同名设备的 WASAPI 2ch 版本。"""
        try:
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
            t0 = time.time()
            try:
                self._play_immediate(data)
            except Exception as exc:
                logger.error(f"Audio playback error: {exc}")
            finally:
                # 通知外部该段真实播放耗时（覆盖段间开销/重采样延迟）
                if self.on_segment_finished is not None:
                    try:
                        self.on_segment_finished(time.time() - t0)
                    except Exception:
                        pass
                secure_clear(bytearray(data))

    def set_device(self, device_id: int | str | None) -> None:
        """Change the output device."""
        self.device_id = device_id
        self._query_device()

    def _play_immediate(self, pcm16_bytes: bytes) -> None:
        # int16 → float32
        samples = bytes_to_pcm16_array(pcm16_bytes).astype(np.float32) / 32768.0

        # 提升响度与人声清晰度（针对 VB-Cable 等虚拟声卡：信号电平最大化 +
        # 高频 presence 补偿 + 软限幅，避免对端降噪/AGC 误伤）
        samples = enhance_clarity(samples, _TTS_SR)

        # 重采样到设备实际采样率（避免 PortAudio 低质量重采样）
        if self._device_sr != _TTS_SR:
            samples = resample(samples, _TTS_SR, self._device_sr)

        # 单声道转立体声（匹配设备声道数，避免 VB-Cable 16ch 映射错误）
        if self._device_ch == 2 and samples.ndim == 1:
            samples = np.column_stack([samples, samples])

        device = self.device_id
        if isinstance(device, str):
            import os

            os.environ["PULSE_SINK"] = device
            device = None

        sd.play(
            samples,
            samplerate=self._device_sr,
            device=device,
            blocking=True,
        )
        sd.wait()
