"""Unit tests for configuration validation."""

import json

import pytest

from src.core.exceptions import ConfigValidationError
from src.models.config import AppConfigModel
from src.utils import config_manager


class _MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch) -> _MemoryKeyring:
    backend = _MemoryKeyring()
    monkeypatch.setattr(config_manager, "keyring", backend)
    return backend


def test_default_config_is_valid() -> None:
    config = AppConfigModel()
    assert config.source_language == "zh"
    assert config.target_language == "en"
    assert config.use_volc is True
    assert config.translation_mode == "volc"
    assert config.economy_asr_backend == "live_captions"


def test_translation_mode_economy_sets_use_volc_false() -> None:
    config = AppConfigModel(translation_mode="economy")
    assert config.translation_mode == "economy"
    assert config.use_volc is False


def test_languages_must_differ() -> None:
    with pytest.raises(ValueError, match="must be different"):
        AppConfigModel(source_language="zh", target_language="zh")


def test_invalid_font_size() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_font_size=5)


def test_invalid_opacity() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_opacity=1.5)


def test_window_positions_validation() -> None:
    config = AppConfigModel(subtitle_window_positions={"outbound": (10, 20, 300, 100)})
    assert "outbound" in config.subtitle_window_positions


def test_invalid_window_position_key() -> None:
    with pytest.raises(ValueError, match="Invalid window position keys"):
        AppConfigModel(subtitle_window_positions={"unknown": (0, 0, 100, 100)})


