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
    """Resample a 1-D float32 audio array using simple linear interpolation."""
    if orig_sr == target_sr:
        return audio
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(indices, np.arange(len(audio)), audio)


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
