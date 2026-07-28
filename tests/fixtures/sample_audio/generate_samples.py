"""Generate simple synthetic WAV fixtures for integration tests."""

import wave
from pathlib import Path

import numpy as np


def write_sine_wave(
    path: Path, frequency: float, duration: float, sample_rate: int = 16000
) -> None:
    """Write a mono PCM16 sine wave to a WAV file."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    samples = (np.sin(2 * np.pi * frequency * t) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


if __name__ == "__main__":
    out_dir = Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    write_sine_wave(out_dir / "hello_zh.wav", 440.0, 3.0)
    write_sine_wave(out_dir / "hello_en.wav", 523.25, 3.0)
    print(f"Generated fixtures in {out_dir}")
