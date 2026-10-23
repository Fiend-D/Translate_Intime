"""Audio playback and output routing using sounddevice."""

import contextlib
import threading
import time
from collections.abc import Callable
from queue import Empty, Queue
from typing import Any

import numpy as np

from src.audio.pulse_env import temporary_pulse_sink
from src.utils.audio_utils import (
    bytes_to_pcm16_array,
    enhance_clarity,
    resample,
    secure_clear,
)
from src.utils.logger import logger

_TTS_SR = 16000
_TTS_BYTES_PER_SEC = _TTS_SR * 2
# Interrupt granularity: clear_queue() takes effect between two writes.
_WRITE_CHUNK_MS = 60
# Backlog cap by audio duration; dropping finished translations is a last resort.
_MAX_QUEUED_SECONDS = 30.0
_VIRTUAL_DEVICE_MARKERS = ("cable", "voicemeeter", "vb-audio", "virtual")


def _sounddevice() -> Any:
    """Import PortAudio bindings only when playback/device access is requested."""
    import sounddevice

    return sounddevice


def _stream_latency(stream: Any) -> float:
    """Output latency in seconds as reported by PortAudio (0 when unknown)."""
    latency = getattr(stream, "latency", 0.0)
    if isinstance(latency, tuple | list):
        latency = latency[-1] if latency else 0.0
    try:
        return max(0.0, float(latency))
    except (TypeError, ValueError):
        return 0.0


def is_virtual_device_name(name: str) -> bool:
    """True for virtual cables (VB-Cable / Voicemeeter) that have no acoustic path."""
    lowered = (name or "").lower()
    return bool(lowered) and any(m in lowered for m in _VIRTUAL_DEVICE_MARKERS)


