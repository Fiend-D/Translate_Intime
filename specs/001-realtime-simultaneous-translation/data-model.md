# Data Model: Realtime Simultaneous Translation

## Overview

All domain entities are immutable-ish dataclasses (Pydantic `BaseModel` or Python `@dataclass(frozen=True)`). Mutable state lives only in the `TranslationSession` orchestrator and Qt GUI widgets.

---

## Entities

### `TranslationSession`

Represents a single active simultaneous-interpretation session.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `session_id` | `UUID` | Auto-generated | Unique identifier |
| `status` | `SessionStatus` | Enum: `IDLE`, `STARTING`, `RUNNING`, `PAUSED`, `STOPPED` | Lifecycle state |
| `outbound_enabled` | `bool` | Default `True` | Whether to process user microphone audio |
| `inbound_enabled` | `bool` | Default `True` | Whether to process counterparty loopback audio |
| `source_language` | `LanguageCode` | Enum: `zh`, `en`, `ja`, `ko` | User's spoken language (outbound source / inbound target) |
| `target_language` | `LanguageCode` | Enum: `zh`, `en`, `ja`, `ko` | Target for outbound; source for inbound |
| `asr_engine_id` | `str` | Non-empty | Selected ASR engine identifier (e.g., `qwen-asr`, `whisper-cloud`) |
| `translator_engine_id` | `str` | Non-empty | Selected translator engine identifier (e.g., `hunyuan`, `openai`) |
| `tts_engine_id` | `str` | Non-empty | Selected TTS engine identifier (e.g., `chattts`, `openai-tts`) |
| `start_time` | `datetime` | ISO-8601 | When the session entered `RUNNING` |
| `subtitle_windows_locked` | `dict[Direction, bool]` | Default `{OUTBOUND: False, INBOUND: False}` | Per-direction lock state |
| `subtitle_window_positions` | `dict[Direction, WindowPosition]` | Optional | Last known screen coordinates per direction |

**State Transitions**:
```
IDLE ──[start()]──> STARTING ──[engines_ready]──> RUNNING
RUNNING ──[pause()]──> PAUSED ──[resume()]──> RUNNING
RUNNING ──[stop()]──> STOPPED
PAUSED  ──[stop()]──> STOPPED
STARTING ──[cancel()]──> IDLE
```

**Validation**:
- `source_language` and `target_language` MUST be different.
- At least one of `outbound_enabled` or `inbound_enabled` MUST be `True`.

---

### `SubtitleEntry`

A single bilingual subtitle line produced by the pipeline.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `entry_id` | `UUID` | Auto-generated | Unique identifier |
| `timestamp` | `datetime` | ISO-8601, UTC | When the subtitle was generated |
| `direction` | `Direction` | Enum: `OUTBOUND`, `INBOUND` | Which audio stream produced this entry |
| `original_text` | `str` | Max 500 chars, non-empty | ASR-transcribed text |
| `translated_text` | `str` | Max 500 chars, non-empty | Translated text |
| `displayed` | `bool` | Default `False` | Whether the GUI has shown this entry (for dedup) |

**Validation**:
- Both `original_text` and `translated_text` MUST be non-empty strings.
- `original_text` MUST NOT equal `translated_text` when source and target languages differ (sanity check, not strict).

---

### `EngineConfig`

User-selected engine preferences persisted across restarts.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `asr_backend` | `BackendType` | Enum: `LOCAL`, `CLOUD` | ASR execution mode |
| `asr_model` | `str` | Required if `LOCAL` | Path or HuggingFace repo id for local ASR |
| `asr_api_key` | `SecretStr` | Required if `CLOUD` | Cloud ASR API key (masked, stored in keyring) |
| `translator_backend` | `BackendType` | Enum: `LOCAL`, `CLOUD` | Translation execution mode |
| `translator_model` | `str` | Required if `LOCAL` | Path or HuggingFace repo id for local translator |
| `translator_api_key` | `SecretStr` | Required if `CLOUD` | Cloud translation API key (masked, stored in keyring) |
| `translator_api_url` | `HttpUrl` | Optional | Custom base URL for OpenAI-compatible APIs |
| `tts_backend` | `BackendType` | Enum: `LOCAL`, `CLOUD` | TTS execution mode |
| `tts_model` | `str` | Required if `LOCAL` | Path or HuggingFace repo id for local TTS |
| `tts_voice` | `str` | Optional | Voice identifier (cloud-only, e.g., `alloy`) |
| `tts_api_key` | `SecretStr` | Required if `CLOUD` | Cloud TTS API key (masked, stored in keyring) |

