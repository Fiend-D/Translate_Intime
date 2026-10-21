"""Audio format conversion, resampling, and secure buffer utilities."""

import array
from pathlib import Path

import numpy as np
import soundfile as sf


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Convert 16-bit signed PCM bytes to normalized float32 array."""
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert normalized float32 array to 16-bit signed PCM bytes."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    return bytes(pcm.tobytes())


def resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample a 1-D float32 audio array using high-quality polyphase filtering."""
    if orig_sr == target_sr:
        return audio
    from math import gcd

    g = gcd(orig_sr, target_sr)
    up = target_sr // g
    down = orig_sr // g
    from scipy.signal import resample_poly

    return np.asarray(resample_poly(audio, up, down), dtype=np.float32)


class StreamResampler:
    """Polyphase resampler that carries filter history across chunks.

    Resampling each capture chunk on its own restarts the anti-alias filter at
    every boundary (a click every ~100 ms). Feeding chunks here yields exactly
    the samples a single pass over the whole stream would, at the cost of a
    causal delay of about half the filter length (well under 1 ms).
    """

    def __init__(self, orig_sr: int, target_sr: int) -> None:
        from math import gcd

        from scipy.signal import firwin

        g = gcd(orig_sr, target_sr)
        self._up = target_sr // g
        self._down = orig_sr // g
        self.passthrough = self._up == self._down
        max_rate = max(self._up, self._down)
        taps = 2 * 10 * max_rate + 1
        self._h = firwin(taps, 1.0 / max_rate, window=("kaiser", 5.0)) * self._up
        # Input samples a full filter window spans, rounded up to the decimation grid.
        self._keep = (taps // self._up + 2) // self._down * self._down + self._down
        self._history = np.zeros(0, dtype=np.float32)
        self._hist_start = 0  # global index of _history[0]; always a multiple of _down
        self._n_in = 0
        self._n_out = 0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if self.passthrough:
            return np.asarray(chunk, dtype=np.float32)
        from scipy.signal import upfirdn

        data = np.concatenate([self._history, np.asarray(chunk, dtype=np.float32)])
        self._n_in += len(chunk)
        last_out = ((self._n_in - 1) * self._up) // self._down
        first_rel = self._n_out - (self._hist_start * self._up) // self._down
        out = np.zeros(0, dtype=np.float32)
        if last_out >= self._n_out:
            full = upfirdn(self._h, data, self._up, self._down)
            out = np.asarray(full[first_rel : first_rel + last_out - self._n_out + 1], np.float32)
            self._n_out = last_out + 1
        cut = max(0, self._n_in - self._keep)
        cut -= cut % self._down
        if cut > self._hist_start:
            data = data[cut - self._hist_start :]
            self._hist_start = cut
        self._history = data
        return out


def enhance_clarity(
    audio: np.ndarray,
    sr: int,
    *,
    target_peak_db: float = -3.0,
    max_boost_db: float = 6.0,
    presence_db: float = 1.0,
    presence_hz: float = 3000.0,
    noise_floor_db: float = -45.0,
) -> np.ndarray:
    """Boost loudness and vocal presence for virtual-cable / small-speaker playback.

    Pipeline:
      1. **Noise gate** — silence below ``noise_floor_db`` dBFS to prevent hiss.
      2. **Peak normalization with gain cap** — normalize to ``target_peak_db``
         but never boost more than ``max_boost_db`` dB.  This prevents blowing up
         the noise floor on quiet TTS segments.
      3. **Zero-phase high-shelf boost** — lift ``presence_hz`` and above by
         ``presence_db`` using forward-backward filtering (no phase shift, no
         ringing).
      4. **Soft-limit** (tanh) to keep within [-1, 1] without hard clipping.

    Operates on a 1-D float32 array. Returns float32.

    Note: 参数已调温和 (2026-07), 避免破坏 Kokoro TTS 的自然音质.
    仅在输出到虚拟声卡 (VB-Cable/Voicemeeter) 时由调用方启用.
    """
    if audio.size == 0 or audio.ndim != 1:
        return audio

    out = audio.astype(np.float32, copy=True)

    # --- Step 1: Noise gate ---
    # 静音低于噪声门限的部分，避免峰值归一化时把噪声底拉起来
    gate_threshold = 10.0 ** (noise_floor_db / 20.0)
    gate_mask = np.abs(out) < gate_threshold
    out[gate_mask] = 0.0

    # --- Step 2: Peak normalization (gain-capped) ---
    peak = float(np.max(np.abs(out)))
    if peak > gate_threshold:
        target_linear = 10.0 ** (target_peak_db / 20.0)
        gain = target_linear / peak
        # 限制最大提升量（dB），防止静音/尾音段被过度放大
        max_gain_linear = 10.0 ** (max_boost_db / 20.0)
        if gain > max_gain_linear:
            gain = max_gain_linear
        out *= gain

    # --- Step 3: Zero-phase high-shelf presence boost ---
    nyq = sr / 2.0
    cutoff = min(presence_hz, nyq * 0.85)
    if cutoff < nyq and presence_db > 0:
        try:
            from scipy.signal import butter, sosfiltfilt

            # 使用零相位滤波 (filtfilt)：前向+后向各一次，无相位偏移，无振铃
            sos = butter(2, cutoff / nyq, btype="high", output="sos")
            high = sosfiltfilt(sos, out.astype(np.float64)).astype(np.float32)
            additive_gain = 10.0 ** (presence_db / 20.0) - 1.0
            out = out + high * additive_gain
        except Exception:
            pass

    # --- Step 4: Soft limit ---
    out = np.asarray(np.tanh(out * 1.05) / 1.05, dtype=np.float32)

    return out.astype(np.float32)


def secure_clear(buffer: bytearray | bytes | np.ndarray) -> None:
    """Zero out an audio buffer to prevent sensitive data lingering in memory."""
    if isinstance(buffer, bytearray):
        for i in range(len(buffer)):
            buffer[i] = 0
    elif isinstance(buffer, bytes):
        # bytes are immutable; best effort via array
        arr = array.array("b", buffer)
        for i in range(len(arr)):
            arr[i] = 0
    elif isinstance(buffer, np.ndarray):
        buffer.fill(0.0)


def load_audio_file(path: str | Path, target_sr: int = 16000) -> np.ndarray:
    """Load an audio file and resample to target sample rate as mono float32."""
    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    return resample(data, sr, target_sr)


def bytes_to_pcm16_array(data: bytes) -> np.ndarray:
    """Convert raw PCM16 bytes to a NumPy int16 array."""
    return np.frombuffer(data, dtype=np.int16)
