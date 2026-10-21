"""JSON persistence for application settings."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.core.exceptions import ConfigValidationError
from src.models.config import AppConfigModel
from src.utils.logger import logger

_SERVICE_NAME = "translator_intime"
_SECRET_FIELDS = {
    "volc_api_key": "volc_api_key",
    "volc_access_token": "volc_access_token",
    "volc_iam_ak": "volc_iam_ak",
    "volc_iam_sk": "volc_iam_sk",
    "economy_dashscope_api_key": "economy_dashscope_api_key",
    "economy_llm_api_key": "economy_llm_api_key",
    "economy_openai_tts_api_key": "economy_openai_tts_api_key",
}
SECRET_FIELDS: tuple[str, ...] = tuple(_SECRET_FIELDS)
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
        with contextlib.suppress(OSError):
            temp_file.chmod(0o600)
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
        return AppConfigModel()

    try:
        with config_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("top level is not an object", "", 0)
    except json.JSONDecodeError as exc:
        backup = _backup_bad_config(config_file)
        raise ConfigValidationError(
            f"Config file is not valid JSON: {exc}; original kept at {backup}"
        ) from exc

    stale_keys = set(data) - set(AppConfigModel.model_fields)
    _migrate_hotkey_defaults(data)

    # Null/empty values from old versions fall back to the model defaults.
    for field in (
        "economy_utterance_soft_split_ms",
        "economy_utterance_soft_split_quiet_ms",
        "economy_kokoro_speed",
        "economy_kokoro_voice_en",
    ):
        if data.get(field) in (None, ""):
            data.pop(field, None)

    plaintext_secrets = {field: str(data.pop(field, "") or "").strip() for field in _SECRET_FIELDS}
    # Plaintext secrets the keyring could not take; they must survive any rewrite.
    unmigrated: dict[str, str] = {}
    found_plaintext = False
    for field, username in _SECRET_FIELDS.items():
        secret = _get_keyring_password(username)
        if not secret and field == "volc_api_key":
            secret = _get_keyring_password("volcengine")
        plaintext = plaintext_secrets[field]
        if not secret and plaintext:
            found_plaintext = True
            if not _set_keyring_password(username, plaintext):
                unmigrated[field] = plaintext
            secret = plaintext
        if secret:
            data[field] = secret

    # Remove legacy plaintext only after every discovered secret reached the
    # system credential store. A backend outage must not destroy credentials.
    if found_plaintext and not unmigrated:
        _write_config_data(config_file, _without_secrets(data))

    try:
        config = AppConfigModel(**data)
    except ValidationError as exc:
        config = _salvage_config(config_file, data, exc, keep_secrets=unmigrated)
    if stale_keys:
        # Settings removed from the model stay on disk until rewritten.
        public = _without_secrets(config.model_dump(mode="json"))
        public.update(unmigrated)
        with contextlib.suppress(OSError):
            _write_config_data(config_file, public)
    return config


_MAX_BAD_BACKUPS = 3


def _backup_bad_config(
    config_file: Path, data: dict[str, Any] | None = None, *, keep_secrets: Collection[str] = ()
) -> Path | None:
    """Keep a copy of a rejected config; secrets already in the keyring are left out."""
    backup = config_file.with_name(f"{config_file.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        if data is None:
            shutil.copy2(config_file, backup)
        else:
            redacted = {
                key: value
                for key, value in data.items()
                if key not in _SECRET_FIELDS or key in keep_secrets
            }
            backup.write_text(json.dumps(redacted, indent=2, ensure_ascii=False), "utf-8")
        with contextlib.suppress(OSError):
            backup.chmod(0o600)
    except OSError as exc:
        logger.warning(f"无法备份损坏的配置文件: {exc}")
        return None
    old = sorted(config_file.parent.glob(f"{config_file.name}.bad-*"))
    for stale in old[:-_MAX_BAD_BACKUPS]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return backup


def _salvage_config(
    config_file: Path,
    data: dict[str, Any],
    exc: ValidationError,
    *,
    keep_secrets: Collection[str] = (),
) -> AppConfigModel:
    """Keep every valid setting; invalid fields fall back to defaults (original backed up)."""
    bad = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
    kept = {key: value for key, value in data.items() if key not in bad}
    try:
        config = AppConfigModel(**kept)
    except ValidationError as again:
        raise ConfigValidationError(f"Config validation failed: {again}") from exc
    backup = _backup_bad_config(config_file, data, keep_secrets=keep_secrets)
    logger.warning(f"配置项无效已恢复默认: {', '.join(sorted(bad))}；原文件备份在 {backup}")
    return config


def _migrate_hotkey_defaults(data: dict[str, Any]) -> None:
    """Replace the old console-interrupt shortcut without touching custom bindings."""
    hotkeys = data.get("hotkeys")
    if not isinstance(hotkeys, dict):
        return
    current = str(hotkeys.get("dota_coach_ask", "")).strip().lower().replace(" ", "")
    if current == _LEGACY_DOTA_COACH_HOTKEY:
        hotkeys["dota_coach_ask"] = _DOTA_COACH_HOTKEY


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
