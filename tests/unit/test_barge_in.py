"""Unit tests for TTS barge-in helpers."""

from __future__ import annotations

import array
import time

from src.core.echo_canceller import AecResult
from src.core.pipeline import TranslationPipeline
from src.core.speech_gate import SpeechGate
from src.models.config import AppConfigModel
from src.models.enums import Direction


def _pcm(value: int, samples: int = 320) -> bytes:
    return array.array("h", [value] * samples).tobytes()


class _FakeEngine:
    def __init__(self) -> None:
        self.engine_id = "volc"
        self.active_directions = frozenset({Direction.OUTBOUND})
        self.sent: list[bytes] = []
        self.pending: list[Direction] = []

    def send_pcm(self, direction: Direction, data: bytes) -> None:
        assert direction == Direction.OUTBOUND
        self.sent.append(data)

    def notify_pcm_pending(self, direction: Direction) -> None:
        self.pending.append(direction)

    def close(self) -> None:
        return None


class _FakePlayer:
    def __init__(self) -> None:
        self.cleared = False
        self.is_playing = False
        self.queue_size = 0

    def clear_queue(self) -> None:
        self.cleared = True


def test_barge_in_clears_tts_and_flushes_buffer(tmp_path) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
        vad_backend="rms",
        vad_barge_in_ms=40,
    )
    pipeline = TranslationPipeline(config)
    pipeline._engine = _FakeEngine()
    pipeline._player = _FakePlayer()
    pipeline._vad_outbound = SpeechGate(
        backend="rms",
        sensitivity="medium",
        open_ms=20,
        hangover_ms=80,
        preroll_ms=60,
    )
    pipeline._tts_playing_until = time.time() + 5.0

    silence = _pcm(0)
    loud = _pcm(3000)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, silence)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, loud)
    pipeline._barge_in_started_at[Direction.OUTBOUND] = time.time() - 0.1
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, loud)

    assert pipeline._player.cleared is True
    assert pipeline._tts_playing_until <= time.time()
    assert pipeline._engine.sent
    assert silence in pipeline._engine.sent[0]
    assert loud in pipeline._engine.sent[0]


def test_feedback_buffer_limit_flushes_all_audio_instead_of_dropping(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    player = _FakePlayer()
    player.is_playing = True
    pipeline._engine = engine
    pipeline._player = player

    first = _pcm(1000)
    second = _pcm(2000)
    pipeline._feedback_buffer_max_bytes = len(first) + 1
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, first)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, second)

    assert engine.pending == [Direction.OUTBOUND, Direction.OUTBOUND]
    assert player.cleared is True
    assert engine.sent == [first + second]
    assert pipeline._feedback_buffer_bytes[Direction.OUTBOUND] == 0


def test_finished_tts_segment_does_not_count_played_duration_twice(tmp_path, monkeypatch) -> None:
    pipeline = TranslationPipeline(AppConfigModel(log_dir=str(tmp_path / "logs")))
    pipeline._player = _FakePlayer()
    now = 100.0
    monkeypatch.setattr("src.core.pipeline.time.time", lambda: now)

    pipeline._on_tts_segment_finished(played_sec=3.0)

    assert pipeline._tts_playing_until == now + pipeline._tts_silence_margin_sec


