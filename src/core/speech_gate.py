"""Voice Activity Detection (VAD) gate for filtering audio chunks."""

from __future__ import annotations

import audioop
from dataclasses import dataclass

# 灵敏度档位对应的 RMS 阈值
_SENS_THRESHOLDS = {
    "loose": 0.004,
    "low": 0.006,
    "medium": 0.010,
    "high": 0.018,
    "strict": 0.030,
}


@dataclass
class VADStats:
    rms: float = 0.0
    open: bool = False


class SpeechGate:
    """基于 RMS 电平的简易 VAD 门控。

    当检测到人声（RMS 超过阈值）时开启 open_ms 毫秒，
    关闭后保持 hangover_ms 毫秒的拖尾，避免语音被截断。
    """

    def __init__(
        self,
        *,
        profile: str = "mic",
        sensitivity: str = "medium",
        sample_rate: int = 16000,
        open_ms: int = 80,
        hangover_ms: int = 600,
    ) -> None:
        self._profile = profile
        self._sample_rate = sample_rate
        self._threshold = _SENS_THRESHOLDS.get(sensitivity, _SENS_THRESHOLDS["medium"])
        # 每块大约 20ms（16000Hz * 20ms = 320 samples = 640 bytes @ 16bit mono）
        self._block_samples = int(sample_rate * 0.02)
        self._open_blocks = max(1, int(open_ms / 20))
        self._hangover_blocks = max(1, int(hangover_ms / 20))
        self._active_counter = 0
        self._hangover_counter = 0
        self._is_open = False
        self._last_rms = 0.0

    def accept(self, pcm: bytes) -> bool:
        """判断该 PCM 块是否应该送出。返回 True 表示放行。"""
        if not pcm:
            return False

        try:
            rms = audioop.rms(pcm, 2)
        except Exception:
            return True

        # 归一化到 0..1（16bit 最大值 32768）
        normalized = rms / 32768.0
        self._last_rms = normalized

        triggered = normalized >= self._threshold

        if triggered:
            self._active_counter = self._open_blocks
            self._hangover_counter = self._hangover_blocks
            self._is_open = True
        elif self._active_counter > 0:
            self._active_counter -= 1
        elif self._hangover_counter > 0:
            self._hangover_counter -= 1
        else:
            self._is_open = False

        return self._is_open

    def stats(self) -> VADStats:
        return VADStats(rms=self._last_rms, open=self._is_open)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def profile(self) -> str:
        return self._profile
