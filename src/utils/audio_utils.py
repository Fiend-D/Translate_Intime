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
    target_peak_db: float = -1.0,
    presence_db: float = 3.0,
    presence_hz: float = 3000.0,
) -> np.ndarray:
    """Boost loudness and vocal presence for virtual-cable / small-speaker playback.

    Pipeline:
      1. Peak normalize to ``target_peak_db`` dBFS (maximizes signal level so the
         receiver's noise gate does not chop quiet speech).
      2. High-shelf boost above ``presence_hz`` by ``presence_db`` (restores vocal
         consonants that 16 kHz TTS audio and receiver high-pass filters attenuate).
      3. Soft-limit (tanh) to prevent clipping from the above processing.

    Operates on a 1-D float32 array. Returns float32.
    """
    if audio.size == 0 or audio.ndim != 1:
        return audio

    out = audio.astype(np.float32, copy=True)

    # 1. Peak normalization
    peak = float(np.max(np.abs(out)))
    if peak > 1e-6:
        target = 10.0 ** (target_peak_db / 20.0)
        out *= target / peak

    # 2. High-shelf presence boost (high-pass + additive blend)
    nyq = sr / 2.0
    cutoff = min(presence_hz, nyq * 0.9)
    if cutoff < nyq:
        try:
            from scipy.signal import butter, sosfilt

            sos = butter(1, cutoff / nyq, btype="high", output="sos")
            high = sosfilt(sos, out)
            additive_gain = 10.0 ** (presence_db / 20.0) - 1.0
            out = out + high * additive_gain
        except Exception:
            pass

    # 3. Soft limit to keep within [-1, 1] without hard clipping
    out = np.tanh(out * 1.2) / 1.2

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