def test_barge_in_cancels_engine_tts_queue(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    cancelled: list[Direction] = []
    engine.cancel_pending_audio = cancelled.append  # type: ignore[attr-defined]
    pipeline._engine = engine
    pipeline._player = _FakePlayer()

    pipeline._end_tts_mute_for_barge_in(Direction.OUTBOUND)

    assert pipeline._player.cleared is True
    assert cancelled == [Direction.OUTBOUND, Direction.INBOUND]


def test_virtual_cable_output_keeps_mic_open_during_tts(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    player = _FakePlayer()
    player.is_playing = True
    player.is_virtual_output = True  # type: ignore[attr-defined]
    pipeline._engine = engine
    pipeline._player = player
    pipeline._device_name = lambda device, *, kind: "Microphone (USB Audio)"  # type: ignore[method-assign]
    pipeline._refresh_outbound_echo_route()

    speech = _pcm(2000)
    pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, speech)

    assert engine.sent == [speech]
    assert engine.pending == []
    assert player.cleared is False


def test_virtual_cable_mic_still_mutes_during_tts(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    player = _FakePlayer()
    player.is_virtual_output = True  # type: ignore[attr-defined]
    pipeline._player = player
    pipeline._device_name = lambda device, *, kind: "CABLE Output (VB-Audio)"  # type: ignore[method-assign]

    pipeline._refresh_outbound_echo_route()

    assert pipeline._outbound_echo_free is False


class _FakeAec:
    def __init__(self, result: AecResult) -> None:
        self.result = result
        self.calls = 0

    def process(self, pcm: bytes, end_time: float) -> AecResult:
        del end_time
        self.calls += 1
        return AecResult(self.result.pcm or pcm, *self._fields())

    def _fields(self) -> tuple[bool, float, float, bool]:
        r = self.result
        return r.far_active, r.echo_energy, r.residual_energy, r.converged


def _aec_pipeline(tmp_path, aec_result: AecResult) -> tuple[TranslationPipeline, _FakeAec]:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_backend="rms", vad_barge_in_ms=40)
    )
    pipeline._engine = _FakeEngine()
    pipeline._player = _FakePlayer()
    pipeline._vad_outbound = SpeechGate(
        backend="rms", sensitivity="medium", open_ms=20, hangover_ms=80, preroll_ms=60
    )
    aec = _FakeAec(aec_result)
    pipeline._aec = aec  # type: ignore[assignment]
    pipeline._tts_playing_until = time.time() + 5.0
    return pipeline, aec


def _drive_barge_in(pipeline: TranslationPipeline) -> None:
    loud = _pcm(3000)
    for _ in range(3):
        pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, loud)
        if pipeline._barge_in_started_at[Direction.OUTBOUND] > 0:
            pipeline._barge_in_started_at[Direction.OUTBOUND] = time.time() - 0.1


def test_residual_echo_does_not_barge_in(tmp_path) -> None:
    echo_only = AecResult(b"", True, echo_energy=0.01, residual_energy=0.001, converged=True)
    pipeline, aec = _aec_pipeline(tmp_path, echo_only)
    _drive_barge_in(pipeline)
    assert aec.calls == 3
    assert pipeline._player.cleared is False


def test_near_end_speech_over_echo_barges_in(tmp_path) -> None:
    talking = AecResult(b"", True, echo_energy=0.001, residual_energy=0.02, converged=True)
    pipeline, _aec = _aec_pipeline(tmp_path, talking)
    _drive_barge_in(pipeline)
    assert pipeline._player.cleared is True


def test_unconverged_aec_keeps_vad_barge_in(tmp_path) -> None:
    unknown = AecResult(b"", True, echo_energy=0.01, residual_energy=0.001, converged=False)
    pipeline, _aec = _aec_pipeline(tmp_path, unknown)
    _drive_barge_in(pipeline)
    assert pipeline._player.cleared is True


def test_echo_canceller_only_runs_when_speakers_can_echo(tmp_path) -> None:
    pipeline = TranslationPipeline(AppConfigModel(log_dir=str(tmp_path / "logs")))
    player = _FakePlayer()
    player.on_reference = None  # type: ignore[attr-defined]
    pipeline._player = player
    pipeline._capture_outbound = object()  # type: ignore[assignment]
    pipeline._device_name = lambda device, *, kind: "Microphone (USB Audio)"  # type: ignore[method-assign]

    pipeline._refresh_outbound_echo_route()
    assert pipeline._aec is not None
    assert callable(player.on_reference)  # type: ignore[attr-defined]

    player.is_virtual_output = True  # type: ignore[attr-defined]
    pipeline._refresh_outbound_echo_route()
    assert pipeline._aec is None
    assert player.on_reference is None  # type: ignore[attr-defined]


