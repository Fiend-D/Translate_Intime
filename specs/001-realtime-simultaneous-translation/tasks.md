# Tasks: Realtime Simultaneous Translation

**Input**: Design documents from `/specs/001-realtime-simultaneous-translation/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [research.md](./research.md), [quickstart.md](./quickstart.md)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure.

[x] T001 Create project directory structure per plan.md (`src/core/`, `src/gui/`, `src/models/`, `src/utils/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/`)
[x] T002 [P] Create `requirements.txt` with runtime dependencies (PyQt6, PyAudio, sounddevice, soundfile, transformers, torch, pydantic, loguru, numpy, httpx, keyring, pynput)
[x] T003 [P] Create `requirements-dev.txt` with test/lint dependencies (pytest, pytest-qt, pytest-asyncio, ruff, mypy)
[x] T004 [P] Create `run.sh` and `run.bat` launch scripts that activate venv and execute `python -m src.main`
[x] T005 [P] Configure pytest in `pyproject.toml` with Qt test plugin and asyncio mode
[x] T006 [P] Create `.gitignore` for Python, PyTorch cache, and model weights

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

[x] T007 [P] Define core exceptions in `src/core/exceptions.py` (`EngineLoadError`, `EngineRuntimeError`, `EngineNotLoadedError`, `ConfigValidationError`)
[x] T008 [P] Implement `AudioChunk` and `PipelineResult` internal dataclasses in `src/models/internal.py`
[x] T009 [P] Implement `audio_utils.py` in `src/utils/` with `resample()`, `pcm16_to_float32()`, and `secure_clear()` buffer zeroing
[x] T010 [P] Implement `config_manager.py` in `src/utils/` with Pydantic `AppConfigModel` load/save and OS keyring integration for API keys
[x] T011 [P] Implement `logger.py` in `src/utils/` with Loguru setup and daily subtitle log rotation in `~/.config/translator_intime/logs/`
[x] T012 [P] Implement `EngineRegistry` in `src/core/engine_registry.py` with register/get for ASR/Translator/TTS classes
[x] T013 [P] Define `BaseASREngine` abstract interface in `src/core/asr/base.py` per `contracts/engine-interface.md`
[x] T014 [P] Define `BaseTranslator` abstract interface in `src/core/translator/base.py` per `contracts/engine-interface.md`
[x] T015 [P] Define `BaseTTSEngine` abstract interface in `src/core/tts/base.py` per `contracts/engine-interface.md`
[x] T016 [P] Define `SessionStatus`, `Direction`, `BackendType`, `LanguageCode` enums in `src/models/enums.py`
[x] T017 [P] Implement `TranslationSession` dataclass in `src/models/session.py` with state transition validation
[x] T018 [P] Implement `SubtitleEntry` dataclass in `src/models/subtitle.py` per `data-model.md`
[x] T019 [P] Implement `EngineConfig` dataclass in `src/models/config.py` with backend-specific validation rules
[x] T020 [P] Add unit test for config validation in `tests/unit/test_config.py` (valid/invalid paths, language mismatch, missing API keys)
[x] T021 [P] Add unit test for `secure_clear()` in `tests/unit/test_audio_utils.py`
[x] T022 [P] Create sample audio fixtures (`tests/fixtures/sample_audio/hello_zh.wav`, `hello_en.wav`) for integration tests

**Checkpoint**: Foundation ready — `EngineRegistry` can instantiate dummy engines; config loads/saves with keyring; audio utilities tested.

---

## Phase 3: User Story 1 - Outbound Realtime Interpretation (Priority: P1) 🎯 MVP

**Goal**: User speaks into microphone → ASR → translate → TTS → output to counterparty audio device.

**Independent Test**: Run `pytest tests/integration/test_pipeline.py -k outbound` with a sample Chinese WAV; verify English TTS audio bytes are produced and latency < 3 s.

### Tests for User Story 1

[x] T023 [P] [US1] Add contract test for `BaseASREngine` interface in `tests/unit/test_asr_engines.py` (mock subclass)
[x] T024 [P] [US1] Add contract test for `BaseTranslator` interface in `tests/unit/test_translators.py` (mock subclass)
[x] T025 [P] [US1] Add contract test for `BaseTTSEngine` interface in `tests/unit/test_tts_engines.py` (mock subclass)
[x] T026 [US1] Add integration test for outbound pipeline in `tests/integration/test_pipeline.py` using recorded fixture

### Implementation for User Story 1

[x] T027 [P] [US1] Implement Qwen3-ASR-0.6B engine in `src/core/asr/qwen_asr.py` with lazy GPU/CPU loading and `torch.inference_mode()`
[x] T028 [P] [US1] Implement Hunyuan HY-MT1.5-1.8B translator in `src/core/translator/hunyuan.py` with GPU memory check and CPU fallback
[x] T029 [P] [US1] Implement ChatTTS local engine in `src/core/tts/local_tts.py` with 22050 Hz mono PCM16 output
[x] T030 [P] [US1] Implement OpenAI cloud translator in `src/core/translator/openai_translator.py` with configurable base URL
[x] T031 [P] [US1] Implement OpenAI cloud TTS in `src/core/tts/cloud_tts.py` with voice selection
[x] T032 [US1] Implement microphone audio capture in `src/core/audio_capture.py` using PyAudio callback threads
[x] T033 [US1] Implement audio player / output routing in `src/core/audio_player.py` using sounddevice with device selection
[x] T034 [US1] Implement `TranslationSession` orchestrator in `src/core/pipeline.py` for outbound path (chunk → ASR → translate → TTS → play)
[x] T035 [US1] Add engine loading progress signals (`pyqtSignal`) for GUI feedback in `src/core/pipeline.py`
[x] T036 [US1] Create basic GUI main window skeleton in `src/gui/main_window.py` with dark-theme QSS and start/stop buttons
[x] T037 [US1] Wire start/stop buttons to `TranslationSession` lifecycle in `src/gui/main_window.py`

**Checkpoint**: User Story 1 is fully functional — speak into mic, hear translated speech from selected output device.

---

## Phase 4: User Story 2 - Inbound Realtime Interpretation (Priority: P1)

**Goal**: Counterparty audio (via virtual loopback) → ASR → translate → TTS → output to user headphones.

**Independent Test**: Route a pre-recorded English WAV through the virtual loopback; verify Chinese TTS plays on the user output device and latency < 3 s.

### Tests for User Story 2

[x] T038 [P] [US2] Add integration test for inbound pipeline in `tests/integration/test_pipeline.py` using virtual loopback mock

### Implementation for User Story 2

[x] T039 [US2] Extend `src/core/audio_capture.py` to support virtual loopback device capture (VB-Cable on Windows, PulseAudio null-sink on Linux)
[x] T040 [US2] Extend `src/core/pipeline.py` to instantiate dual pipelines (outbound + inbound) with independent engine configurations
[x] T041 [US2] Update `src/gui/main_window.py` with independent enable/disable toggles for outbound and inbound directions
[x] T042 [US2] Add audio device enumeration and selection UI in `src/gui/main_window.py` (input device, output device, loopback device)

**Checkpoint**: User Stories 1 AND 2 both work independently; user can enable/disable either direction.

---

## Phase 5: User Story 3 - Realtime Subtitle Overlay (Priority: P2)

**Goal**: Two transparent, borderless, fully draggable subtitle windows show the latest 1–2 utterances per direction.

**Independent Test**: Start a session and verify both subtitle windows appear; drag them to new positions; lock one and confirm mouse clicks pass through to the game window underneath.

### Tests for User Story 3

[x] T043 [P] [US3] Add Qt Test for subtitle overlay behavior in `tests/integration/test_subtitle_overlay.py` (create, drag, lock, text update)

### Implementation for User Story 3

[x] T044 [P] [US3] Implement `SubtitleOverlay` transparent window in `src/gui/subtitle_overlay.py` with `Qt.FramelessWindowHint`, `Qt.WA_TranslucentBackground`, and per-pixel transparency
[x] T045 [P] [US3] Implement subtitle text layout (original above translation, no separators, color-only differentiation) in `src/gui/subtitle_overlay.py`
[x] T046 [US3] Implement drag-to-move and resize-via-corner in `src/gui/subtitle_overlay.py` (disabled when locked)
[x] T047 [US3] Implement lock/unlock toggle with `Qt.WA_TransparentForMouseEvents` when locked in `src/gui/subtitle_overlay.py`
[x] T048 [US3] Integrate subtitle generation into `src/core/pipeline.py` — emit `SubtitleEntry` via signal after each translation
[x] T049 [US3] Implement subtitle log persistence in `src/utils/logger.py` per `contracts/subtitle-log-format.md`
[x] T050 [US3] Add GUI controls for subtitle font size (12–48 px) and text opacity (0.3–1.0) in `src/gui/main_window.py`
[x] T051 [US3] Implement window position/size memory in `AppConfig.subtitle_window_positions` and restore on launch

**Checkpoint**: Subtitle windows display, drag, lock, and persist logs correctly.

---

## Phase 6: User Story 4 - Engine Selection & Fallback (Priority: P2)

**Goal**: Users can pick local/cloud engines per capability; cloud failures trigger 3× exponential backoff then automatic fallback to local; one-click model download.

**Independent Test**: Configure an invalid OpenAI API key with Hunyuan as backup; start session; verify retry messages appear, then downgrade occurs and translation continues with local model.

### Tests for User Story 4

[x] T052 [P] [US4] Add unit test for cloud API retry logic in `tests/unit/test_pipeline.py` (mock failing HTTP client)
[x] T053 [P] [US4] Add unit test for automatic fallback in `tests/unit/test_pipeline.py` (verify local engine is invoked after retries exhaust)

### Implementation for User Story 4

[x] T054 [P] [US4] Implement engine settings UI with left-list + right-card layout in `src/gui/engine_settings.py` (one card per engine type)
[x] T055 [P] [US4] Implement masked API key input with show/hide toggle in `src/gui/engine_settings.py`
[x] T056 [P] [US4] Implement model path browse button and `config.json` existence validation in `src/gui/engine_settings.py`
[x] T057 [P] [US4] Implement one-click model download button with progress bar and background `QThread` in `src/gui/engine_settings.py`
[x] T058 [US4] Implement cloud API retry with exponential backoff (1s → 2s → 4s) in `src/core/pipeline.py`
[x] T059 [US4] Implement automatic fallback to configured local backup engine in `src/core/pipeline.py` with UI degradation notice
[x] T060 [US4] Implement engine hot-swap without restarting session in `src/core/pipeline.py` (unload old, load new, resume)
[x] T061 [US4] Add save-time configuration validation in `src/utils/config_manager.py` (check model paths, API key format, language mismatch)
[x] T062 [US4] Implement GPU memory detection and CPU fallback warning in `src/core/translator/hunyuan.py` and `src/core/asr/qwen_asr.py`
[x] T063 [US4] Add "Detect" button for local model status in `src/gui/engine_settings.py`

**Checkpoint**: Engine switching, retry, fallback, and download all functional; invalid configs blocked at save time.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories.

### UX & Consistency

[ ] T064 [P] Implement global hotkeys (start/stop, toggle subtitles) via `pynput` in `src/utils/hotkeys.py`
[ ] T065 [P] Add first-launch setup wizard in `src/gui/main_window.py` to guide model path and device selection
[ ] T066 [P] Add loading spinners and progress bars for all engine loading operations in `src/gui/main_window.py`
[ ] T067 [P] Review all GUI error messages for human-readability and i18n (no raw tracebacks)
[ ] T068 [P] Implement dark-theme QSS compliance audit across all `src/gui/` files

### Performance & Observability

[ ] T069 [P] Add performance metrics overlay (E2E latency, engine status) visible only when `debug_mode=True` in `src/gui/main_window.py`
[ ] T070 [P] Implement audio device hot-plug detection in `src/core/audio_capture.py` (pause on disconnect, auto-resume on reconnect)
[ ] T071 [P] Add CPU/RAM profiling script (`scripts/profile.py`) to validate constitutional limits (<2 GB RAM, <30 % CPU)
[ ] T072 [P] Optimize model warmup (empty inference on first load) in `src/core/asr/qwen_asr.py` and `src/core/translator/hunyuan.py`

### Security & Quality

[ ] T073 [P] Implement input validation for all user-provided paths and URLs in `src/utils/config_manager.py` (prevent path traversal)
[ ] T074 [P] Verify secure audio buffer zeroing after processing in `src/core/pipeline.py` and `src/core/audio_player.py`
[x] T075 [P] Run `ruff check src/` and `ruff format src/` with zero errors
[x] T076 [P] Run `mypy src/` and resolve all new type errors
[x] T077 [P] Add type hints to all public APIs in `src/core/` and `src/models/`
[ ] T078 [P] Run quickstart.md validation scenarios manually and verify all pass
[x] T079 [P] Create `README.md` with installation, model download, and usage instructions

**Checkpoint**: All constitutional principles verified; app is release-ready.

---

## Dependencies & Execution Order

### Phase Dependencies

| Phase | Depends On | Blocks |
|-------|-----------|--------|
| Phase 1: Setup | None | Phase 2 |
| Phase 2: Foundational | Phase 1 | Phase 3, 4, 5, 6 |
| Phase 3: US1 (Outbound) | Phase 2 | Phase 4 (US2 extends US1 pipeline) |
| Phase 4: US2 (Inbound) | Phase 2, Phase 3 | None |
| Phase 5: US3 (Subtitles) | Phase 2, Phase 3 | None |
| Phase 6: US4 (Engines) | Phase 2, Phase 3 | None |
| Phase 7: Polish | Phase 3–6 | Release |

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational (Phase 2). No dependencies on other stories. **This is the MVP scope.**
- **US2 (P1)**: Can start after Foundational + US1. Extends the same pipeline architecture to a second audio source.
- **US3 (P2)**: Can start after Foundational + US1. Adds visual output; does not block US2 or US4.
- **US4 (P2)**: Can start after Foundational + US1. Adds configuration UI and resilience logic; does not block US2 or US3.

### Parallel Opportunities

- All tasks marked **[P]** within the same phase can be implemented in parallel (different files, no cross-dependencies).
- Once Phase 2 is complete, **US2, US3, and US4 can proceed in parallel** if team capacity allows, because they depend only on the foundational layer and US1.
- Phase 7 polish tasks are all **[P]** and can be distributed across the team.

### Within Each User Story

- Contract tests (if included) should be written before engine implementations.
- Engine implementations (ASR, Translator, TTS) are independent and can be done in parallel.
- Pipeline orchestrator depends on all three engine types being available.
- GUI wiring depends on the pipeline being functional.

---

## Suggested MVP Scope

**Minimum Viable Product = Phase 1 + Phase 2 + Phase 3 (US1 only)**

Deliverables:
1. Application launches with dark-theme GUI.
2. User selects local Qwen ASR + Hunyuan translator + ChatTTS.
3. User clicks "Start Translation."
4. User speaks Chinese into microphone.
5. English TTS plays on selected output device within 3 seconds.
6. Basic subtitle window appears with Chinese original + English translation.

This gives users immediate value while US2–US4 are built incrementally.

---

## Task Summary

| Phase | Task Count | Parallel Tasks |
|-------|-----------|----------------|
| Phase 1: Setup | 6 | 5 |
| Phase 2: Foundational | 16 | 16 |
| Phase 3: US1 (Outbound) | 15 | 10 |
| Phase 4: US2 (Inbound) | 5 | 1 |
| Phase 5: US3 (Subtitles) | 9 | 3 |
| Phase 6: US4 (Engines) | 12 | 5 |
| Phase 7: Polish | 16 | 16 |
| **Total** | **79** | **56** |
