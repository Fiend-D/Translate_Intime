"""Play a local audio file to a chosen output device (e.g. CABLE Input / virtual mic)."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

from src.audio.pulse_env import temporary_pulse_sink
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
_MAX_OUTPUT_PEAK = 0.999
_PROGRESS_INTERVAL_SEC = 0.1
_FFMPEG_TIMEOUT_SEC = 120.0


class MusicLoadCancelledError(RuntimeError):
    """Raised when a superseded music load is cancelled."""


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise MusicLoadCancelledError("music load cancelled")


@dataclass(frozen=True)
class PreparedMusicTrack:
    path: Path
    source_data: np.ndarray
    source_rate: int
    playback_data: np.ndarray
    output_rate: int
    output_channels: int
    device: int | str | None


def list_audio_files(folder: Path | str, *, recursive: bool = False) -> list[Path]:
    """Return sorted audio files in a folder (non-recursive by default)."""
    root = Path(folder)
    if not root.is_dir():
        return []
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
    return sorted(files, key=lambda p: p.name.lower())


def load_audio_file(
    path: Path | str,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[np.ndarray, int]:
    """Load audio as float32 shape (n, ch). Decodes mp3/m4a via ffmpeg when needed."""
    path = Path(path)
    _check_cancelled(cancel_event)
    if not path.is_file():
        raise FileNotFoundError(f"找不到音频文件: {path}")

    suffix = path.suffix.lower()
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        _check_cancelled(cancel_event)
        if data.size == 0:
            raise RuntimeError("空音频")
        data = np.nan_to_num(data, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        return np.ascontiguousarray(data, dtype=np.float32), int(sr)
    except Exception as native_exc:
        if suffix in _SUPPORTED_NATIVE:
            raise RuntimeError(f"无法读取音频: {native_exc}") from native_exc
        return _ffmpeg_decode(path, cancel_event=cancel_event)


def _ffmpeg_decode(
    path: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[np.ndarray, int]:
    """Decode via ffmpeg to lossless float WAV without changing rate/channels."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-acodec",
            "pcm_f32le",
            str(tmp_path),
        ]
        if cancel_event is None:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=_FFMPEG_TIMEOUT_SEC,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + _FFMPEG_TIMEOUT_SEC
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event.is_set():
                        proc.terminate()
                        with contextlib.suppress(Exception):
                            proc.communicate(timeout=2.0)
                        raise MusicLoadCancelledError("music load cancelled") from None
                    if time.monotonic() >= deadline:
                        proc.kill()
                        with contextlib.suppress(Exception):
                            proc.communicate(timeout=2.0)
                        raise RuntimeError("ffmpeg decode timed out") from None
            returncode = proc.returncode
        if returncode != 0:
            err = (stderr or stdout or "").strip().splitlines()
            tail = err[-3:] if err else ["unknown ffmpeg error"]
            raise RuntimeError(
                "无法解码该格式。请安装 ffmpeg，或改用 WAV/FLAC/OGG。\n" + "\n".join(tail)
            )
        data, sr = sf.read(str(tmp_path), dtype="float32", always_2d=True)
        _check_cancelled(cancel_event)
        data = np.nan_to_num(data, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        return np.ascontiguousarray(data, dtype=np.float32), int(sr)
    finally:
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)


def _prepare_playback_data(
    data: np.ndarray,
    source_sr: int,
    target_sr: int,
    target_channels: int,
    cancel_event: threading.Event | None = None,
) -> np.ndarray:
    """Match the output format while preserving stereo and preventing clipping."""
    if source_sr <= 0 or target_sr <= 0:
        raise ValueError("sample rate must be positive")
    if target_channels not in (1, 2):
        raise ValueError("music output supports one or two channels")
    _check_cancelled(cancel_event)

    play_data = np.asarray(data, dtype=np.float32)
    if play_data.ndim != 2 or play_data.shape[1] < 1:
        raise ValueError("audio data must have shape (frames, channels)")

    if target_sr != source_sr:
        from src.utils.audio_utils import resample

        channels = []
        for ch in range(play_data.shape[1]):
            _check_cancelled(cancel_event)
            channels.append(resample(play_data[:, ch], source_sr, target_sr))
        play_data = np.column_stack(channels)

    current_channels = int(play_data.shape[1])
    if target_channels == 1 and current_channels > 1:
        # A real downmix keeps information from both stereo channels. Taking only
        # the left channel can remove vocals or instruments from some recordings.
        play_data = np.mean(play_data, axis=1, dtype=np.float32).reshape(-1, 1)
    elif target_channels == 2 and current_channels == 1:
        play_data = np.repeat(play_data, 2, axis=1)
    elif target_channels == 2 and current_channels > 2:
        # The first pair is the front stereo image. Preserve it rather than
        # averaging every surround channel and collapsing the stereo field.
        play_data = play_data[:, :2]

    play_data = np.nan_to_num(play_data, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(play_data))) if play_data.size else 0.0
    if peak > _MAX_OUTPUT_PEAK:
        play_data = play_data * (_MAX_OUTPUT_PEAK / peak)
    return np.ascontiguousarray(play_data, dtype=np.float32)


def _query_output_format(device_id: int | str | None, source_rate: int) -> tuple[int, int]:
    query_id: int | None = device_id if isinstance(device_id, int) else None
    if device_id is None:
        query_id = int(sd.default.device[1])
    if query_id is None or query_id < 0:
        return source_rate, 2
    info = sd.query_devices(query_id)
    output_rate = int(info.get("default_samplerate", source_rate) or source_rate)
    output_channels = min(max(int(info.get("max_output_channels", 2)), 1), 2)
    return output_rate, output_channels


