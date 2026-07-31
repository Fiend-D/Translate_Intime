"""Legacy soundcard-based WASAPI loopback compatibility capture.

``soundcard`` captures the complete endpoint mix and cannot exclude one process.
The old process-exclude identifier remains for configuration migration only and
is no longer advertised as an available backend.
"""

from __future__ import annotations

import platform
import threading
import time
from collections import deque
from typing import Any

import numpy as np

from src.utils.logger import logger

IS_WINDOWS = platform.system() == "Windows"

# 设备 ID 前缀，stream.py / device_guard.py 以此识别
WASAPI_PROC_EXCLUDE_PREFIX = "wasapi_proc_exclude:"
PROC_EXCLUDE_DEVICE_ID = WASAPI_PROC_EXCLUDE_PREFIX


def is_process_exclude_available() -> bool:
    """Return False until a real per-process WASAPI backend is implemented."""
    return False


class ProcessExcludeLoopback:
    """Legacy class that captures the full default endpoint mix.

    It does not exclude this process. New selection paths use the explicitly
    named classic loopback backend instead.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        buffer_seconds: float = 2.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._buffer_seconds = buffer_seconds
        self._buffer: deque[np.ndarray] = deque()
        self._buffer_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("ProcessExcludeLoopback 已启动")

    def _find_loopback_mic(self) -> Any | None:
        """查找默认扬声器的 loopback 捕获设备。"""
        import soundcard as sc

        default_speaker = sc.default_speaker()
        speaker_name = default_speaker.name
        logger.info(f"默认扬声器: {speaker_name!r}")

        all_mics = sc.all_microphones(include_loopback=True)

        # 优先匹配默认扬声器名称的 loopback 设备
        for mic in all_mics:
            if getattr(mic, "isloopback", False) and mic.name == speaker_name:
                logger.info(f"找到匹配的 loopback 设备: {mic.name!r}")
                return mic

        # 回退：第一个 loopback 设备
        for mic in all_mics:
            if getattr(mic, "isloopback", False):
                logger.info(f"使用第一个 loopback 设备: {mic.name!r}")
                return mic

        return None

    def _capture_loop(self) -> None:
        """捕获循环：使用 soundcard 捕获 loopback 音频。"""
        try:
            import warnings

            loopback_mic = self._find_loopback_mic()
            if loopback_mic is None:
                raise RuntimeError("未找到任何 WASAPI loopback 设备")

            source_rate = 48000
            chunk_frames = int(source_rate * 0.02)  # 20ms

            with loopback_mic.recorder(samplerate=source_rate) as rec:
                logger.info(
                    f"Loopback 录音已打开: {loopback_mic.name!r}, "
                    f"source_rate={source_rate}, target_rate={self._sample_rate}"
                )
                while self._running:
                    try:
                        with warnings.catch_warnings():
                            warnings.filterwarnings(
                                "ignore",
                                message="data discontinuity in recording",
                            )
                            data = rec.record(numframes=chunk_frames)
                        if data is None or data.size == 0:
                            time.sleep(0.005)
                            continue

                        # 多声道转单声道
                        mono = data.mean(axis=1) if data.ndim > 1 else data

                        # 重采样到目标采样率
                        if source_rate != self._sample_rate:
                            mono = self._resample(mono, source_rate, self._sample_rate)

                        mono = mono.astype(np.float32)

                        with self._buffer_lock:
                            self._buffer.append(mono)
                            # 限制缓冲区大小（50 chunks/sec at 20ms each）
                            max_chunks = int(self._buffer_seconds * 50)
                            while len(self._buffer) > max_chunks:
                                self._buffer.popleft()

                    except Exception as exc:
                        if self._running:
                            logger.warning(f"WASAPI loopback 捕获异常: {exc}")
                        time.sleep(0.02)

        except Exception as exc:
            logger.error(f"WASAPI loopback 捕获循环失败: {exc}")
            self._running = False

    def _resample(self, data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Resample with anti-alias filtering."""
        from src.utils.audio_utils import resample

        return resample(data.astype(np.float32), src_rate, dst_rate)

    def read_float32(self, chunk_size: int) -> np.ndarray | None:
        """读取一个音频块。缓冲区为空时返回 None（不返回零数组）。"""
        if not self._running:
            return None

        with self._buffer_lock:
            # Do not consume a partial block and pad it with silence. The capture
            # thread produces 20 ms chunks while the pipeline normally requests
            # 40 ms; waiting for both preserves continuous audio and real timing.
            if sum(len(chunk) for chunk in self._buffer) < chunk_size:
                return None

            collected: list[np.ndarray] = []
            remaining = chunk_size
            while remaining > 0 and self._buffer:
                chunk = self._buffer.popleft()
                if len(chunk) <= remaining:
                    collected.append(chunk)
                    remaining -= len(chunk)
                else:
                    collected.append(chunk[:remaining])
                    self._buffer.appendleft(chunk[remaining:])
                    remaining = 0

        result = np.concatenate(collected)
        return result[:chunk_size]

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._buffer_lock:
            self._buffer.clear()
        logger.info("ProcessExcludeLoopback 已停止")
