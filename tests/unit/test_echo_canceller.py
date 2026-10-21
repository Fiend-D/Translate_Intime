"""Unit tests for the software echo canceller (synthetic signals, no audio hardware)."""

from __future__ import annotations

import numpy as np

from src.core.echo_canceller import (
    AecResult,
    EchoCanceller,
    LoopbackEchoCanceller,
    PlaybackReference,
)

SR = 16000
MIC_CHUNK = 640
PLAY_CHUNK = 960


def _speechy(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    kernel = np.exp(-np.arange(64) / 8.0)
    s = np.convolve(rng.standard_normal(n), kernel)[:n]
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 3 * np.arange(n) / SR + rng.uniform(0, 6))) ** 2
    s = s * envelope
    return 0.2 * s / np.max(np.abs(s))


def _room(delay_ms: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    d0 = int(delay_ms * SR / 1000)
    ir = np.zeros(d0 + 800)
    ir[d0:] = rng.standard_normal(800) * np.exp(-np.arange(800) / 150.0) * 0.08
    return ir


def _simulate(
    far: np.ndarray, mic: np.ndarray, *, mic_latency_ms: float = 25.0
) -> tuple[np.ndarray, EchoCanceller, list[AecResult]]:
    """Feed playback and capture on a shared clock, as AudioPlayer/AudioCapture would."""
    reference = PlaybackReference(origin=0.0)
    aec = EchoCanceller(reference)
    out = np.zeros(mic.size)
    results: list[AecResult] = []
    played = written = 0
    for start in range(0, mic.size, MIC_CHUNK):
        while played < far.size and played / SR < start / SR + 0.06:
            seg = far[played : played + PLAY_CHUNK].astype(np.float32)
            reference.push(seg, (played + seg.size) / SR)
            played += seg.size
        chunk = mic[start : start + MIC_CHUNK]
        pcm = (np.clip(chunk, -1, 1) * 32767).astype(np.int16).tobytes()
        result = aec.process(pcm, (start + chunk.size) / SR + mic_latency_ms / 1000)
        results.append(result)
        cleaned = np.frombuffer(result.pcm, dtype=np.int16) / 32768.0
        out[written : written + cleaned.size] = cleaned
        written += cleaned.size
    return out, aec, results


def _db(a: np.ndarray, b: np.ndarray) -> float:
    return float(10 * np.log10((np.mean(a**2) + 1e-12) / (np.mean(b**2) + 1e-12)))


def test_reference_read_returns_pushed_samples_and_zero_fills_gaps() -> None:
    ref = PlaybackReference(origin=0.0)
    ref.push(np.ones(160, dtype=np.float32), end_time=0.01)
    ref.push(np.full(160, 2.0, dtype=np.float32), end_time=0.5)
    assert np.all(ref.read(160, 160) == 1.0)
    assert np.all(ref.read(ref.index_at(0.5), 160) == 2.0)
    assert np.all(ref.read(ref.index_at(0.3), 160) == 0.0)
    assert np.all(ref.read(ref.index_at(9.0), 160) == 0.0)


def test_passthrough_without_playback() -> None:
    near = _speechy(SR * 2, 7)
    out, aec, results = _simulate(np.zeros(SR * 2), near)
    assert np.allclose(out, near, atol=2 / 32768)
    assert not any(r.far_active for r in results)
    assert not aec.converged


def test_single_talk_echo_is_suppressed() -> None:
    far = _speechy(SR * 6, 1)
    echo = np.convolve(far, _room(30))[: far.size]
    out, aec, results = _simulate(far, echo)
    tail = slice(3 * SR, 6 * SR)
    assert _db(echo[tail], out[tail]) > 15.0
    assert aec.converged
    assert not results[-1].near_end_dominant


def test_double_talk_keeps_near_end_speech() -> None:
    n = SR * 8
    far = _speechy(n, 1)
    echo = np.convolve(far, _room(10))[:n]
    near = np.zeros(n)
    near[5 * SR :] = _speechy(3 * SR, 9) * 0.8
    out, _aec, results = _simulate(far, echo + near)
    talk = slice(6 * SR, 8 * SR)
    assert np.corrcoef(out[talk], near[talk])[0, 1] > 0.9
    late = results[-(2 * SR // MIC_CHUNK) :]
    echo_e = sum(r.echo_energy for r in late)
    resid_e = sum(r.residual_energy for r in late)
    assert AecResult(b"", True, echo_e, resid_e, True).near_end_dominant


def test_reference_ignores_early_timestamps_and_counts_discontinuities() -> None:
    ref = PlaybackReference(origin=0.0)
    ref.push(np.ones(160, dtype=np.float32), end_time=0.02)
    head = ref.head
    ref.push(np.full(160, 2.0, dtype=np.float32), end_time=0.015)
    assert ref.head == head + 160
    assert np.all(ref.read(head, 160) == 1.0)
    anchors = ref.anchors
    ref.push(np.full(160, 3.0, dtype=np.float32), end_time=0.03, discontinuity=True)
    assert ref.anchors == anchors + 1


_MONITOR_IR = np.array([0.2, 0.9, -0.15, 0.05])


def _loopback_sim(
    n: int,
    segments: list[tuple[int, np.ndarray, float]],
    game: np.ndarray | None = None,
    lag: int = 240,
) -> np.ndarray:
    """Monitor capture of our playback (``lag`` samples + short IR) plus other audio.

    ``segments`` are (start sample, TTS, timestamp error in seconds) as AudioPlayer
    would publish them; capture chunks carry their true end time.
    """
    far = np.zeros(n)
    for start, tts, _jitter in segments:
        far[start : start + tts.size] = tts
    capture = np.zeros(n)
    capture[lag:] = np.convolve(far, _MONITOR_IR)[: n - lag]
    if game is not None:
        capture += game
    reference = PlaybackReference(origin=0.0)
    aec = LoopbackEchoCanceller(reference)
    pushes = [
        (start + k, tts[k : k + PLAY_CHUNK], jitter)
        for start, tts, jitter in segments
        for k in range(0, tts.size, PLAY_CHUNK)
    ]
    out: list[bytes] = []
    for pos in range(0, n, MIC_CHUNK):
        # The player publishes a chunk right after handing it to the device.
        while pushes and pushes[0][0] < pos + MIC_CHUNK:
            p0, chunk, jitter = pushes.pop(0)
            reference.push(chunk.astype(np.float32), (p0 + chunk.size) / SR + jitter)
        block = capture[pos : pos + MIC_CHUNK]
        pcm = (np.clip(block, -1, 1) * 32767).astype(np.int16).tobytes()
        out.append(aec.process(pcm, (pos + block.size) / SR).pcm)
    return np.frombuffer(b"".join(out), dtype=np.int16) / 32768.0


def test_loopback_passthrough_without_playback() -> None:
    game = _speechy(SR * 2, 7)
    out = _loopback_sim(SR * 2, [], game)
    assert np.allclose(out, game[: out.size], atol=2 / 32768)


def test_loopback_removes_own_tts_entirely() -> None:
    n = SR * 8
    segments = [(SR // 2, _speechy(3 * SR, 1), 0.0), (5 * SR, _speechy(2 * SR, 2), 0.006)]
    out = _loopback_sim(n, segments)
    assert np.max(np.abs(out)) < 2 / 32768


def test_loopback_keeps_other_audio_over_tts() -> None:
    n = SR * 6
    game = np.zeros(n)
    game[3 * SR :] = _speechy(3 * SR, 9) * 0.6
    out = _loopback_sim(n, [(SR // 2, _speechy(5 * SR, 1), 0.0)], game)
    talk = slice(3 * SR + SR // 2, out.size)
    assert np.sum(out[talk] ** 2) / np.sum(game[talk] ** 2) > 0.9
    assert np.corrcoef(out[talk], game[talk])[0, 1] > 0.95


def test_large_output_latency_is_found_by_delay_search() -> None:
    far = _speechy(SR * 10, 1)
    echo = np.convolve(far, _room(450))[: far.size]
    out, aec, _results = _simulate(far, echo)
    assert aec.delay_shift_samples > int(0.3 * SR)
    tail = slice(7 * SR, 10 * SR)
    assert _db(echo[tail], out[tail]) > 10.0
