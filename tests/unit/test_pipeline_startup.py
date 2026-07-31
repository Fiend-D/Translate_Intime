"""Transactional startup tests for translation channels."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.core.exceptions import EngineRuntimeError
from src.core.pipeline import TranslationPipeline
from src.models.config import AppConfigModel
from src.models.enums import Direction


class _FakeEngine:
    engine_id = "economy"

    def __init__(self) -> None:
        self.started: list[Direction] = []
        self.stopped: list[Direction] = []

    @property
    def active_directions(self) -> frozenset[Direction]:
        return frozenset(set(self.started) - set(self.stopped))

    def start_direction(self, direction: Direction, *, play_voice: bool = False) -> bool:
        del play_voice
        self.started.append(direction)
        return True

    def stop_direction(self, direction: Direction) -> None:
        self.stopped.append(direction)

    def send_pcm(self, direction: Direction, pcm: bytes) -> None:
        del direction, pcm

    def close(self) -> None:
        return None


class _FailOnceCapture:
    instances: list[_FailOnceCapture] = []
    should_fail = True

    def __init__(self, direction: Direction, device: object, **kwargs: object) -> None:
        del kwargs
        self.direction = direction
        self.device = device
        self.on_pcm = None
        self.on_error = None
        self.running = False
        self.stop_calls = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        if self.__class__.should_fail:
            self.__class__.should_fail = False
            raise EngineRuntimeError("capture unavailable")
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.stop_calls += 1

    def is_running(self) -> bool:
        return self.running


class _FailingPlayer:
    instances: list[_FailingPlayer] = []

    def __init__(self, device: object) -> None:
        del device
        self.on_segment_finished = None
        self.stop_calls = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        raise EngineRuntimeError("output unavailable")

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.parametrize("direction", [Direction.OUTBOUND, Direction.INBOUND])
def test_failed_capture_start_rolls_back_and_allows_retry(
    tmp_path, monkeypatch, direction: Direction
) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        vad_enabled=False,
        vad_game_enabled=False,
    )
    pipeline = TranslationPipeline(config)
    engine = _FakeEngine()
    pipeline._engine = engine
    begin_session = Mock(return_value=tmp_path / "subtitles.txt")
    pipeline._subtitle_logger.begin_session = begin_session

    _FailOnceCapture.instances = []
    _FailOnceCapture.should_fail = True
    monkeypatch.setattr("src.core.pipeline.AudioCapture", _FailOnceCapture)
    monkeypatch.setattr(pipeline, "_validate_feedback_routes", lambda _direction: None)
    monkeypatch.setattr(pipeline, "_resolve_loopback_device", lambda: "loopback:test")

    with pytest.raises(EngineRuntimeError, match="capture unavailable"):
        pipeline.start_channel(direction, play_voice=False)

    assert engine.stopped == [direction]
    assert pipeline._capture_outbound is None
    assert pipeline._capture_inbound is None
    assert pipeline.active_channels() == []
    assert pipeline._running is False
    assert begin_session.call_count == 0
    assert _FailOnceCapture.instances[0].stop_calls == 1

    pipeline.start_channel(direction, play_voice=False)

    assert engine.started == [direction, direction]
    assert pipeline.is_channel_active(direction) is True
    assert begin_session.call_count == 1

    pipeline.stop_channel(direction)


def test_failed_player_start_stops_capture_and_engine(tmp_path, monkeypatch) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        translation_mode="economy",
        vad_enabled=False,
    )
    pipeline = TranslationPipeline(config)
    engine = _FakeEngine()
    pipeline._engine = engine
    pipeline._subtitle_logger.begin_session = Mock(return_value=tmp_path / "subtitles.txt")

    _FailOnceCapture.instances = []
    _FailOnceCapture.should_fail = False
    _FailingPlayer.instances = []
    monkeypatch.setattr("src.core.pipeline.AudioCapture", _FailOnceCapture)
    monkeypatch.setattr("src.core.pipeline.AudioPlayer", _FailingPlayer)
    monkeypatch.setattr(pipeline, "_validate_feedback_routes", lambda _direction: None)

    with pytest.raises(EngineRuntimeError, match="output unavailable"):
        pipeline.start_channel(Direction.OUTBOUND, play_voice=True)

    assert engine.stopped == [Direction.OUTBOUND]
    assert pipeline._capture_outbound is None
    assert pipeline._player is None
    assert pipeline._play_outbound_voice is False
    assert _FailOnceCapture.instances[0].stop_calls == 1
    assert _FailingPlayer.instances[0].stop_calls == 1
