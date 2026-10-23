"""Audio player state and queue concurrency regressions."""

from unittest.mock import MagicMock

from src.core.audio_player import _MAX_QUEUED_SECONDS, _TTS_BYTES_PER_SEC, AudioPlayer


def _player_without_device_query(monkeypatch) -> AudioPlayer:  # noqa: ANN001
    monkeypatch.setattr(AudioPlayer, "_query_device", lambda self: None)
    player = AudioPlayer()
    player._running = True  # started, but without a playback thread
    return player


def test_idle_open_stream_is_not_reported_as_playing(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    player._stream = MagicMock(active=True)

    assert player.is_playing is False


def test_active_segment_is_reported_as_playing(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    player._active_segment = True

    assert player.is_playing is True


def test_clear_queue_uses_non_blocking_drain(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    player.play(b"first")
    player.play(b"second")
    closed = MagicMock()
    monkeypatch.setattr(player, "_close_stream", closed)

    player.clear_queue()

    assert player.queue_size == 0
    assert player.queued_seconds == 0
    closed.assert_called_once_with()


def test_queue_keeps_many_sentences_and_reports_duration(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    one_second = b"\x00\x00" * (_TTS_BYTES_PER_SEC // 2)
    for _ in range(8):
        player.play(one_second)

    assert player.queue_size == 8
    assert player.queued_seconds == 8.0


def test_queue_drops_oldest_only_past_duration_cap(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    ten_seconds = b"\x00\x00" * (_TTS_BYTES_PER_SEC // 2) * 10
    count = int(_MAX_QUEUED_SECONDS // 10) + 1
    for index in range(count):
        player.play(ten_seconds[:-1] + bytes([index]))

    queued = [player._queue.get_nowait()[1][-1] for _ in range(player.queue_size)]

    assert queued == list(range(1, count))


def test_clear_queue_interrupts_segment_between_write_chunks(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    writes: list[int] = []

    class _Stream:
        active = True

        def write(self, frames) -> None:  # noqa: ANN001
            writes.append(len(frames))
            if len(writes) == 2:
                # clear_queue() from another thread: bumps the generation.
                player._generation += 1

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(player, "_ensure_stream_locked", lambda: _Stream())
    player._device_ch = 1
    finished = MagicMock()
    player.on_segment_finished = finished
    one_second = b"\x00\x10" * 16000

    player.play(one_second)
    generation, data = player._queue.get_nowait()
    completed = player._play_immediate(data, generation)

    assert completed is False
    assert len(writes) == 2
    finished.assert_not_called()


def test_stale_queued_segment_is_skipped_by_playback_loop(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    played = MagicMock(return_value=True)
    monkeypatch.setattr(player, "_play_immediate", played)
    monkeypatch.setattr(player, "_close_stream", lambda: None)
    player._queue.put_nowait((player._generation, b"\x00\x00"))
    player._generation += 1

    player._running = False
    player._playback_loop()

    played.assert_not_called()


def test_audio_queued_after_stop_is_dropped(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    monkeypatch.setattr(player, "_close_stream", lambda: None)
    player.stop()

    player.play(b"\x00\x00" * 160)

    assert player.queue_size == 0
    assert player.is_playing is False


def test_idle_player_keeps_echo_reference_continuous(monkeypatch) -> None:  # noqa: ANN001
    import time

    import numpy as np

    from src.core.echo_canceller import PlaybackReference

    player = _player_without_device_query(monkeypatch)
    player._device_sr = 16000
    player._device_ch = 2
    reference = PlaybackReference(sample_rate=16000)
    clock = {"now": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    writes: list[tuple[int, ...]] = []

    class _Stream:
        active = True
        latency = 0.05

        def write(self, frames) -> bool:  # noqa: ANN001
            writes.append(frames.shape)
            clock["now"] += len(frames) / 16000
            return False

    player._stream = _Stream()
    player.on_reference = reference.push
    player.on_reference(np.ones(960, dtype=np.float32) * 0.1, 100.05, False)
    anchors = reference.anchors

    for _ in range(20):
        player._write_silence()
    player.on_reference(np.ones(960, dtype=np.float32) * 0.1, clock["now"] + 0.05, False)

    assert writes[0] == (960, 2)
    assert reference.anchors == anchors

    # Without the idle writes the next segment lands after a gap and re-anchors.
    clock["now"] += 1.2
    player.on_reference(np.ones(960, dtype=np.float32) * 0.1, clock["now"] + 0.05, False)
    assert reference.anchors == anchors + 1
