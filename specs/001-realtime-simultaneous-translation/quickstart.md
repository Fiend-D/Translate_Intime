# Quickstart Validation Guide

## Prerequisites

- Python 3.12 installed
- `venv` module available
- Git (for model download scripts)
- A working microphone (for live tests)
- Optional: Virtual audio cable (VB-Cable on Windows, PulseAudio null-sink on Linux) for inbound testing

## Environment Setup

```bash
# 1. Create virtual environment
python3.12 -m venv .venv

# 2. Activate (Linux/macOS)
source .venv/bin/activate
#    Activate (Windows)
#    .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Validation Scenarios

### Scenario 1: Application Launch & Navigation

**Goal**: Verify the GUI starts, all settings pages are reachable, and the dark theme renders correctly.

```bash
python -m src.main
```

**Expected Result**:
- Main window opens within 3 seconds.
- Settings tabs (General, ASR, Translator, TTS) are clickable.
- No console errors; `ruff check src/` passes with zero warnings.

**Manual Checks**:
- [ ] Engine list shows all registered engines (Qwen ASR, Hunyuan, OpenAI, ChatTTS, etc.).
- [ ] Selecting a cloud engine reveals the API key input with mask toggle.
- [ ] Selecting a local engine reveals the model path input with browse button.

---

### Scenario 2: Configuration Persistence

**Goal**: Verify settings survive application restart.

**Steps**:
1. Open the app.
2. Change source language to `en`, target to `zh`.
3. Select ASR backend = `local`, set model path to `./models/test-asr`.
4. Enter a fake API key for OpenAI translator (will be stored in keyring).
5. Close the app completely.
6. Re-open the app.

**Expected Result**:
- Language selections are restored.
- Local model path is restored.
- API key field shows masked value (loaded from keyring, not JSON).
- Config JSON on disk (`~/.config/translator_intime/config.json`) does NOT contain the API key.

---

### Scenario 3: Local Model Load

**Goal**: Verify local ASR and translator models can be loaded and report readiness.

**Prerequisites**: Model weights downloaded to `./models/` (see `models/README.md` for download commands).

**Steps**:
1. Open app → ASR settings → select `Qwen3-ASR-0.6B`.
2. Click "Detect" button next to model path.

**Expected Result**:
- If `config.json` exists in the model directory, status shows "Model ready".
- If not, status shows "Model not found" with a download hint.
3. Repeat for Translator → `Hunyuan HY-MT1.5-1.8B`.

---

### Scenario 4: Subtitle Overlay Behavior

**Goal**: Verify two transparent subtitle windows appear, are draggable, and respect lock state.

**Steps**:
1. Open app → click "Start Translation" (engines need not be fully loaded; mock mode is acceptable for this test).
2. Two subtitle windows appear on screen.

**Expected Result**:
- Windows have no title bar, no border, fully transparent background.
- Text is visible (white original + slightly dimmed translation below).
- Dragging the window moves it; clicking the lock icon prevents further movement and mouse passthrough.
3. Resize the window via corner drag.

**Expected Result**:
- Font size scales proportionally with window height.
- Window position and size are remembered in `AppConfig.subtitle_window_positions`.

---

### Scenario 5: Audio Pipeline Integration (Mock Audio)

**Goal**: Verify the full pipeline (capture → ASR → translate → TTS → subtitle) using a recorded audio file instead of live microphone.

```bash
pytest tests/integration/test_pipeline.py -v
```

**Expected Result**:
- Test loads a 5-second WAV fixture from `tests/fixtures/sample_audio/hello_zh.wav`.
- Pipeline produces at least one `SubtitleEntry` with `original_text` containing the expected Chinese phrase.
- `translated_text` is non-empty and different from `original_text`.
- `latency_ms` is measured and falls below the 3000 ms budget for local models.
- All `AudioChunk` buffers are zeroed after processing (verified via mock spy).

---

### Scenario 6: Cloud Fallback & Retry

**Goal**: Verify exponential backoff and automatic downgrade when a cloud API fails.

**Steps**:
1. Configure translator = `OpenAI` with an intentionally invalid API key.
2. Set local backup translator = `Hunyuan` (model must be present).
3. Start translation with a mock audio fixture.

**Expected Result**:
- UI shows "Retrying 1/3..." with a spinner.
- After 3 retries (~7 s total), UI shows "Downgrading to local engine...".
- Translation continues using Hunyuan local model.
- Subtitle log contains entries with `engine_used: {"translator": "hunyuan"}`.

---

### Scenario 7: Audio Device Hot-Plug

**Goal**: Verify graceful pause/resume when the active microphone is unplugged.

**Steps**:
1. Start translation with a real microphone selected.
2. Physically unplug the microphone (or disable it in OS settings).

**Expected Result**:
- Outbound subtitle window shows "Microphone disconnected — paused".
- CPU usage drops (no ASR processing).
3. Re-plug the microphone.

**Expected Result**:
- App detects the device return within 5 seconds.
- Subtitle window automatically resumes without user intervention.

---

## Performance Smoke Test

Run the following to verify resource usage stays within constitutional limits:

```bash
pytest tests/integration/test_pipeline.py -v -k "performance"
```

**Expected Result**:
- Peak RAM during model loading < 2.5 GB (allows transient spike).
- Steady-state RAM after 3 minutes < 2.0 GB.
- Average CPU during active processing < 30 % on a 4-core machine.

---

## Clean-Up

```bash
deactivate          # exit venv
rm -rf .venv        # if you want a fresh start
```
