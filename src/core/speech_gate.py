"""Voice Activity Detection (VAD) gate for filtering audio chunks."""

from __future__ import annotations

import audioop
from dataclasses import dataclass

# 灵敏度档位对应的 RMS 阈值（归一化 0..1，对应 16bit RMS / 32768）
# 数值越小越灵敏（更容易判定为语音）
_SENS_THRESHOLDS = {
    "very_loose": 0.0015,  # -66 dBFS：几乎最灵敏，适用于安静环境 / 低音量麦克风
    "loose": 0.0025,       # -60 dBFS：宽松
    "low": 0.005,          # -56 dBFS
    "medium": 0.008,       # -58 dBFS
    "high": 0.014,         # -57 dBFS
    "strict": 0.025,       # -32 dBFS
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
        """判断该 PCM 块是否应该送出。返回 True 表示放行。

        输入可以是任意大小的 PCM16 mono 字节流；内部会按 20ms block 处理。
        只要有任意一个 block 被判定为语音，就返回 True。
        """
        if not pcm:
            return False

        # 如果输入块大小正好等于内部 block，直接处理
        # 否则按 block 切分处理（取 OR：有一个 block 为语音就放行）
        block_bytes = self._block_samples * 2  # 2 bytes per sample (int16)
        if len(pcm) == block_bytes:
            return self._accept_block(pcm)

        # 多 block：任何一个 block 通过就算通过
        any_pass = False
        for offset in range(0, len(pcm), block_bytes):
            chunk = pcm[offset : offset + block_bytes]
            if len(chunk) < block_bytes:
                # 尾部不足一个 block，用零填充
                chunk = chunk + b"\x00" * (block_bytes - len(chunk))
            if self._accept_block(chunk):
                any_pass = True
            # 如果有任何一个 block 被放行，保持 open 状态直到后续 block 判断
        return any_pass

    def _accept_block(self, pcm: bytes) -> bool:
        """处理单个 20ms PCM block。"""
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
