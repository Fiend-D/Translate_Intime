"""JSON persistence for application settings."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.core.exceptions import ConfigValidationError
from src.models.config import AppConfigModel
from src.utils.resource_paths import bundled_path

_SERVICE_NAME = "translator_intime"
_SECRET_FIELDS = {
    "volc_api_key": "volc_api_key",
    "volc_access_token": "volc_access_token",
    "volc_iam_ak": "volc_iam_ak",
    "volc_iam_sk": "volc_iam_sk",
    "economy_dashscope_api_key": "economy_dashscope_api_key",
}
_LEGACY_DOTA_COACH_HOTKEY = "<ctrl>+<alt>+c"
_DOTA_COACH_HOTKEY = "<ctrl>+<alt>+k"

_keyring: Any
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
        raise RuntimeError("No system keyring backend is available")

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
    """Read a secret without making configuration loading depend on keyring."""
    try:
        value = keyring.get_password(_SERVICE_NAME, username)
        return str(value) if value is not None else None
    except Exception:
        return None


def _set_keyring_password(username: str, password: str) -> bool:
    try:
        keyring.set_password(_SERVICE_NAME, username, password)
        return bool(keyring.get_password(_SERVICE_NAME, username) == password)
    except Exception:
        return False


def _delete_keyring_password(username: str) -> None:
    with contextlib.suppress(Exception):
        keyring.delete_password(_SERVICE_NAME, username)


def _without_secrets(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in _SECRET_FIELDS}


def _write_config_data(config_file: Path, data: dict[str, Any]) -> None:
    """Atomically replace the public JSON settings file."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = config_file.with_suffix(config_file.suffix + ".tmp")
    try:
        with temp_file.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp_file.replace(config_file)
    finally:
        temp_file.unlink(missing_ok=True)


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
    _migrate_hotkey_defaults(data)

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

    plaintext_secrets = {field: str(data.pop(field, "") or "").strip() for field in _SECRET_FIELDS}
    migration_succeeded = True
    found_plaintext = False
    for field, username in _SECRET_FIELDS.items():
        secret = _get_keyring_password(username)
        if not secret and field == "volc_api_key":
            secret = _get_keyring_password("volcengine")
        plaintext = plaintext_secrets[field]
        if not secret and plaintext:
            found_plaintext = True
            if _set_keyring_password(username, plaintext):
                secret = plaintext
            else:
                migration_succeeded = False
                secret = plaintext
        if secret:
            data[field] = secret

    # Remove legacy plaintext only after every discovered secret reached the
    # system credential store. A backend outage must not destroy credentials.
    if found_plaintext and migration_succeeded:
        _write_config_data(config_file, _without_secrets(data))

    try:
        config = AppConfigModel(**data)
    except ValidationError as exc:
        raise ConfigValidationError(f"Config validation failed: {exc}") from exc
    return _fill_volc_from_yaml(config)


def _migrate_hotkey_defaults(data: dict[str, Any]) -> None:
    """Replace the old console-interrupt shortcut without touching custom bindings."""
    hotkeys = data.get("hotkeys")
    if not isinstance(hotkeys, dict):
        return
    current = str(hotkeys.get("dota_coach_ask", "")).strip().lower().replace(" ", "")
    if current == _LEGACY_DOTA_COACH_HOTKEY:
        hotkeys["dota_coach_ask"] = _DOTA_COACH_HOTKEY


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
    if not yaml_path.is_file():
        yaml_path = bundled_path("config", "default_config.yaml")
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


def save_config(
    config: AppConfigModel,
    *,
    clear_secret_fields: Collection[str] = (),
) -> None:
    """Persist settings, preserving unavailable secrets unless explicitly cleared."""
    data = config.model_dump(mode="json")
    config_file = _config_path()
    invalid_clear_fields = set(clear_secret_fields) - set(_SECRET_FIELDS)
    if invalid_clear_fields:
        raise ConfigValidationError(
            f"Unknown secret fields: {', '.join(sorted(invalid_clear_fields))}"
        )
    for field, username in _SECRET_FIELDS.items():
        secret = str(data.get(field, "") or "").strip()
        if secret:
            if not _set_keyring_password(username, secret):
                raise ConfigValidationError(f"无法将 {field} 保存到系统凭据管理器；配置未写入。")
        elif field in clear_secret_fields:
            _delete_keyring_password(username)
    _write_config_data(config_file, _without_secrets(data))


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
