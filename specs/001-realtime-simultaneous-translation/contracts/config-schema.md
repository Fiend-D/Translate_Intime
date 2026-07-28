# Contract: Configuration JSON Schema

## Purpose

Defines the on-disk JSON format for `AppConfig` (excluding secrets). This contract allows manual editing, version migration, and backward-compatibility checks.

## File Location

`~/.config/translator_intime/config.json`

## Schema (Pydantic v2)

```python
from pydantic import BaseModel, Field, field_validator
from pathlib import Path

class EngineConfigModel(BaseModel):
    asr_backend: str = Field(..., pattern="^(local|cloud)$")
    asr_model: str | None = None
    translator_backend: str = Field(..., pattern="^(local|cloud)$")
    translator_model: str | None = None
    translator_api_url: str | None = None
    tts_backend: str = Field(..., pattern="^(local|cloud)$")
    tts_model: str | None = None
    tts_voice: str | None = None

class AppConfigModel(BaseModel):
    version: int = Field(default=1, ge=1)
    engine: EngineConfigModel
    source_language: str = Field(default="zh", pattern="^(zh|en|ja|ko)$")
    target_language: str = Field(default="en", pattern="^(zh|en|ja|ko)$")
    subtitle_font_size: int = Field(default=16, ge=12, le=48)
    subtitle_opacity: float = Field(default=0.9, ge=0.3, le=1.0)
    log_dir: str = Field(default="~/.config/translator_intime/logs")
    model_cache_dir: str = Field(default="./models")
    debug_mode: bool = Field(default=False)

    @field_validator("target_language")
    def languages_must_differ(cls, v: str, info) -> str:
        if v == info.data.get("source_language"):
            raise ValueError("source_language and target_language must be different")
        return v

    @field_validator("log_dir", "model_cache_dir")
    def expand_and_validate_path(cls, v: str) -> str:
        path = Path(v).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError(f"Path is not a directory: {path}")
        return str(path)
```

## Example JSON

```json
{
  "version": 1,
  "engine": {
    "asr_backend": "local",
    "asr_model": "./models/Qwen3-ASR-0.6B",
    "translator_backend": "local",
    "translator_model": "./models/HY-MT1.5-1.8B",
    "tts_backend": "local",
    "tts_model": "./models/ChatTTS",
    "tts_voice": null
  },
  "source_language": "zh",
  "target_language": "en",
  "subtitle_font_size": 16,
  "subtitle_opacity": 0.9,
  "log_dir": "~/.config/translator_intime/logs",
  "model_cache_dir": "./models",
  "debug_mode": false
}
```

## Secret Handling

- `api_key` fields are **absent** from this JSON schema.
- At load time, the application queries the OS keyring for each cloud backend using the service name `translator_intime/<backend>` (e.g., `translator_intime/openai`).
- At save time, any non-empty API key entered in the GUI is written to the keyring, and the JSON on disk remains unchanged.

## Version Migration

| Schema Version | Migration Rule |
|----------------|----------------|
| `1` (current) | Baseline |
| Future `2` | If `version` is missing, treat as `1`; apply `migrations/v1_to_v2.py` if needed |