def test_load_config_preserves_intentional_dashscope(tmp_path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source_language": "zh",
                "target_language": "en",
                "translation_mode": "economy",
                "economy_asr_backend": "dashscope",
                "economy_dashscope_api_key": "sk-keep",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    loaded = config_manager.load_config()
    assert loaded.economy_asr_backend == "dashscope"
    assert loaded.economy_dashscope_api_key == "sk-keep"


def test_load_config_maps_sherpa_whisper_aliases(tmp_path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source_language": "zh",
                "target_language": "en",
                "economy_asr_backend": "whisper",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    loaded = config_manager.load_config()
    assert loaded.economy_asr_backend == "local"


def test_validate_config_allows_economy_local_without_dashscope() -> None:
    ok, msg = config_manager.validate_config(
        AppConfigModel(
            translation_mode="economy",
            economy_asr_backend="local",
            economy_dashscope_api_key="",
        )
    )
    assert ok is True
    assert msg == ""


def test_load_config_ignores_unavailable_keyring(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"source_language": "zh", "target_language": "en"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    monkeypatch.setattr(
        config_manager.keyring,
        "get_password",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("no backend")),
    )

    loaded = config_manager.load_config()

    assert loaded.volc_api_key == ""


def test_legacy_yaml_app_id_is_not_used_as_api_key(tmp_path, monkeypatch) -> None:
    yaml_path = tmp_path / "config" / "default_config.yaml"
    yaml_path.parent.mkdir()
    yaml_path.write_text(
        "translation:\n  source_lang: zh\n  target_lang: en\n  volc_app_id: app-id-only\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    loaded = config_manager._seed_from_yaml_defaults()

    assert loaded is not None
    assert loaded.volc_api_key == ""
    assert loaded.volc_console_app_id == "app-id-only"


def test_merge_config_updates_preserves_engine_only_settings() -> None:
    original = AppConfigModel(
        economy_kokoro_speed=1.08,
        economy_sentence_min_chars=9,
        economy_sentence_pause_ms=1350,
        economy_sentence_max_wait_ms=3600,
        economy_utterance_soft_split_ms=7400,
        economy_utterance_soft_split_quiet_ms=420,
        economy_utterance_tail_rms=0.006,
    )

    updated = config_manager.merge_config_updates(original, subtitle_font_size=30)

    assert updated.subtitle_font_size == 30
    assert updated.economy_kokoro_speed == 1.08
    assert updated.economy_sentence_min_chars == 9
    assert updated.economy_sentence_pause_ms == 1350
    assert updated.economy_sentence_max_wait_ms == 3600
    assert updated.economy_utterance_soft_split_ms == 7400
    assert updated.economy_utterance_soft_split_quiet_ms == 420
    assert updated.economy_utterance_tail_rms == 0.006


def test_new_economy_settings_survive_save_load_round_trip(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    original = AppConfigModel(
        translation_mode="economy",
        economy_asr_backend="local",
        economy_asr_local_model="faster-whisper-medium",
        economy_kokoro_voice_en="am_michael",
        economy_kokoro_speed=1.08,
        economy_sentence_min_chars=9,
        economy_sentence_pause_ms=1350,
        economy_sentence_max_wait_ms=3600,
        economy_utterance_soft_split_ms=7400,
        economy_utterance_soft_split_quiet_ms=420,
        economy_utterance_tail_rms=0.006,
    )

    config_manager.save_config(original)
    loaded = config_manager.load_config()

    assert loaded == original


def test_explicit_legacy_looking_values_are_not_migrated_after_save(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda c: c)
    original = AppConfigModel(
        translation_mode="economy",
        economy_asr_backend="local",
        economy_asr_local_model="auto",
        economy_kokoro_voice_en="af_heart",
        economy_utterance_silence_ms=450,
        economy_utterance_max_ms=12000,
        economy_utterance_soft_split_ms=5000,
    )

    config_manager.save_config(original)
    loaded = config_manager.load_config()

    assert loaded == original


def test_merge_config_updates_revalidates_values() -> None:
    config = AppConfigModel(source_language="zh", target_language="en")

    with pytest.raises(ValueError, match="must be different"):
        config_manager.merge_config_updates(config, source_language="en")


def test_save_stores_secrets_only_in_keyring(tmp_path, monkeypatch, memory_keyring) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    config = AppConfigModel(
        volc_api_key="volc-key",
        volc_access_token="volc-token",
        volc_iam_ak="iam-ak",
        volc_iam_sk="iam-sk",
        economy_dashscope_api_key="dashscope-key",
    )

    config_manager.save_config(config)

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    for field, username in config_manager._SECRET_FIELDS.items():
        assert field not in saved
        assert memory_keyring.get_password(config_manager._SERVICE_NAME, username) == getattr(
            config, field
        )


def test_load_migrates_plaintext_secrets_and_scrubs_json(
    tmp_path, monkeypatch, memory_keyring
) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source_language": "zh",
                "target_language": "en",
                "volc_api_key": "legacy-volc",
                "economy_dashscope_api_key": "legacy-dashscope",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_fill_volc_from_yaml", lambda config: config)

    loaded = config_manager.load_config()

    assert loaded.volc_api_key == "legacy-volc"
    assert loaded.economy_dashscope_api_key == "legacy-dashscope"
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "volc_api_key" not in saved
    assert "economy_dashscope_api_key" not in saved
    assert (
        memory_keyring.get_password(config_manager._SERVICE_NAME, "volc_api_key") == "legacy-volc"
    )


def test_save_fails_without_secure_storage(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_set_keyring_password", lambda *_args: False)

    with pytest.raises(ConfigValidationError, match="系统凭据管理器"):
        config_manager.save_config(AppConfigModel(volc_api_key="must-not-leak"))

    assert not cfg_file.exists()


def test_blank_secret_preserves_existing_keyring_value(
    tmp_path, monkeypatch, memory_keyring
) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    memory_keyring.set_password(config_manager._SERVICE_NAME, "volc_api_key", "keep-me")

    config_manager.save_config(AppConfigModel(volc_api_key=""))

    assert memory_keyring.get_password(config_manager._SERVICE_NAME, "volc_api_key") == "keep-me"


def test_explicit_secret_clear_deletes_keyring_value(tmp_path, monkeypatch, memory_keyring) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    memory_keyring.set_password(config_manager._SERVICE_NAME, "volc_api_key", "delete-me")

    config_manager.save_config(
        AppConfigModel(volc_api_key=""),
        clear_secret_fields={"volc_api_key"},
    )

    assert memory_keyring.get_password(config_manager._SERVICE_NAME, "volc_api_key") is None
