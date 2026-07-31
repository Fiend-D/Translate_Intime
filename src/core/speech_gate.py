"""Voice Activity Detection (VAD) gate for filtering audio chunks."""

from __future__ import annotations

import audioop
from dataclasses import dataclass

import numpy as np

from src.core.audio_pre_roll import AudioPreRoll
from src.core.silero_vad import SileroVadEngine
from src.utils.logger import logger

# 灵敏度档位对应的 RMS 阈值（归一化 0..1，对应 16bit RMS / 32768）
# 数值越小越灵敏（更容易判定为语音）
_SENS_THRESHOLDS = {
    "very_loose": 0.0015,  # -66 dBFS：几乎最灵敏，适用于安静环境 / 低音量麦克风
    "loose": 0.0025,  # -60 dBFS：宽松
    "low": 0.005,  # -56 dBFS
    "medium": 0.008,  # -58 dBFS
    "high": 0.014,  # -57 dBFS
    "strict": 0.025,  # -32 dBFS
}

_SILERO_THRESHOLDS = {
    "very_loose": 0.25,
    "loose": 0.30,
    "low": 0.35,
    "medium": 0.50,
    "high": 0.70,
    "strict": 0.82,
}


@dataclass
class VADStats:
    rms: float = 0.0
    open: bool = False
    backend: str = "rms"
    speech_prob: float = 0.0


@dataclass
class GateResult:
    passed: bool
    opened_now: bool = False
    preroll: bytes = b""

    @property
    def pass_(self) -> bool:
        return self.passed

    def __getattr__(self, name: str) -> bool:
        if name == "pass":
            return self.passed
        raise AttributeError(name)


class SpeechGate:
    """VAD 门控：优先 Silero ONNX，可回退到 RMS 电平检测。"""

    def __init__(
        self,
        *,
        profile: str = "mic",
        sensitivity: str = "medium",
        sample_rate: int = 16000,
        open_ms: int = 80,
        hangover_ms: int = 600,
        backend: str = "auto",
        preroll_ms: int = 300,
        silero_engine: SileroVadEngine | None = None,
    ) -> None:
        self._profile = profile
        self._sample_rate = sample_rate
        self._sensitivity = sensitivity
        self._backend_requested = (backend or "auto").lower()
        self._backend = "rms"
        self._open_ms = open_ms
        self._hangover_ms = hangover_ms
        self._threshold = _SENS_THRESHOLDS.get(sensitivity, _SENS_THRESHOLDS["medium"])
        self._silero_threshold = _SILERO_THRESHOLDS.get(sensitivity, _SILERO_THRESHOLDS["medium"])
        self._silero = silero_engine
        self._silero_warned = False
        self._silero_buffer = bytearray()
        self._last_prob = 0.0

        self._block_samples = int(sample_rate * 0.02)
        self._block_ms = 20
        self._open_blocks = max(1, int(self._open_ms / self._block_ms))
        self._hangover_blocks = max(1, int(self._hangover_ms / self._block_ms))
        self._active_counter = 0
        self._hangover_counter = 0
        self._is_open = False
        self._last_rms = 0.0
        self._pre_roll = AudioPreRoll(sample_rate=sample_rate, preroll_ms=preroll_ms)
        self._select_backend()

    def process(self, pcm: bytes) -> GateResult:
        """Process PCM16 mono bytes and return gate edge/pre-roll information."""
        if not pcm:
            return GateResult(False)
        self._maybe_promote_silero()
        was_open = self._is_open
        passed = self._process_silero(pcm) if self._backend == "silero" else self._process_rms(pcm)
        opened_now = self._is_open and not was_open
        preroll = self._pre_roll.drain() if opened_now else b""
        self._pre_roll.push(pcm)
        return GateResult(passed=passed, opened_now=opened_now, preroll=preroll)

    def accept(self, pcm: bytes) -> bool:
        """Backward-compatible API: True means this chunk should pass."""
        return self.process(pcm).passed

    def take_preroll(self) -> bytes:
        return self._pre_roll.drain()

    def clear_preroll(self) -> None:
        self._pre_roll.clear()

    def _select_backend(self) -> None:
        requested = self._backend_requested
        if requested == "rms":
            self._backend = "rms"
            return
        if self._silero is None:
            self._silero = SileroVadEngine(sample_rate=self._sample_rate)
        self._silero.start_loading()
        self._maybe_promote_silero()
        if self._backend == "silero":
            return
        self._backend = "rms"
        if requested in {"auto", "silero"} and not self._silero_warned:
            logger.info("Silero VAD 后台加载中，暂用 RMS SpeechGate")
            self._silero_warned = True

    def _maybe_promote_silero(self) -> None:
        if self._backend == "silero" or self._backend_requested == "rms":
            return
        if self._silero is None or not self._silero.is_available():
            return
        self._backend = "silero"
        self._block_samples = 512
        self._block_ms = int(round(512 * 1000 / self._sample_rate))
        self._open_blocks = max(1, int(self._open_ms / self._block_ms))
        self._hangover_blocks = max(1, int(self._hangover_ms / self._block_ms))
        self._silero_buffer.clear()
        logger.info("Silero VAD 已就绪，切换到 ONNX 后端")

    def _process_rms(self, pcm: bytes) -> bool:
        block_bytes = self._block_samples * 2
        if len(pcm) == block_bytes:
            return self._accept_rms_block(pcm)
        any_pass = False
        for offset in range(0, len(pcm), block_bytes):
            chunk = pcm[offset : offset + block_bytes]
            if len(chunk) < block_bytes:
                chunk = chunk + b"\x00" * (block_bytes - len(chunk))
            if self._accept_rms_block(chunk):
                any_pass = True
        return any_pass

    def _accept_rms_block(self, pcm: bytes) -> bool:
        try:
            rms = audioop.rms(pcm, 2)
        except Exception:
            return True
        normalized = rms / 32768.0
        self._last_rms = normalized
        self._update_gate(normalized >= self._threshold)
        return self._is_open

    def _process_silero(self, pcm: bytes) -> bool:
        self._update_rms_stat(pcm)
        self._silero_buffer.extend(pcm)
        block_bytes = 512 * 2
        any_pass = self._is_open
        while len(self._silero_buffer) >= block_bytes:
            raw = bytes(self._silero_buffer[:block_bytes])
            del self._silero_buffer[:block_bytes]
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            try:
                assert self._silero is not None
                self._last_prob = self._silero.prob(samples)
                self._update_gate(self._last_prob >= self._silero_threshold)
                any_pass = any_pass or self._is_open
            except Exception as exc:
                logger.warning(f"Silero VAD 推理失败，回退 RMS VAD: {exc}")
                self._backend = "rms"
                self._silero_buffer.clear()
                return self._process_rms(pcm)
        return any_pass or self._is_open

    def _update_rms_stat(self, pcm: bytes) -> None:
        try:
            self._last_rms = audioop.rms(pcm, 2) / 32768.0
        except Exception:
            self._last_rms = 0.0

    def _update_gate(self, triggered: bool) -> None:
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

    def stats(self) -> VADStats:
        return VADStats(
            rms=self._last_rms,
            open=self._is_open,
            backend=self._backend,
            speech_prob=self._last_prob,
        )

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def profile(self) -> str:
        return self._profile
