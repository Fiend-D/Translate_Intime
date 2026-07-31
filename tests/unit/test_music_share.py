"""Unit tests for the isolated, high-fidelity music playback path."""

import os
import threading
from pathlib import Path
from subprocess import CompletedProcess

import numpy as np
import pytest

from src.core import music_share
from src.core.music_share import (
    MusicSharePlayer,
    PreparedMusicTrack,
    _ffmpeg_decode,
    _prepare_playback_data,
    load_audio_file,
)


def test_prepare_playback_preserves_native_stereo() -> None:
    source = np.array([[0.1, -0.2], [0.3, -0.4]], dtype=np.float32)

    prepared = _prepare_playback_data(source, 48000, 48000, 2)

    assert prepared.dtype == np.float32
    assert prepared.flags.c_contiguous
    assert np.array_equal(prepared, source)


def test_prepare_playback_resamples_each_stereo_channel() -> None:
    frames = 4410
    timeline = np.arange(frames, dtype=np.float32) / 44100.0
    source = np.column_stack(
        [
            np.sin(2.0 * np.pi * 440.0 * timeline),
            np.sin(2.0 * np.pi * 880.0 * timeline),
        ]
    ).astype(np.float32)

    prepared = _prepare_playback_data(source, 44100, 48000, 2)

    assert prepared.shape == (4800, 2)
    assert not np.array_equal(prepared[:, 0], prepared[:, 1])
    assert float(np.max(np.abs(prepared))) <= 0.99901


def test_prepare_playback_downmixes_stereo_without_dropping_a_channel() -> None:
    source = np.array([[1.0, -1.0], [0.2, 0.6]], dtype=np.float32)

    prepared = _prepare_playback_data(source, 48000, 48000, 1)

    assert prepared.shape == (2, 1)
    assert np.allclose(prepared[:, 0], [0.0, 0.4])


def test_prepare_playback_removes_invalid_samples_and_prevents_clipping() -> None:
    source = np.array([[2.0, -2.0], [np.nan, np.inf]], dtype=np.float32)

    prepared = _prepare_playback_data(source, 48000, 48000, 2)

    assert np.all(np.isfinite(prepared))
    assert float(np.max(np.abs(prepared))) <= 0.99901


def test_music_volume_never_applies_clipping_gain() -> None:
    player = MusicSharePlayer()

    player.set_volume(1.5)
    assert player._volume == 1.0

    player.set_volume(-0.5)
    assert player._volume == 0.0


def test_default_output_format_is_queried(monkeypatch) -> None:  # noqa: ANN001
    class DefaultDevice:
        device = (1, 7)

    monkeypatch.setattr(music_share.sd, "default", DefaultDevice())
    monkeypatch.setattr(
        music_share.sd,
        "query_devices",
        lambda device_id: {"default_samplerate": 48000, "max_output_channels": 2},
    )
    player = MusicSharePlayer()

    player.set_device(None)

    assert player._dev_sr == 48000
    assert player._dev_ch == 2


def test_ffmpeg_fallback_keeps_rate_and_float_precision(monkeypatch) -> None:  # noqa: ANN001
    source_path = Path("track.m4a")
    captured_command: list[str] = []

    def fake_run(command, **_kwargs):  # noqa: ANN001, ANN003
        captured_command.extend(command)
        return CompletedProcess(command, 0, "", "")

    decoded = np.array([[0.1, -0.1], [0.2, -0.2]], dtype=np.float32)
    monkeypatch.setattr(music_share.subprocess, "run", fake_run)
    monkeypatch.setattr(
        music_share.sf,
        "read",
        lambda *_args, **_kwargs: (decoded.copy(), 48000),
    )

    result, sample_rate = _ffmpeg_decode(source_path)

    assert sample_rate == 48000
    assert np.array_equal(result, decoded)
    assert "pcm_f32le" in captured_command
    assert "-ar" not in captured_command
    assert "-ac" not in captured_command


def test_cancelled_music_load_stops_before_decode(tmp_path) -> None:
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(RuntimeError, match="cancelled"):
        load_audio_file(tmp_path / "track.wav", cancel_event=cancel_event)


def test_music_output_does_not_persistently_change_translation_sink(
    monkeypatch,
) -> None:  # noqa: ANN001
    opened: dict[str, object] = {}

    class FakeOutputStream:
        active = False

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            opened.update(kwargs)

        def start(self) -> None:
            self.active = True

        def abort(self) -> None:
            self.active = False

        def close(self) -> None:
            self.active = False

    monkeypatch.setenv("PULSE_SINK", "translation_sink")
    monkeypatch.setattr(music_share.sd, "OutputStream", FakeOutputStream)
    player = MusicSharePlayer()
    player._data = np.array([[0.1, -0.1], [0.2, -0.2]], dtype=np.float32)
    player._sr = 48000
    player._path = Path("track.wav")
    player.set_device("music_sink")

    player.play()

    assert os.environ["PULSE_SINK"] == "translation_sink"
    assert opened["samplerate"] == 48000
    assert opened["channels"] == 2
    assert opened["latency"] == "high"
    player.stop()


def test_prepared_track_is_reused_without_resampling(monkeypatch) -> None:  # noqa: ANN001
    prepared = np.array([[0.1, -0.1], [0.2, -0.2]], dtype=np.float32)
    track = PreparedMusicTrack(
        path=Path("track.wav"),
        source_data=prepared.copy(),
        source_rate=48000,
        playback_data=prepared,
        output_rate=48000,
        output_channels=2,
        device=7,
    )
    player = MusicSharePlayer()
    player.load_prepared(track)
    monkeypatch.setattr(
        music_share,
        "_prepare_playback_data",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected resample")),
    )

    class FakeOutputStream:
        active = False

        def __init__(self, **_kwargs) -> None:  # noqa: ANN003
            pass

        def start(self) -> None:
            self.active = True

        def abort(self) -> None:
            self.active = False

        def close(self) -> None:
            self.active = False

    monkeypatch.setattr(music_share.sd, "OutputStream", FakeOutputStream)

    player.play()

    assert player._play_data is prepared
    player.stop()
