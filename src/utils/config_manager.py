"""JSON persistence for application settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.core.exceptions import ConfigValidationError
from src.models.config import AppConfigModel

_SERVICE_NAME = "translator_intime"

try:
    import keyring as _keyring
except ImportError:  # pragma: no cover - optional dependency
    _keyring = None


class _NullKeyring:
    @staticmethod
    def get_password(service: str, username: str) -> str | None:
        return None

    @staticmethod
    def set_password(service: str, username: str, password: str) -> None:
        return None

    @staticmethod
    def delete_password(service: str, username: str) -> None:
        return None


class _NullKeyringErrors:
    class PasswordDeleteError(Exception):
        pass


keyring: Any = _keyring if _keyring is not None else _NullKeyring()
if _keyring is None:
    keyring.errors = _NullKeyringErrors()


def _get_keyring_password(username: str) -> str | None:
    """Read a legacy secret without making configuration loading depend on keyring."""
    try:
        return keyring.get_password(_SERVICE_NAME, username)
    except Exception:
        return None


def _config_path() -> Path:
    path = Path.home() / ".config" / "translator_intime"
    path.mkdir(parents=True, exist_ok=True)
    return path / "config.json"


def load_config() -> AppConfigModel:
    """Load configuration from disk."""
    config_file = _config_path()
    if not config_file.exists():
        seeded = _seed_from_yaml_defaults()
        return seeded if seeded is not None else AppConfigModel()

    try:
        with config_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"Config file is not valid JSON: {exc}") from exc

    # Drop legacy local-engine block if present
    data.pop("engine", None)
    data.pop("model_cache_dir", None)

    # Migrate missing ASR backend → platform default.
    # Windows: live_captions; Linux/macOS: local (Live Captions is Win-only).
    # Do NOT rewrite intentional "dashscope" — that broke cloud ASR after save/reload
    # and made the UI look local while can_start still demanded a DashScope key
    # when combo selection failed to match aliases.
    legacy_backend = data.get("economy_asr_backend", None)
    if legacy_backend in (None, ""):
        data["economy_asr_backend"] = "live_captions" if sys.platform.startswith("win") else "local"
    elif legacy_backend == "live_captions" and not sys.platform.startswith("win"):
        # Persisted Win-only default is useless on Linux — fall back to local ASR.
        data["economy_asr_backend"] = "local"
    elif legacy_backend in ("sherpa", "whisper"):
        # UI combo only exposes "local"; keep model choice via economy_asr_local_model.
        data["economy_asr_backend"] = "local"

    # Only fill a missing local model. ``auto`` / SenseVoice / Zipformer remain
    # valid user-selectable options and must survive save → reload unchanged.
    if not data.get("economy_asr_local_model"):
        data["economy_asr_local_model"] = "faster-whisper-medium"

    # New defaults apply only when a field is absent. Value-based migrations
    # cannot distinguish an old default from a deliberate advanced setting.
    if data.get("economy_utterance_soft_split_ms") is None:
        data["economy_utterance_soft_split_ms"] = 6000
    if data.get("economy_utterance_soft_split_quiet_ms") is None:
        data["economy_utterance_soft_split_quiet_ms"] = 280
    if data.get("economy_kokoro_speed") is None:
        data["economy_kokoro_speed"] = 0.92
    if not data.get("economy_kokoro_voice_en"):
        data["economy_kokoro_voice_en"] = "af_bella"

    # Best-effort: migrate old keyring volcengine secret into volc_api_key once
    if not data.get("volc_api_key"):
        legacy = _get_keyring_password("volcengine")
        if legacy:
            data["volc_api_key"] = legacy

    try:
        config = AppConfigModel(**data)
    except ValidationError as exc:
        raise ConfigValidationError(f"Config validation failed: {exc}") from exc
    return _fill_volc_from_yaml(config)


def _fill_volc_from_yaml(config: AppConfigModel) -> AppConfigModel:
    """If JSON config lacks Volc key, pull from legacy YAML once."""
    if config.volc_api_key:
        return config
    seeded = _seed_from_yaml_defaults()
    if seeded is None or not seeded.volc_api_key:
        return config
    config.volc_api_key = seeded.volc_api_key
    if not config.volc_access_token and seeded.volc_access_token:
        config.volc_access_token = seeded.volc_access_token
    return config


def _seed_from_yaml_defaults() -> AppConfigModel | None:
    """Best-effort import from legacy config/default_config.yaml on first run."""
    yaml_path = Path("config/default_config.yaml")
    if not yaml_path.exists():
        return None
    try:
        import yaml

        with yaml_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return None

    ui = raw.get("ui", {}) or {}
    translation = raw.get("translation", {}) or {}
    audio = raw.get("audio", {}) or {}
    try:
        font_size = int(ui.get("font_size", 22) or 22)
        font_size = max(12, min(64, font_size))
        return AppConfigModel(
            source_language=translation.get("source_lang", "zh"),
            target_language=translation.get("target_lang", "en"),
            subtitle_font_size=font_size,
            subtitle_opacity=float(ui.get("subtitle_opacity", 0.88) or 0.88),
            enable_mic=bool(ui.get("enable_mic", True)),
            enable_game=bool(ui.get("enable_game", True)),
            show_mic_subtitle=bool(ui.get("show_mic_subtitle", True)),
            show_game_subtitle=bool(ui.get("show_game_subtitle", True)),
            play_mic_voice=bool(ui.get("play_outbound_voice", False)),
            play_game_voice=bool(ui.get("play_inbound_voice", ui.get("play_chinese_voice", False))),
            input_device=audio.get("input_device"),
            output_device=audio.get("output_device"),
            loopback_device=audio.get("game_output_device"),
            use_volc=True,
            # A legacy App ID is not an AST 2.0 API key. Preserve it only for
            # display/migration instead of letting credential checks accept it.
            volc_api_key=str(translation.get("volc_api_key", "") or ""),
            volc_access_token=str(translation.get("volc_access_token", "") or ""),
            volc_console_app_id=str(translation.get("volc_app_id", "") or ""),
            theme_mode="dark",
        )
    except Exception:
        return None


def save_config(config: AppConfigModel) -> None:
    """Persist configuration to disk."""
    data = config.model_dump(mode="json")
    config_file = _config_path()
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        if not config_file.parent.exists():
            raise
    with config_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def merge_config_updates(config: AppConfigModel, **updates: Any) -> AppConfigModel:
    """Apply UI changes without dropping settings unknown to that UI version.

    The settings window only owns a subset of the configuration. Starting from
    the complete current model means newly-added engine tuning fields survive an
    unrelated Save click even before dedicated controls are added for them.
    Re-validating the merged mapping also avoids ``model_copy(update=...)``
    silently accepting invalid values.
    """
    data = config.model_dump(mode="python")
    data.update(updates)
    return AppConfigModel.model_validate(data)


def validate_config(config: AppConfigModel) -> tuple[bool, str]:
    """High-level save-time validation returning (ok, error_message)."""
    if config.source_language == config.target_language:
        return False, "源语言和目标语言不能相同"
    mode = getattr(config, "translation_mode", "volc") or "volc"
    if mode == "volc" and not (config.volc_api_key or "").strip():
        return False, "请填写火山 API Key"
    return True, ""