def prepare_music_track(
    path: Path | str,
    device: int | str | None,
    *,
    cancel_event: threading.Event | None = None,
) -> PreparedMusicTrack:
    """Decode and convert a track for the selected output; safe to call in a worker."""
    resolved = Path(path)
    data, source_rate = load_audio_file(resolved, cancel_event=cancel_event)
    try:
        output_rate, output_channels = _query_output_format(device, source_rate)
    except Exception:
        output_rate, output_channels = source_rate, 2
    playback_data = _prepare_playback_data(
        data,
        source_rate,
        output_rate,
        output_channels,
        cancel_event,
    )
    return PreparedMusicTrack(
        path=resolved,
        source_data=data,
        source_rate=source_rate,
        playback_data=playback_data,
        output_rate=output_rate,
        output_channels=output_channels,
        device=device,
    )


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
        self._dev_sr: int | None = None
        self._dev_ch: int = 2
        self._volume = 1.0
        self._loop = False
        self._paused = False
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._play_data: np.ndarray | None = None  # 重采样后的播放数据
        self._prepared_format: tuple[int, int] | None = None
        self._last_progress_at = 0.0

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
        sr = self._dev_sr if (self._play_data is not None and self._dev_sr) else self._sr
        return float(self._pos) / float(sr) if sr else 0.0

    def load(self, path: Path | str) -> tuple[str, float]:
        """Load file; returns (display_name, duration_sec). Stops current playback."""
        self.stop()
        data, sr = load_audio_file(path)
        self._data = data
        self._sr = sr
        self._pos = 0
        self._path = Path(path)
        self._play_data = None
        self._prepared_format = None
        return self._path.name, self.duration_sec

    def load_prepared(self, track: PreparedMusicTrack) -> tuple[str, float]:
        """Commit worker-prepared audio without decoding on the caller's thread."""
        self.stop()
        self._data = track.source_data
        self._sr = track.source_rate
        self._pos = 0
        self._path = track.path
        self._device = track.device
        self._dev_sr = track.output_rate
        self._dev_ch = track.output_channels
        self._play_data = track.playback_data
        self._prepared_format = (track.output_rate, track.output_channels)
        return track.path.name, self.duration_sec

    def set_device(self, device_id: int | str | None) -> None:
        if device_id == self._device and self._dev_sr is not None:
            return
        self._device = device_id
        self._dev_sr = None
        self._dev_ch = 2
        self._play_data = None
        self._prepared_format = None
        with contextlib.suppress(Exception):
            self._dev_sr, self._dev_ch = _query_output_format(device_id, self._sr)

    def set_volume(self, volume: float) -> None:
        # Digital gain above unity clips mastered music and cannot be lossless.
        # Keep attenuation only; users can raise the receiving application's volume.
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)

    def play(self) -> None:
        if self._data is None:
            raise RuntimeError("请先选择音频文件")
        if self._stream is not None and self._stream.active:
            self._paused = False
            return

        play_sr = self._dev_sr or self._sr
        channels = self._dev_ch
        if self._play_data is None or self._prepared_format != (play_sr, channels):
            self._play_data = _prepare_playback_data(
                self._data,
                self._sr,
                play_sr,
                channels,
            )
            self._prepared_format = (play_sr, channels)
        play_data = self._play_data
        device = self._device
        pulse_sink = device if isinstance(device, str) else None
        if pulse_sink is not None:
            device = None

        self._paused = False
        self._play_data = play_data
        self._last_progress_at = 0.0

        def callback(
            outdata: np.ndarray,
            frames: int,
            _time: Any,
            status: Any,
        ) -> None:
            if status:
                logger.debug(f"music share stream status: {status}")
            progress: tuple[float, float] | None = None
            stop_after_callback = False
            with self._lock:
                if self._paused or self._play_data is None:
                    outdata.fill(0)
                    return
                outdata.fill(0)
                written = 0
                total_frames = self._play_data.shape[0]
                while written < frames and total_frames > 0:
                    available = total_frames - self._pos
                    count = min(frames - written, available)
                    np.multiply(
                        self._play_data[self._pos : self._pos + count],
                        self._volume,
                        out=outdata[written : written + count],
                    )
                    written += count
                    self._pos += count
                    if self._pos >= total_frames:
                        if self._loop:
                            self._pos = 0
                        else:
                            stop_after_callback = True
                            break

                now = time.monotonic()
                if self.on_progress and (
                    stop_after_callback or now - self._last_progress_at >= _PROGRESS_INTERVAL_SEC
                ):
                    self._last_progress_at = now
                    progress = (self.position_sec, self.duration_sec)

            if progress is not None and self.on_progress:
                with contextlib.suppress(Exception):
                    self.on_progress(*progress)
            if stop_after_callback:
                raise sd.CallbackStop

        try:
            with temporary_pulse_sink(pulse_sink):
                self._stream = sd.OutputStream(
                    samplerate=play_sr,
                    channels=channels,
                    dtype="float32",
                    device=device,
                    latency="high",
                    callback=callback,
                    finished_callback=self._on_stream_finished,
                )
            self._stream.start()
            logger.info(
                f"Music share playing: {self._path} → device={self._device!r} "
                f"source_sr={self._sr} source_ch={self._data.shape[1]} "
                f"output_sr={play_sr} output_ch={channels} volume={self._volume:.2f}"
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
            self._play_data is not None and self._pos >= self._play_data.shape[0] and not self._loop
        )
        if finished and self.on_finished:
            with contextlib.suppress(Exception):
                self.on_finished()