def test_continuous_game_voice_does_not_cut_translation_playback(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    engine.active_directions = frozenset({Direction.INBOUND})
    sent: list[bytes] = []
    engine.send_pcm = lambda _direction, data: sent.append(data)  # type: ignore[method-assign]
    player = _FakePlayer()
    player.is_playing = True
    pipeline._engine = engine
    pipeline._player = player
    pipeline._tts_playing_until = time.time() + 5.0
    pipeline._inbound_route_checked = True

    voice = _pcm(3000)
    for _ in range(50):
        pipeline._handle_pcm_with_feedback_suppression(Direction.INBOUND, voice)

    assert player.cleared is False
    assert len(sent) == 50


def test_unverified_game_route_pauses_during_playback(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    engine.active_directions = frozenset({Direction.INBOUND})
    sent: list[bytes] = []
    engine.send_pcm = lambda _direction, data: sent.append(data)  # type: ignore[method-assign]
    pipeline._engine = engine
    pipeline._player = _FakePlayer()
    pipeline._tts_playing_until = time.time() + 5.0

    pipeline._handle_pcm_with_feedback_suppression(Direction.INBOUND, _pcm(3000))

    assert sent == []


def test_mic_start_checks_loopback_the_game_channel_really_uses(tmp_path, monkeypatch) -> None:
    pipeline = TranslationPipeline(AppConfigModel(log_dir=str(tmp_path / "logs")))
    pipeline._inbound_active = True
    pipeline._inbound_loopback = 7
    pipeline._loopback_device = None
    pipeline._play_outbound_voice = True
    pipeline._output_device = 3
    seen: list[object] = []

    def shares(**kw: object) -> bool:
        seen.append(kw["loopback_device"])
        return True

    monkeypatch.setattr("src.audio.device_guard.shares_physical_output_path", shares)
    monkeypatch.setattr(pipeline, "_device_name", lambda device, *, kind: str(device))

    pipeline._validate_feedback_routes(Direction.OUTBOUND)
    assert seen == [7]
    assert pipeline._inbound_feedback_route is True
    assert pipeline._inbound_route_checked is True


def test_shared_output_route_cancels_tts_on_game_channel(tmp_path) -> None:
    pipeline = TranslationPipeline(AppConfigModel(log_dir=str(tmp_path / "logs")))
    player = _FakePlayer()
    player.on_reference = None  # type: ignore[attr-defined]
    pipeline._player = player
    pipeline._capture_outbound = object()  # type: ignore[assignment]
    pipeline._device_name = lambda device, *, kind: "Microphone (USB Audio)"  # type: ignore[method-assign]
    pipeline._inbound_feedback_route = True

    pipeline._refresh_outbound_echo_route()
    assert pipeline._aec is not None
    assert pipeline._aec_inbound is not None
    assert player.on_reference == pipeline._reference.push  # type: ignore[attr-defined,union-attr]

    pipeline._capture_outbound = None
    pipeline._refresh_echo_canceller()
    assert pipeline._aec is None
    assert pipeline._aec_inbound is not None
    assert callable(player.on_reference)  # type: ignore[attr-defined]

    pipeline._inbound_feedback_route = False
    pipeline._refresh_echo_canceller()
    assert pipeline._aec_inbound is None
    assert player.on_reference is None  # type: ignore[attr-defined]


def test_game_channel_runs_loopback_canceller(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_enabled=False)
    )
    engine = _FakeEngine()
    engine.active_directions = frozenset({Direction.INBOUND})
    sent: list[bytes] = []
    engine.send_pcm = lambda _direction, data: sent.append(data)  # type: ignore[method-assign]
    pipeline._engine = engine
    pipeline._player = _FakePlayer()
    pipeline._inbound_route_checked = True
    cleaned = _pcm(0)
    aec = _FakeAec(AecResult(cleaned, True, echo_energy=0.01, residual_energy=0.0, converged=True))
    pipeline._aec_inbound = aec  # type: ignore[assignment]

    pipeline._handle_pcm_with_feedback_suppression(Direction.INBOUND, _pcm(3000))

    assert aec.calls == 1
    assert sent == [cleaned]


def test_vad_rejected_frames_are_not_replayed_after_tts(tmp_path) -> None:
    pipeline = TranslationPipeline(
        AppConfigModel(log_dir=str(tmp_path / "logs"), vad_backend="rms")
    )
    pipeline._engine = _FakeEngine()
    pipeline._player = _FakePlayer()
    pipeline._vad_outbound_filters = True
    pipeline._vad_outbound = SpeechGate(
        backend="rms", sensitivity="medium", open_ms=20, hangover_ms=80, preroll_ms=60
    )
    pipeline._tts_playing_until = time.time() + 5.0

    for _ in range(5):
        pipeline._handle_pcm_with_feedback_suppression(Direction.OUTBOUND, _pcm(0))

    assert pipeline._feedback_buffer_bytes[Direction.OUTBOUND] == 0
