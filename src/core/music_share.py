"""Play a local audio file to a chosen output device (e.g. CABLE Input / virtual mic)."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from src.utils.logger import logger

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".wma",
    ".aiff",
    ".aif",
    ".w64",
}
_SUPPORTED_NATIVE = {".wav", ".flac", ".ogg", ".aiff", ".aif", ".w64"}


def list_audio_files(folder: Path | str, *, recursive: bool = False) -> list[Path]:
    """Return sorted audio files in a folder (non-recursive by default)."""
    root = Path(folder)
    if not root.is_dir():
        return []
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [
        p
        for p in iterator
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def load_audio_file(path: Path | str) -> tuple[np.ndarray, int]:
    """Load audio as float32 shape (n, ch). Decodes mp3/m4a via ffmpeg when needed."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到音频文件: {path}")

    suffix = path.suffix.lower()
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        if data.size == 0:
            raise RuntimeError("空音频")
        return data, int(sr)
    except Exception as native_exc:
        if suffix in _SUPPORTED_NATIVE:
            raise RuntimeError(f"无法读取音频: {native_exc}") from native_exc
        return _ffmpeg_decode(path)


def _ffmpeg_decode(path: Path) -> tuple[np.ndarray, int]:
    """Decode via ffmpeg → temp wav (needs ffmpeg on PATH)."""
    tmp_path = Path(
        tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    )
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = err[-3:] if err else ["unknown ffmpeg error"]
            raise RuntimeError(
                "无法解码该格式。请安装 ffmpeg，或改用 WAV/FLAC/OGG。\n"
                + "\n".join(tail)
            )
        data, sr = sf.read(str(tmp_path), dtype="float32", always_2d=True)
        return data, int(sr)
    finally:
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)


class MusicSharePlayer:
    """Stream a loaded track to an output device with volume / loop / pause."""

    def __init__(
        self,
        *,
        on_finished: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> None:
        self.on_finished = on_finished
        self.on_error = on_error
        self.on_progress = on_progress

        self._data: np.ndarray | None = None  # (n, ch) float32
        self._sr = 44100
        self._pos = 0
        self._device: int | str | None = None
        self._volume = 0.7
        self._loop = False
        self._paused = False
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._path: Path | None = None

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and self._stream.active and not self._paused

    @property
    def is_loaded(self) -> bool:
        return self._data is not None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def duration_sec(self) -> float:
        if self._data is None:
            return 0.0
        return float(self._data.shape[0]) / float(self._sr)

    @property
    def position_sec(self) -> float:
        return float(self._pos) / float(self._sr) if self._sr else 0.0

    def load(self, path: Path | str) -> tuple[str, float]:
        """Load file; returns (display_name, duration_sec). Stops current playback."""
        self.stop()
        data, sr = load_audio_file(path)
        if data.shape[1] > 2:
            data = data[:, :2]
        self._data = data
        self._sr = sr
        self._pos = 0
        self._path = Path(path)
        return self._path.name, self.duration_sec

    def set_device(self, device_id: int | str | None) -> None:
        self._device = device_id

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)

    def play(self) -> None:
        if self._data is None:
            raise RuntimeError("请先选择音频文件")
        if self._stream is not None and self._stream.active:
            self._paused = False
            return

        channels = int(self._data.shape[1])
        device = self._device
        if isinstance(device, str):
            import os

            os.environ["PULSE_SINK"] = device
            device = None

        self._paused = False

        def callback(outdata, frames, _time, status) -> None:  # noqa: ANN001
            if status:
                logger.debug(f"music share stream status: {status}")
            with self._lock:
                if self._paused or self._data is None:
                    outdata.fill(0)
                    return
                end = self._pos + frames
                chunk = self._data[self._pos : end]
                n = chunk.shape[0]
                if n < frames:
                    outdata[:n] = chunk * self._volume
                    if self._loop and self._data.shape[0] > 0:
                        need = frames - n
                        wrap = self._data[:need]
                        outdata[n : n + wrap.shape[0]] = wrap * self._volume
                        if wrap.shape[0] < need:
                            outdata[n + wrap.shape[0] :].fill(0)
                        self._pos = wrap.shape[0]
                    else:
                        outdata[n:].fill(0)
                        self._pos = self._data.shape[0]
                        raise sd.CallbackStop
                else:
                    outdata[:] = chunk * self._volume
                    self._pos = end
                if self.on_progress:
                    with contextlib.suppress(Exception):
                        self.on_progress(self.position_sec, self.duration_sec)

        try:
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=channels,
                dtype="float32",
                device=device,
                callback=callback,
                finished_callback=self._on_stream_finished,
            )
            self._stream.start()
            logger.info(
                f"Music share playing: {self._path} → device={self._device!r} "
                f"sr={self._sr} ch={channels}"
            )
        except Exception as exc:
            self._stream = None
            raise RuntimeError(f"无法打开输出设备: {exc}") from exc

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if self._stream is not None and self._stream.active:
            self._paused = False
        else:
            self.play()

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self._paused = False
        self._pos = 0
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.abort()
            with contextlib.suppress(Exception):
                stream.close()

    def _on_stream_finished(self) -> None:
        self._stream = None
        self._paused = False
        finished = (
            self._data is not None
            and self._pos >= self._data.shape[0]
            and not self._loop
        )
        if finished and self.on_finished:
            with contextlib.suppress(Exception):
                self.on_finished()
