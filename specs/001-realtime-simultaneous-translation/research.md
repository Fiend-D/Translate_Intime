# Phase 0 Research: Realtime Simultaneous Translation

## Decision: Local TTS Engine

**Decision**: Primary local TTS will use **ChatTTS** (latest stable release); **Coqui TTS** is documented as an alternative if ChatTTS proves unstable on the target hardware.

**Rationale**:
- ChatTTS produces highly natural Chinese/English mixed speech, which matches the primary user scenario (Chinese ↔ English gamers).
- It runs fully offline and supports GPU acceleration via PyTorch.
- Startup latency (~2–3 s first inference) is acceptable given the spec’s local E2E budget of < 3 s.

**Alternatives considered**:
- *Coqui TTS*: Mature, many languages, but Chinese quality is noticeably robotic compared with ChatTTS.
- *PaddleSpeech TTS*: Good Chinese quality, heavier dependencies (PaddlePaddle), larger community friction on Windows.
- *Edge-TTS (Microsoft)*: Cloud-dependent, violates offline requirement.

**Trade-off accepted**: ChatTTS model weights are ~2 GB; this consumes significant RAM but stays within the 2 GB steady-state budget when ASR + translation models are loaded sequentially (not all at once).

---

## Decision: Audio Capture & Playback Libraries

**Decision**: **PyAudio** for capture (device enumeration + stream callback), **sounddevice** + **soundfile** for playback.

**Rationale**:
- PyAudio has the most reliable cross-platform device listing (`get_device_info_by_index`) and supports non-blocking callback streams, which is critical for real-time microphone capture.
- sounddevice offers a simpler NumPy-based playback API (`sd.play`) and automatic sample-rate conversion, reducing boilerplate for TTS output.
- Both are pip-installable wheels on Windows/Linux.

**Alternatives considered**:
- *sounddevice for both capture/play*: Device enumeration is less detailed than PyAudio; hot-plug detection is weaker.
- *pyaudio alone for both*: Playback requires manual buffer management; sounddevice is simpler.

---

## Decision: Audio Streaming & Segmentation Strategy

**Decision**: Fixed-length sliding buffers (2.5 s chunks, 1 s stride) for ASR input; VAD (Voice Activity Detection) is deferred to post-MVP.

**Rationale**:
- Fixed chunks are deterministic and easy to debug; they guarantee a steady subtitle refresh rate.
- Qwen3-ASR-0.6B handles short audio well; 2.5 s is enough for a typical sentence fragment.
- VAD (e.g., Silero VAD or webrtcvad) would reduce redundant ASR calls but adds complexity and a third model to load. It can be added later without architectural changes.

**Trade-off accepted**: Without VAD, silent segments still get sent to ASR, wasting a small amount of CPU. This is acceptable for the first release.

---

## Decision: Qt6 Transparent Subtitle Overlay

**Decision**: Two `QWidget` instances with `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, plus `Qt.WA_TranslucentBackground` and a custom paint event for per-pixel transparency.

**Rationale**:
- `Qt.WA_TranslucentBackground` allows the window background to be fully transparent while text remains opaque.
- `Qt.Tool` prevents the window from appearing in the taskbar/dock.
- Mouse events are handled for drag/resize; when locked, `Qt.WA_TransparentForMouseEvents` is set so clicks pass through to underlying windows (games).
- Font color differentiation (white original + slightly dimmed white translation) avoids any graphical chrome, satisfying the spec’s "no lines or graphics" requirement.

**Alternatives considered**:
- *QML*: More declarative but adds a new language/file type to the project; unnecessary for two simple text labels.
- *OpenGL overlay*: Overkill and breaks on some GPU drivers.

---

## Decision: Configuration Persistence

**Decision**: **Pydantic v2** models serialized to JSON (`~/.config/translator_intime/config.json`); API keys stored via **`keyring`** (cross-platform OS keychain abstraction).

**Rationale**:
- Pydantic gives type-safe validation and clear error messages on corrupt config.
- JSON is human-readable for debugging and trivial to version-control as test fixtures.
- `keyring` delegates to Windows Credential Locker / macOS Keychain / Linux Secret Service, satisfying the "encrypted at rest" security principle without custom crypto code.

**Alternatives considered**:
- *SQLite*: Adds a binary dependency and schema migration burden; overkill for a handful of settings.
- *YAML*: Human-friendly but requires an extra dependency (`PyYAML`) and offers no validation without Pydantic anyway.

---

## Decision: Threading & Concurrency Model

**Decision**: **QThreadPool** + custom `QRunnable` tasks for per-chunk pipeline stages; a dedicated `QThread` with `moveToThread` for long-lived audio capture loops.

**Rationale**:
- `QThreadPool` reuses threads automatically and avoids the overhead of spawning a new thread per 2.5 s audio chunk.
- Each pipeline stage (ASR, translate, TTS) runs as a separate runnable so the GUI can cancel or swap engines mid-flight.
- `pyqtSignal` is used for thread-safe communication back to the GUI (subtitle text, status updates).

**Alternatives considered**:
- *Python `threading` directly*: Works but lacks Qt-native signal/slot integration; requires manual mutexes.
- *Multiprocessing*: Would isolate GIL contention but adds huge serialization overhead for PyTorch tensors and audio buffers; rejected for simplicity.
- *asyncio*: Not a natural fit for PyQt6’s event loop without extra bridging libraries.

---

## Decision: Model Loading & Memory Management

**Decision**: Engines are lazy-loaded on first use and cached in singleton instances; `torch.inference_mode()` is used for all local model forward passes.

**Rationale**:
- Lazy loading keeps startup time fast (< 5 clicks to start translation per SC-001).
- Singleton caching avoids reloading models when the user toggles inbound/outbound.
- `inference_mode()` disables gradient computation and saves memory compared with `no_grad()`.
- If GPU OOM occurs, the engine catches `torch.cuda.OutOfMemoryError`, clears the CUDA cache, and falls back to CPU (already implemented in `hunyuan_engine.py`).

---

## Open Questions Deferred to Implementation

1. **ChatTTS installation on Windows**: The project currently assumes a working `pip install`. If ChatTTS compilation fails on Windows CI, fallback to Coqui TTS.
2. **Virtual audio cable UX on Linux**: PipeWire null-sink creation may require `pactl` commands. A one-time setup wizard may be needed; deferred to post-MVP polish.
3. **Global hotkey scope**: `pynput` global listeners may trigger antivirus heuristics on Windows. If this occurs, switch to Qt-native `QShortcut` (application-level only).
