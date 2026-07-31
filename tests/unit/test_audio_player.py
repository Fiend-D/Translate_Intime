"""Audio player state and queue concurrency regressions."""

from unittest.mock import MagicMock

from src.core.audio_player import _MAX_QUEUED_SEGMENTS, AudioPlayer


def _player_without_device_query(monkeypatch) -> AudioPlayer:  # noqa: ANN001
    monkeypatch.setattr(AudioPlayer, "_query_device", lambda self: None)
    return AudioPlayer()


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
    closed.assert_called_once_with()


def test_full_queue_drops_oldest_segment(monkeypatch) -> None:  # noqa: ANN001
    player = _player_without_device_query(monkeypatch)
    for index in range(_MAX_QUEUED_SEGMENTS + 1):
        player.play(bytes([index]))

    queued = [player._queue.get_nowait() for _ in range(_MAX_QUEUED_SEGMENTS)]

    assert queued == [bytes([index]) for index in range(1, _MAX_QUEUED_SEGMENTS + 1)]