**Validation Rules**:
- If `backend == LOCAL`, the corresponding `model` field MUST point to an existing directory containing `config.json` (validated at save time, not at model load time).
- If `backend == CLOUD`, the corresponding `api_key` field MUST be non-empty.
- `api_key` values are NEVER serialized to JSON on disk; they are read from / written to the OS keyring via `keyring.get_password()` / `set_password()`.

---

### `AppConfig`

Top-level application settings singleton.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `version` | `int` | Default `1` | Config schema version for future migrations |
| `engine` | `EngineConfig` | Required | ASR / Translator / TTS selections |
| `source_language` | `LanguageCode` | Default `zh` | Default outbound source / inbound target |
| `target_language` | `LanguageCode` | Default `en` | Default outbound target / inbound source |
| `subtitle_font_size` | `int` | Range 12–48, default `16` | Base font size for subtitle overlays |
| `subtitle_opacity` | `float` | Range 0.3–1.0, default `0.9` | Text opacity (background remains fully transparent) |
| `log_dir` | `Path` | Writable directory, default `~/.config/translator_intime/logs` | Subtitle log storage |
| `model_cache_dir` | `Path` | Writable directory, default `./models` | Local model download/cache root |
| `debug_mode` | `bool` | Default `False` | Show performance metrics and extra logging |

**Validation**:
- `log_dir` and `model_cache_dir` MUST be writable. If they do not exist, the app creates them on first launch.
- `subtitle_font_size` and `subtitle_opacity` are clamped to their ranges on load.

---

### `AudioChunk`

Internal data carrier for raw audio buffers (not persisted).

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `chunk_id` | `UUID` | Auto-generated | Unique identifier |
| `direction` | `Direction` | Enum: `OUTBOUND`, `INBOUND` | Source stream |
| `timestamp` | `datetime` | ISO-8601, UTC | Capture start time |
| `sample_rate` | `int` | 8000–48000, default `16000` | Sample rate in Hz |
| `channels` | `int` | 1 or 2, default `1` | Mono or stereo |
| `data` | `bytes` | Non-empty | Raw PCM audio buffer |

**Invariants**:
- `data` length MUST be consistent with `sample_rate`, `channels`, and the expected chunk duration (2.5 s → `sample_rate * channels * 2` bytes for 16-bit PCM).
- After processing, the buffer MUST be explicitly zeroed (`audio_utils.secure_clear(data)`) before garbage collection to prevent sensitive audio from lingering in memory.

---

### `PipelineResult`

Internal result of a single ASR → Translate → TTS pass (not persisted).

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `result_id` | `UUID` | Auto-generated | Unique identifier |
| `direction` | `Direction` | Enum | Source stream |
| `original_text` | `str` | Non-empty | ASR output |
| `translated_text` | `str` | Non-empty | Translation output |
| `tts_audio` | `bytes`  `None` | Optional | Synthesized audio PCM (may be `None` if TTS is disabled or failed) |
| `latency_ms` | `int` | >= 0 | Measured E2E processing time for this chunk |
| `engine_used` | `dict[str, str]` | e.g., `{"asr": "qwen-asr", "translator": "hunyuan", "tts": "chattts"}` | Snapshot of which engines produced this result |

---

## Enumerations

```python
class SessionStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"

class Direction(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"

class BackendType(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"

class LanguageCode(str, Enum):
    ZH = "zh"
    EN = "en"
    JA = "ja"
    KO = "ko"
```

---

## Relationships

```
AppConfig 1──1 EngineConfig
TranslationSession 1──* SubtitleEntry (produces many over time)
TranslationSession 1──1 EngineConfig (references current selection)
AudioChunk 1──1 PipelineResult (transformed into)
PipelineResult 1──0..1 SubtitleEntry (if subtitle overlay is enabled)
```

---

## Persistence

| Entity | Storage | Format | Notes |
|--------|---------|--------|-------|
| `AppConfig` | `~/.config/translator_intime/config.json` | JSON | `api_key` fields excluded; read from keyring on load |
| `SubtitleEntry` | `~/.config/translator_intime/logs/subtitles_YYYY-MM-DD_HH-MM-SS.txt` | Plain text, one line per entry | Rotated daily or at 10 MB |
| `TranslationSession` | In-memory only | — | Re-created on each app launch |
| `AudioChunk` / `PipelineResult` | In-memory only | — | Zeroed after processing |
