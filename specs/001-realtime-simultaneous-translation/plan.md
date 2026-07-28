# Implementation Plan: Realtime Simultaneous Translation

**Branch**: `001-realtime-simultaneous-translation` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-realtime-simultaneous-translation/spec.md`

## Summary

Build a desktop real-time simultaneous-interpretation application in Python 3.12 + PyQt6. The app captures microphone audio and virtual-loopback audio, runs them through pluggable ASR → Translation → TTS pipelines (local models or cloud APIs), and outputs synthesized speech to selectable audio devices. Two transparent subtitle overlays display the latest 1–2 utterances per direction. All heavy inference runs on background threads; the UI stays responsive. No backend server is required.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- **GUI**: PyQt6 (dark-theme QSS, borderless transparent overlays, global hotkeys via `pynput`)
- **Audio I/O**: `PyAudio` + `sounddevice` / `soundfile` for cross-platform capture and playback
- **Local ML**: `transformers` + `torch` (Qwen3-ASR-0.6B, Hunyuan HY-MT1.5-1.8B); local TTS via `ChatTTS` or `coqui-ai/TTS` (evaluated in research.md)
- **Cloud APIs**: OpenAI-compatible HTTP clients (`httpx` or `requests`)
- **Utilities**: `pydantic` (settings & data validation), `loguru` (structured logging), `numpy` (audio buffers)

**Storage**: Local filesystem only
- User config: `~/.config/translator_intime/config.json`
- Subtitle logs: `~/.config/translator_intime/logs/subtitles_*.txt`
- Model cache: user-configurable directory (default `./models/`)

**Testing**: `pytest` + `pytest-qt` for GUI automation; `pytest-asyncio` for pipeline tests

**Target Platform**: Windows 10+ and Linux (x86_64); macOS best-effort

**Project Type**: desktop-app

**Performance Goals**:
- Cloud E2E latency (audio in → translated voice out) < 2 s
- Local E2E latency < 3 s on recommended hardware (modern 4-core + 8 GB RAM + GPU optional)
- Subtitle on-screen latency < 500 ms
- CPU usage < 30 % during active translation
- Steady-state RAM < 2 GB

**Constraints**:
- UI thread must never block on I/O or inference
- Offline-capable when local ASR + translation + TTS are configured
- No persistent backend service or database server
- All credentials masked in UI and encrypted at rest (OS keyring when available)

**Scale/Scope**: Single-user desktop application; no multi-user or network coordination

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Verify compliance against Translator InTime Constitution v1.0.0:

1. **Code Quality**: Does the design avoid hard-coded values and keep modules single-responsibility?
   - ✅ Each engine (ASR, Translator, TTS) lives in its own module behind a common interface.
   - ✅ Configuration is centralized in `AppConfig`; no scattered env vars.

2. **Testing Standards**: Are unit and integration test strategies defined for all new engines/pipeline stages?
   - ✅ Unit tests for each engine with mocked I/O.
   - ✅ Integration tests for full pipeline using recorded audio fixtures.
   - ✅ GUI smoke tests for critical journeys (start, switch engine, lock subtitle).

3. **UX Consistency**: Does the feature fit the dark-theme design system and provide loading/error feedback?
   - ✅ QSS theming already established in `styles.py`.
   - ✅ All engine switching and model loading show spinners / progress bars.

4. **Performance**: Is latency impact quantified (<800 ms E2E subtitle, <30% CPU, <2 GB RAM)?
   - ✅ Spec defines <2 s cloud / <3 s local E2E voice; <500 ms subtitle.
   - ✅ Constitution <2 GB RAM and <30 % CPU targets respected.

5. **Maintainability**: Are external APIs wrapped behind engine interfaces and configuration centralized?
   - ✅ `BaseASREngine`, `BaseTranslator`, `BaseTTSEngine` interfaces defined.
   - ✅ `AppConfig` singleton handles all settings.

6. **Security Boundaries**: Are user inputs validated, credentials masked, and audio buffers zeroed?
   - ✅ API keys masked in UI and stored via keyring.
   - ✅ Audio buffers explicitly cleared after processing.
   - ✅ Path validation on model directory to prevent traversal.

## Project Structure

### Documentation (this feature)

```text
specs/001-realtime-simultaneous-translation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
src/
├── core/
│   ├── __init__.py
│   ├── pipeline.py              # TranslationSession orchestrator
│   ├── audio_capture.py         # Microphone & loopback capture (PyAudio)
│   ├── audio_player.py          # TTS audio output routing
│   ├── asr/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseASREngine interface
│   │   ├── qwen_asr.py          # Qwen3-ASR-0.6B local engine
│   │   └── whisper_cloud.py     # Cloud ASR fallback (OpenAI / Azure)
│   ├── translator/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseTranslator interface
│   │   ├── hunyuan.py           # Tencent Hunyuan HY-MT1.5-1.8B
│   │   └── openai_translator.py # OpenAI-compatible cloud translator
│   └── tts/
│       ├── __init__.py
│       ├── base.py              # BaseTTSEngine interface
│       ├── local_tts.py         # Local TTS (ChatTTS / Coqui)
│       └── cloud_tts.py         # Cloud TTS (OpenAI / Azure)
├── gui/
│   ├── __init__.py
│   ├── main_window.py           # Primary settings & control window
│   ├── subtitle_overlay.py      # Transparent borderless subtitle widgets
│   ├── engine_settings.py       # ASR/Translator/TTS configuration cards
│   └── styles.py                # Dark-theme QSS constants
├── models/
│   ├── __init__.py
│   ├── session.py               # TranslationSession dataclass
│   ├── subtitle.py              # SubtitleEntry dataclass
│   └── config.py                # AppConfig pydantic model
├── utils/
│   ├── __init__.py
│   ├── config_manager.py        # JSON persistence + keyring integration
│   ├── logger.py                # Loguru setup + subtitle log rotation
│   └── audio_utils.py           # Resampling, format conversion, buffer zeroing
└── main.py                      # Application entry point

tests/
├── unit/
│   ├── test_asr_engines.py
│   ├── test_translators.py
│   ├── test_tts_engines.py
│   └── test_config.py
├── integration/
│   ├── test_pipeline.py         # Full pipeline with mock audio
│   └── test_subtitle_overlay.py # Qt Test for overlay behavior
├── fixtures/
│   └── sample_audio/            # Short WAV clips for CI
└── conftest.py                  # Shared pytest fixtures

requirements.txt
requirements-dev.txt
run.sh
run.bat
```

**Structure Decision**: Single-project monolith. The app is a standalone desktop executable with no backend service. Engines are organized by capability (`asr/`, `translator/`, `tts/`) behind uniform base classes to keep the architecture simple and allow easy addition of new providers.

## Complexity Tracking

No constitution violations required. Architecture stays minimal:
- No separate backend process (all inference runs in background `QThread` workers).
- No database (JSON config + plain-text subtitle logs).
- No micro-services (single Python executable).
