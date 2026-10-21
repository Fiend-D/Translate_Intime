"""Software acoustic echo cancellation (AEC) for the microphone channel.

The player publishes what it sends to the speaker on a shared 16 kHz timeline
(``PlaybackReference``). ``EchoCanceller`` aligns microphone blocks against that
timeline with wall-clock timestamps and removes the linear echo with a
partitioned-block frequency-domain NLMS filter. A GCC-PHAT estimator moves the
alignment window when the echo arrives outside the filter span (e.g. Bluetooth
speakers with several hundred milliseconds of output latency).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

_SR = 16000
# Residual-energy ratios a loopback filter must reach (12 dB / 6 dB of echo removed).
_STRICT = 16.0
_LENIENT = 4.0


class PlaybackReference:
    """Thread-safe ring buffer of speaker output, indexed by absolute sample time."""

    def __init__(
        self,
        *,
        sample_rate: int = _SR,
        history_sec: float = 6.0,
        origin: float = 0.0,
        contiguity_sec: float = 0.05,
    ) -> None:
        self._sr = int(sample_rate)
        self._size = max(1, int(history_sec * self._sr))
        self._buf = np.zeros(self._size, dtype=np.float32)
        self._origin = float(origin)
        self._head = 0
        self._snap = int(contiguity_sec * self._sr)
        self._anchors = 0
        self._lock = threading.Lock()

    @property
    def sample_rate(self) -> int:
        return self._sr

    @property
    def anchors(self) -> int:
        """Number of times a push re-anchored the timeline (new playback segment)."""
        with self._lock:
            return self._anchors

    def index_at(self, t: float) -> int:
        return int(round((t - self._origin) * self._sr))

    def push(self, samples: np.ndarray, end_time: float, discontinuity: bool = False) -> None:
        """Record ``samples`` that finish sounding at ``end_time`` (monotonic seconds).

        Consecutive writes are kept contiguous; a timestamp more than
        ``contiguity_sec`` past the head, or a ``discontinuity`` reported by the
        device (underflow), re-anchors the timeline.
        """
        data = np.asarray(samples, dtype=np.float32).reshape(-1)
        if data.size == 0:
            return
        if data.size > self._size:
            data = data[-self._size :]
        n = data.size
        start = self.index_at(end_time) - n
        with self._lock:
            if not discontinuity and start - self._head <= self._snap:
                # The device plays one stream in order: an early timestamp is jitter.
                start = self._head
            else:
                start = max(start, self._head)
                self._anchors += 1
            if start > self._head:
                self._write_locked(self._head, np.zeros(min(start - self._head, self._size)))
            self._write_locked(start, data)
            self._head = max(self._head, start + n)

    @property
    def head(self) -> int:
        """Index just past the newest recorded sample."""
        with self._lock:
            return self._head

    def read(self, end_index: int, count: int) -> np.ndarray:
        """Return ``count`` samples ending at ``end_index``; unknown samples are zero."""
        out = np.zeros(count, dtype=np.float32)
        start = end_index - count
        with self._lock:
            lo = max(start, self._head - self._size)
            hi = min(end_index, self._head)
            if hi > lo:
                idx = np.arange(lo, hi) % self._size
                out[lo - start : hi - start] = self._buf[idx]
        return out

    def _write_locked(self, start: int, data: np.ndarray) -> None:
        if data.size == 0:
            return
        idx = np.arange(start, start + data.size) % self._size
        self._buf[idx] = data


@dataclass(frozen=True)
class AecResult:
    pcm: bytes
    far_active: bool
    echo_energy: float
    residual_energy: float
    converged: bool = False

    @property
    def near_end_dominant(self) -> bool:
        """Residual is clearly louder than the estimated echo (user is talking)."""
        if not self.far_active:
            return True
        return self.residual_energy > 1.5 * self.echo_energy + 1e-8


class EchoCanceller:
    """PBFDAF echo canceller with robust (error-clipped) NLMS adaptation."""

    def __init__(
        self,
        reference: PlaybackReference,
        *,
        block: int = 160,
        filter_ms: int = 320,
        pre_ms: int = 20,
        mu: float = 0.5,
        delay_search_ms: int = 900,
    ) -> None:
        self._ref = reference
        self._sr = reference.sample_rate
        self._B = int(block)
        self._N = 2 * self._B
        self._bins = self._B + 1
        self._P = max(1, math.ceil(filter_ms * self._sr / 1000 / self._B))
        self._pre = int(pre_ms * self._sr / 1000)
        self._mu = float(mu)
        self._W = np.zeros((self._P, self._bins), dtype=np.complex128)
        self._X = np.zeros((self._P, self._bins), dtype=np.complex128)
        self._prev_ref = np.zeros(self._B)
        self._err_scale = 0.0
        self._ed = 0.0
        self._ee = 0.0
        self._ed_slow = 0.0
        self._ee_slow = 0.0
        self._converged = False
        self._silent_ref_blocks = self._P + 1
        self._shift = -self._pre  # ref index offset relative to mic block end
        self._mic_head: int | None = None
        self._pending = np.zeros(0)
        self._snap = int(0.03 * self._sr)
        # Delay estimation buffers (raw mic and aligned reference, ~1 s).
        self._search = int(delay_search_ms * self._sr / 1000)
        self._win = self._sr
        self._hist_d = np.zeros(0)
        self._hist_x = np.zeros(0)
        self._far_blocks_since_estimate = 0
        self._last_lag: int | None = None

    @property
    def delay_shift_samples(self) -> int:
        return -self._shift

    def reset(self) -> None:
        self._W[:] = 0
        self._X[:] = 0
        self._prev_ref[:] = 0
        self._err_scale = 0.0
        self._ed = self._ee = 0.0
        self._ed_slow = self._ee_slow = 0.0
        self._converged = False

    def process(self, pcm: bytes, end_time: float) -> AecResult:
        """Cancel echo in a PCM16 mono chunk captured up to ``end_time``."""
        d_all = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
        n = d_all.size
        est_end = self._ref.index_at(end_time)
        expected_end = (self._mic_head or 0) + self._pending.size + n
        if self._mic_head is None or abs(est_end - expected_end) > self._snap:
            self._mic_head = est_end - n - self._pending.size
        buf = np.concatenate([self._pending, d_all])
        out: list[np.ndarray] = []
        echo_e = resid_e = 0.0
        far_any = False
        pos = 0
        while buf.size - pos >= self._B:
            d = buf[pos : pos + self._B]
            pos += self._B
            self._mic_head += self._B
            e, y, far = self._process_block(d, self._mic_head)
            out.append(e)
            far_any = far_any or far
            echo_e += float(np.dot(y, y))
            resid_e += float(np.dot(e, e))
        self._pending = buf[pos:].copy()
        if not out:
            return AecResult(b"", False, 0.0, 0.0)
        cleaned = np.concatenate(out)
        count = max(1, cleaned.size)
        pcm_out = (np.clip(cleaned, -1.0, 32767 / 32768) * 32768.0).astype(np.int16).tobytes()
        return AecResult(pcm_out, far_any, echo_e / count, resid_e / count, self._converged)

    @property
    def converged(self) -> bool:
        """The filter has removed ≥ 6 dB of echo at some point since the last reset."""
        return self._converged

    def _process_block(self, d: np.ndarray, mic_end: int) -> tuple[np.ndarray, np.ndarray, bool]:
        x = self._ref.read(mic_end + self._shift, self._B).astype(np.float64)
        far_energy = float(np.dot(x, x)) / self._B
        frame = np.concatenate([self._prev_ref, x])
        self._prev_ref = x
        self._X = np.roll(self._X, 1, axis=0)
        self._X[0] = np.fft.rfft(frame)
        if far_energy > 1e-9:
            self._silent_ref_blocks = 0
        else:
            self._silent_ref_blocks += 1
        self._track_delay(d, x, far_energy > 1e-7)
        if self._silent_ref_blocks > self._P:
            # The filter input is all zero: the output would equal the input.
            zero = np.zeros(self._B)
            return d, zero, False

        y = np.fft.irfft(np.sum(self._W * self._X, axis=0), self._N)[self._B :]
        e = d - y
        ed = float(np.dot(d, d)) / self._B
        ee = float(np.dot(e, e)) / self._B
        self._ed = 0.9 * self._ed + 0.1 * ed
        self._ee = 0.9 * self._ee + 0.1 * ee
        if self._ee > 2.0 * self._ed + 1e-9 and self._ed > 1e-8:
            # Diverged: the estimate adds energy. Start over and pass the input through.
            self.reset()
            return d, np.zeros(self._B), True

        if far_energy > 1e-7:
            self._adapt(e)
            self._ed_slow = 0.98 * self._ed_slow + 0.02 * ed
            self._ee_slow = 0.98 * self._ee_slow + 0.02 * ee
            if self._ed_slow > 1e-7 and self._ed_slow > 4.0 * self._ee_slow:
                self._converged = True
        return e, y, True

    def _adapt(self, e: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(e * e)))
        if self._err_scale <= 0.0:
            self._err_scale = rms
        # Clipping large errors keeps double-talk from dragging the filter away.
        limit = 2.5 * self._err_scale + 1e-6
        e_c = np.clip(e, -limit, limit)
        self._err_scale = 0.97 * self._err_scale + 0.03 * max(rms, 1e-6)
        err_f = np.fft.rfft(np.concatenate([np.zeros(self._B), e_c]))
        norm = np.sum(self._X.real**2 + self._X.imag**2, axis=0) + 1e-6 * self._N
        grad = np.conj(self._X) * (err_f / norm)
        w = np.fft.irfft(grad, self._N, axis=1)
        w[:, self._B :] = 0.0
        self._W += self._mu * np.fft.rfft(w, axis=1)

    def _track_delay(self, d: np.ndarray, x: np.ndarray, far_active: bool) -> None:
        keep = self._win + self._search
        self._hist_d = np.concatenate([self._hist_d, d])[-keep:]
        self._hist_x = np.concatenate([self._hist_x, x])[-keep:]
        if not far_active:
            return
        self._far_blocks_since_estimate += 1
        if self._far_blocks_since_estimate * self._B < self._sr or self._hist_d.size < keep:
            return
        self._far_blocks_since_estimate = 0
        lag = self._estimate_lag()
        if lag is None:
            self._last_lag = None
            return
        span = self._P * self._B
        low, high = self._pre // 2, int(span * 0.6)
        if low <= lag <= high:
            self._last_lag = None
            return
        if self._last_lag is None or abs(lag - self._last_lag) > int(0.004 * self._sr):
            self._last_lag = lag
            return
        # Two consistent estimates outside the filter span: move the window.
        target = self._pre + 2 * self._B
        self._shift -= lag - target
        self._last_lag = None
        self.reset()
        self._hist_d = np.zeros(0)
        self._hist_x = np.zeros(0)

    def _estimate_lag(self) -> int | None:
        """GCC-PHAT lag (samples) of the echo in the mic relative to the aligned reference."""
        d = self._hist_d[-self._win :]
        x = self._hist_x
        if float(np.dot(x, x)) < 1e-6 or float(np.dot(d, d)) < 1e-8:
            return None
        n = 1 << (d.size + x.size).bit_length()
        d_f = np.fft.rfft(d, n)
        x_f = np.fft.rfft(x, n)
        cross = d_f * np.conj(x_f)
        cross /= np.abs(cross) + 1e-12
        r = np.fft.irfft(cross, n)
        # d starts ``search`` samples after x, so lag L correlates at offset L - search.
        candidates = r[(np.arange(self._search + 1) - self._search) % n]
        k = int(np.argmax(candidates))
        peak = float(candidates[k])
        floor = float(np.mean(np.abs(candidates))) + 1e-12
        if peak / floor < 12.0:
            return None
        return k


class LoopbackEchoCanceller:
    """Remove our own TTS from a loopback/monitor capture of the same output device.

    Unlike a microphone, a monitor tap is a digital copy of the playback: a fixed
    lag plus the resampler's short response. GCC-PHAT finds the lag, a short FIR
    around it is solved by least squares while TTS plays, and blocks that hold
    only a faint residue of our speech are replaced by silence so ASR cannot
    hallucinate on them. Other audio (the game) passes through.
    """

    def __init__(
        self,
        reference: PlaybackReference,
        *,
        taps: int = 64,
        max_lag_ms: int = 500,
        history_sec: float = 2.0,
        fit_every_sec: float = 0.5,
        residue_floor: float = 0.003,
        hold_ms: int = 150,
    ) -> None:
        self._ref = reference
        self._sr = reference.sample_rate
        self._taps = int(taps)
        self._pre = self._taps // 2
        self._max_lag = int(max_lag_ms * self._sr / 1000)
        self._hist_len = int(history_sec * self._sr)
        self._fit_every = int(fit_every_sec * self._sr)
        self._recent = int(0.32 * self._sr)
        self._hold = int(hold_ms * self._sr / 1000)
        self._queue: deque[tuple[np.ndarray, int]] = deque()
        self._residue_floor_e = float(residue_floor) ** 2
        # Capture is a continuous stream: count samples, re-anchor only on real gaps.
        self._snap = int(0.2 * self._sr)
        self._head: int | None = None
        self._hist = np.zeros(0)
        self._since_fit = 0
        self._lag: int | None = None
        self._w = np.zeros(self._taps)
        self._converged = False
        self._anchors = reference.anchors
        self._unaligned = 0
        self._patience = int(1.5 * self._sr)

    @property
    def converged(self) -> bool:
        """Aligned with the current playback segment; until then far-active audio is muted."""
        return self._converged

    @property
    def lag_samples(self) -> int | None:
        return self._lag

    def process(self, pcm: bytes, end_time: float) -> AecResult:
        """Cancel our playback in a capture chunk; output may lag by up to ``hold``.

        The player publishes each chunk only after handing it to the device, so the
        monitor can deliver the sound before its reference exists. Chunks wait until
        the reference covers them (or ``hold`` expires) and are released in order.
        """
        d = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
        n = d.size
        if n:
            est_end = self._ref.index_at(end_time)
            if self._head is None or abs(est_end - (self._head + n)) > self._snap:
                self._head = est_end - n
            self._head += n
            self._queue.append((d, self._head))
        results: list[AecResult] = []
        while self._queue:
            blk, end = self._queue[0]
            needed = end - (self._lag if self._lag is not None else 0) + self._pre
            waited = self._queue[-1][1] - (end - blk.size)
            if self._ref.head < needed and waited < self._hold:
                break
            self._queue.popleft()
            results.append(self._cancel(blk, end))
        if not results:
            return AecResult(b"", False, 0.0, 0.0, self._converged)
        count = sum(len(r.pcm) for r in results) // 2 or 1
        return AecResult(
            b"".join(r.pcm for r in results),
            any(r.far_active for r in results),
            sum(r.echo_energy * len(r.pcm) for r in results) / 2 / count,
            sum(r.residual_energy * len(r.pcm) for r in results) / 2 / count,
            self._converged,
        )

    def _cancel(self, d: np.ndarray, end: int) -> AecResult:
        n = d.size
        pcm = (d * 32768.0).astype(np.int16).tobytes()
        self._hist = np.concatenate([self._hist, d])[-self._hist_len :]

        near = self._ref.read(end + self._max_lag, n + 2 * self._max_lag)
        far_active = float(np.dot(near, near)) / near.size > 1e-7
        if not far_active:
            return AecResult(pcm, False, 0.0, float(np.dot(d, d)) / n, self._converged)

        in_e = float(np.dot(d, d)) / n
        anchors = self._ref.anchors
        if anchors != self._anchors:
            # A new playback segment was placed on the timeline with fresh (jittered)
            # timestamps, so the old lag may be off by a few milliseconds.
            self._anchors = anchors
            self._converged = False
            self._unaligned = 0
        if not self._converged:
            self._unaligned += n
            # Re-align on the new segment; after a while accept a weaker fit, since the
            # game talking over our speech also limits how much the filter can explain.
            self._refit(end, recent=self._recent, strict=self._unaligned < self._patience)
            if not self._converged:
                # Dropping our own speech beats feeding it to ASR.
                silent = np.zeros(n, dtype=np.int16).tobytes()
                return AecResult(silent, True, in_e, 0.0, False)
            self._since_fit = 0

        self._since_fit += n
        if self._since_fit >= self._fit_every and self._hist.size >= self._sr:
            self._since_fit = 0
            self._refit(end)
        y = self._predict(end, n)
        e = d - y
        if in_e > self._residue_floor_e and float(np.dot(e, e)) / n * 10.0 > in_e:
            # Weak cancellation: the game is talking over us, or the timeline moved.
            # Refit on recent audio only, so the old alignment cannot outvote the new.
            self._since_fit = 0
            self._refit(end, recent=self._recent)
            y = self._predict(end, n)
            e = d - y
        echo_e = float(np.dot(y, y)) / n
        resid_e = float(np.dot(e, e)) / n
        silent = np.zeros(n, dtype=np.int16).tobytes()
        if echo_e > self._residue_floor_e:
            if resid_e > in_e and echo_e > 0.25 * in_e:
                # Subtracting the estimate added energy: the echo path moved without
                # a timeline anchor. Mute and re-align.
                self._converged = False
                self._unaligned = 0
                return AecResult(silent, True, echo_e, resid_e, False)
            if resid_e < 0.01 * echo_e or self._explained(end, e) > 0.4:
                # Leftover of our own speech: either faint, or still a filtered copy of
                # the reference (a slightly drifted path). Other audio is not.
                return AecResult(silent, True, echo_e, resid_e, True)
        elif resid_e < self._residue_floor_e:
            return AecResult(silent, True, echo_e, resid_e, True)
        out = (np.clip(e, -1.0, 32767 / 32768) * 32768.0).astype(np.int16).tobytes()
        return AecResult(out, True, echo_e, resid_e, True)

    def _window(self, end: int, lag: int, count: int) -> np.ndarray:
        """Rows of reference taps for the ``count`` capture samples ending at ``end``."""
        xs = self._ref.read(end - lag + self._pre, count + self._taps - 1).astype(np.float64)
        return np.lib.stride_tricks.sliding_window_view(xs, self._taps)[:, ::-1]

    def _explained(self, end: int, e: np.ndarray) -> float:
        """Share of the residual's energy a free filter on the reference could explain."""
        assert self._lag is not None
        x = self._window(end, self._lag, e.size)
        gram = x.T @ x
        reg = 1e-3 * float(np.trace(gram)) / self._taps + 1e-9
        proj = x @ np.linalg.solve(gram + reg * np.eye(self._taps), x.T @ e)
        return float(np.dot(proj, proj)) / (float(np.dot(e, e)) + 1e-12)

    def _predict(self, end: int, count: int) -> np.ndarray:
        assert self._lag is not None
        return self._window(end, self._lag, count) @ self._w

    def _refit(self, end: int, recent: int | None = None, *, strict: bool = False) -> None:
        """Re-solve the filter around the GCC lag and the current one.

        A new lag must remove ≥ 12 dB of the capture (``_STRICT``); keeping the
        current lag needs only 6 dB unless ``strict``, because the game talking over
        our speech is residue the filter cannot (and must not) explain.
        """
        span = self._hist.size if recent is None else min(recent, self._hist.size)
        d = self._hist[-span:]
        d_e = float(np.dot(d, d))
        if d_e < 1e-8:
            return
        candidates: list[tuple[int, float]] = []
        lag = self._estimate_lag(end, d[-self._sr :])
        if lag is not None and (self._lag is None or abs(lag - self._lag) > self._pre // 2):
            candidates.append((lag, _STRICT))
        if self._lag is not None:
            candidates.append((self._lag, _STRICT if strict else _LENIENT))
        for cand, need in candidates:
            fit = self._fit(end, cand, d)
            if fit is None or fit[1] * need > d_e:
                continue
            if cand != self._lag:
                self._lag = cand
                self._hist = d.copy()
            self._w = fit[0]
            self._converged = True
            return

    def _fit(self, end: int, lag: int, d: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Least-squares filter at ``lag`` and its residual energy over ``d``."""
        x = self._window(end, lag, d.size)
        if float(np.sum(x[:, self._pre] ** 2)) < 1e-6:
            return None
        gram = x.T @ x
        reg = 1e-4 * float(np.trace(gram)) / self._taps + 1e-9
        w = np.linalg.solve(gram + reg * np.eye(self._taps), x.T @ d)
        resid = d - x @ w
        return w, float(np.dot(resid, resid))

    def _estimate_lag(self, end: int, d: np.ndarray) -> int | None:
        """GCC-PHAT lag of the echo: capture[t] ~ reference[t - lag]."""
        m = self._max_lag
        x = self._ref.read(end + m, d.size + 2 * m).astype(np.float64)
        if float(np.dot(x, x)) < 1e-6 or float(np.dot(d, d)) < 1e-8:
            return None
        size = 1 << (x.size + d.size).bit_length()
        cross = np.conj(np.fft.rfft(d, size)) * np.fft.rfft(x, size)
        cross /= np.abs(cross) + 1e-12
        r = np.fft.irfft(cross, size)[: 2 * m + 1]
        k = int(np.argmax(r))
        if float(r[k]) / (float(np.mean(np.abs(r))) + 1e-12) < 8.0:
            return None
        return m - k
