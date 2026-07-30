"""JSON persistence for application settings."""

from __future__ import annotations

import contextlib
import json
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

    # Migrate legacy ASR backend → live_captions (推荐默认, 仅 Win11 22H2+)
    # 旧版本默认 "dashscope" 或 "local", 需 API Key 或下载模型.
    # Windows Live Captions 系统级 ASR, 零占用高准确率, 接近辅助字幕水平.
    legacy_backend = data.get("economy_asr_backend", "")
    if legacy_backend in ("", "dashscope"):
        data["economy_asr_backend"] = "live_captions"

    # Migrate legacy ASR model id → faster-whisper-medium (本地模型默认)
    # 旧版本默认 "auto" / SenseVoice / 双语模型, 对游戏实况噪声鲁棒性差.
    # faster-whisper (Whisper medium) 接近辅助字幕准确率, 噪声鲁棒性最好.
    legacy_local_model = data.get("economy_asr_local_model", "")
    if legacy_local_model in (
        "auto",
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
    ):
        data["economy_asr_local_model"] = "faster-whisper-medium"

    # Migrate legacy utterance params → 新默认值 (降低尾部静音, 减少 SenseVoice 幻听)
    # 旧默认 silence=450 / max=12000 会让过长的尾部静音送入模型, 触发语气词幻听.
    if data.get("economy_utterance_silence_ms") == 450:
        data["economy_utterance_silence_ms"] = 300
    if data.get("economy_utterance_max_ms") == 12000:
        data["economy_utterance_max_ms"] = 8000
    # soft_split_ms 旧默认 3000 太激进, 游戏原声每 3 秒切一次产生大量噪点片段;
    # 迁移到 5000 减少切分频率.
    if data.get("economy_utterance_soft_split_ms") in (None, 3000):
        data["economy_utterance_soft_split_ms"] = 5000

    # Best-effort: migrate old keyring volcengine secret into volc_api_key once
    if not data.get("volc_api_key"):
        legacy = keyring.get_password(_SERVICE_NAME, "volcengine")
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
            volc_api_key=str(translation.get("volc_app_id", "") or ""),
            volc_access_token=str(translation.get("volc_access_token", "") or ""),
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


def validate_config(config: AppConfigModel) -> tuple[bool, str]:
    """High-level save-time validation returning (ok, error_message)."""
    if config.source_language == config.target_language:
        return False, "源语言和目标语言不能相同"
    mode = getattr(config, "translation_mode", "volc") or "volc"
    if mode == "volc" and not (config.volc_api_key or "").strip():
        return False, "请填写火山 API Key"
    return True, ""