class AudioPlayer:
    """Plays PCM16 audio bytes to a selected output device."""

    def __init__(self, device_id: int | str | None = None) -> None:
        self.device_id = device_id
        self._queue: Queue[tuple[int, bytes]] = Queue()
        self._queued_bytes = 0
        self._generation = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._stream: Any | None = None
        self._stream_key: tuple[object, int, int] | None = None
        self._stream_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_segment = False
        self._device_sr = _TTS_SR
        self._device_ch = 2
        self._device_name: str = ""
        self._query_device()
        # 回调：每次一段播放结束后调用，传入该段真实播放时长（秒）
        # 用途：外部可以据此延长麦克风回灌静音窗口
        self.on_segment_finished: Callable[[float], None] | None = None
        # 回声消除参考：(16 kHz 单声道样本, 该块播完的 monotonic 时间)
        self.on_reference: Callable[[np.ndarray, float, bool], None] | None = None

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
                    logger.info(f"设备升级: [{dev}] {api_name} {ch}ch → [{wasapi_dev}] WASAPI 2ch")
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
        # A play() that raced with stop() must not linger: is_playing would stay
        # True (keeping the mic muted) and the stale speech would play on restart.
        self.clear_queue()
        self._close_stream()
        logger.info("Audio player stopped")

    def clear_queue(self) -> None:
        """Drop pending playback and stop the current segment within one write chunk."""
        with self._state_lock:
            self._generation += 1
            while True:
                try:
                    _, data = self._queue.get_nowait()
                except Empty:
                    break
                secure_clear(bytearray(data))
            self._queued_bytes = 0
        self._close_stream()

    @property
    def is_playing(self) -> bool:
        """True while a segment is queued or being written to the device.

        PortAudio streams stay active while idle so they can be reused. Stream
        activity therefore cannot represent audible playback.
        """
        if not self._queue.empty():
            return True
        with self._state_lock:
            return self._active_segment

    @property
    def queue_size(self) -> int:
        """Number of pending PCM segments in the queue."""
        return self._queue.qsize()

    @property
    def queued_seconds(self) -> float:
        """Duration of audio waiting in the queue (excludes the segment being played)."""
        with self._state_lock:
            return self._queued_bytes / _TTS_BYTES_PER_SEC

    @property
    def is_virtual_output(self) -> bool:
        return is_virtual_device_name(self._device_name)

    def play(self, pcm16_bytes: bytes) -> None:
        """Enqueue PCM16 mono audio; only drops the oldest audio past the duration cap."""
        if not pcm16_bytes or not self._running:
            return
        cap_bytes = int(_MAX_QUEUED_SECONDS * _TTS_BYTES_PER_SEC)
        dropped = 0
        with self._state_lock:
            generation = self._generation
            while self._queued_bytes + len(pcm16_bytes) > cap_bytes:
                try:
                    _, stale = self._queue.get_nowait()
                except Empty:
                    break
                self._queued_bytes -= len(stale)
                dropped += 1
                secure_clear(bytearray(stale))
            self._queued_bytes += len(pcm16_bytes)
            self._queue.put_nowait((generation, pcm16_bytes))
        if dropped:
            logger.warning(
                f"TTS playback backlog over {_MAX_QUEUED_SECONDS:.0f}s; dropped {dropped} segment(s)"
            )

    def _playback_loop(self) -> None:
        while self._running:
            # With echo cancellation listening, keep the device fed between segments:
            # a drained buffer changes the output latency, and every new segment would
            # make the canceller re-align (muting the capture) before it can cancel.
            keep_alive = self.on_reference is not None and self._stream is not None
            try:
                if keep_alive:
                    generation, data = self._queue.get_nowait()
                else:
                    generation, data = self._queue.get(timeout=0.1)
            except Empty:
                if keep_alive:
                    self._write_silence()
                continue
            with self._state_lock:
                self._queued_bytes = max(0, self._queued_bytes - len(data))
                if generation != self._generation:
                    secure_clear(bytearray(data))
                    continue
                self._active_segment = True
            t0 = time.time()
            completed = False
            try:
                completed = self._play_immediate(data, generation)
            except Exception as exc:
                logger.error(f"Audio playback error: {exc}")
            finally:
                with self._state_lock:
                    self._active_segment = False
                # 通知外部该段真实播放耗时（覆盖段间开销/重采样延迟）。
                # 被 clear_queue 打断的段不回调，否则会重新延长已解除的静音窗口。
                if completed and self.on_segment_finished is not None:
                    with contextlib.suppress(Exception):
                        self.on_segment_finished(time.time() - t0)
                secure_clear(bytearray(data))

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

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
        return is_virtual_device_name(self._device_name)

    def _play_immediate(self, pcm16_bytes: bytes, generation: int | None = None) -> bool:
        """Write one segment in small chunks; return False if interrupted."""
        # int16 → float32
        samples = bytes_to_pcm16_array(pcm16_bytes).astype(np.float32) / 32768.0

        # 仅对虚拟声卡 (VB-Cable / Voicemeeter) 应用 enhance_clarity:
        # 这类设备的对端 (会议软件) 会做降噪/AGC, 需要最大化电平 + 高频补偿.
        # 直接输出到物理音响/耳机时跳过, 保留 Kokoro TTS 的自然音质.
        if self._is_virtual_device():
            samples = enhance_clarity(samples, _TTS_SR)
        reference = samples

        # 重采样到设备实际采样率（避免 PortAudio 低质量重采样）
        if self._device_sr != _TTS_SR:
            samples = resample(samples, _TTS_SR, self._device_sr)

        # 单声道转立体声（匹配设备声道数，避免 VB-Cable 16ch 映射错误）
        if self._device_ch == 2 and samples.ndim == 1:
            samples = np.column_stack([samples, samples])
        elif self._device_ch == 1 and samples.ndim == 1:
            samples = samples.reshape(-1, 1)

        frames = np.ascontiguousarray(samples, dtype=np.float32)
        chunk = max(1, int(self._device_sr * _WRITE_CHUNK_MS / 1000))
        for start in range(0, len(frames), chunk):
            if not self._running or (generation is not None and not self._is_current(generation)):
                return False
            end = min(start + chunk, len(frames))
            with self._stream_lock:
                stream = self._ensure_stream_locked()
                underflowed = bool(stream.write(frames[start:end]))
                latency = _stream_latency(stream)
            on_reference = self.on_reference
            if on_reference is not None:
                r0 = start * _TTS_SR // self._device_sr
                r1 = end * _TTS_SR // self._device_sr
                with contextlib.suppress(Exception):
                    # An underflow inserted device silence: the chunk no longer follows
                    # the previous one, so the reference must re-anchor on its timestamp.
                    on_reference(reference[r0:r1], time.monotonic() + latency, underflowed)
        return True

    def _write_silence(self) -> None:
        """Write one chunk of silence (blocks at the device rate) and report it as reference."""
        frames = np.zeros(
            (max(1, int(self._device_sr * _WRITE_CHUNK_MS / 1000)), max(1, self._device_ch)),
            dtype=np.float32,
        )
        with self._stream_lock:
            stream = self._stream
            if stream is None:
                return
            try:
                underflowed = bool(stream.write(frames))
                latency = _stream_latency(stream)
            except Exception as exc:
                logger.debug(f"Idle playback write failed: {exc}")
                self._close_stream_locked()
                return
        on_reference = self.on_reference
        if on_reference is not None:
            with contextlib.suppress(Exception):
                silence = np.zeros(_TTS_SR * _WRITE_CHUNK_MS // 1000, dtype=np.float32)
                on_reference(silence, time.monotonic() + latency, underflowed)

    def _resolve_device(self) -> int | None:
        device = self.device_id
        return None if isinstance(device, str) else device

    def _ensure_stream_locked(self) -> Any:
        sd = _sounddevice()
        device = self._resolve_device()
        key = (device, self._device_sr, self._device_ch)
        if self._stream is not None and self._stream_key == key and self._stream.active:
            return self._stream
        self._close_stream_locked()
        pulse_sink = self.device_id if isinstance(self.device_id, str) else None
        with temporary_pulse_sink(pulse_sink):
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
