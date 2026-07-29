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
    return pcm.tobytes()


def resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample a 1-D float32 audio array using high-quality polyphase filtering."""
    if orig_sr == target_sr:
        return audio
    from math import gcd

    g = gcd(orig_sr, target_sr)
    up = target_sr // g
    down = orig_sr // g
    from scipy.signal import resample_poly

    return resample_poly(audio, up, down).astype(np.float32)


def enhance_clarity(
    audio: np.ndarray,
    sr: int,
    *,
    target_peak_db: float = -3.0,
    max_boost_db: float = 12.0,
    presence_db: float = 2.0,
    presence_hz: float = 3000.0,
    noise_floor_db: float = -30.0,
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
            sos = butter(
                2, cutoff / nyq, btype="high", output="sos"
            )
            high = sosfiltfilt(sos, out.astype(np.float64)).astype(np.float32)
            additive_gain = 10.0 ** (presence_db / 20.0) - 1.0
            out = out + high * additive_gain
        except Exception:
            pass

    # --- Step 4: Soft limit ---
    out = np.tanh(out * 1.15) / 1.15

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
