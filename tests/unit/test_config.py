"""Unit tests for configuration validation."""

import json

import pytest

from src.core.exceptions import ConfigValidationError
from src.models.config import (
    DEFAULT_GAME_ASR_MODEL,
    DEFAULT_MIC_ASR_MODEL,
    AppConfigModel,
    default_asr_backend,
)
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
    assert config.translation_mode == "volc"
    assert config.economy_asr_backend == default_asr_backend()


@pytest.mark.parametrize(("is_windows", "expected"), [(True, "live_captions"), (False, "local")])
def test_asr_backend_defaults_per_platform(monkeypatch, is_windows: bool, expected: str) -> None:
    monkeypatch.setattr("sys.platform", "win32" if is_windows else "linux")
    assert AppConfigModel().economy_asr_backend == expected
    assert AppConfigModel(economy_asr_backend="").economy_asr_backend == expected
    stored = AppConfigModel(economy_asr_backend="live_captions").economy_asr_backend
    assert stored == ("live_captions" if is_windows else "local")


@pytest.mark.parametrize(
    ("field", "stored", "expected"),
    [
        ("vad_sensitivity", "extreme", "medium"),
        ("quality_preset", "", "balanced"),
        ("capture_backend", "wasapi", "auto"),
        ("dota_coach_mode", "TURBO", "normal"),
        ("quality_preset", "turbo", "turbo"),
    ],
)
def test_unknown_choice_values_fall_back_to_default(field: str, stored: str, expected: str) -> None:
    assert getattr(AppConfigModel(**{field: stored}), field) == expected


def test_load_config_drops_removed_keys_from_disk(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "source_language": "zh",
                "target_language": "en",
                "use_volc": False,
                "engine": {"name": "legacy"},
                "subtitle_font_size": 30,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)

    loaded = config_manager.load_config()

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert loaded.subtitle_font_size == 30
    assert saved["subtitle_font_size"] == 30
    assert "use_volc" not in saved
    assert "engine" not in saved


def test_missing_config_file_uses_model_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_manager, "_config_path", lambda: tmp_path / "config.json")
    assert config_manager.load_config() == AppConfigModel()


def test_languages_must_differ() -> None:
    with pytest.raises(ValueError, match="must be different"):
        AppConfigModel(source_language="zh", target_language="zh")


def test_invalid_font_size() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_font_size=5)


def test_invalid_opacity() -> None:
    with pytest.raises(ValueError):
        AppConfigModel(subtitle_opacity=1.5)


def test_fully_transparent_subtitle_plate_is_allowed() -> None:
    assert AppConfigModel(subtitle_opacity=0.0).subtitle_opacity == 0.0


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
    monkeypatch.setattr(
        config_manager.keyring,
        "get_password",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("no backend")),
    )

    loaded = config_manager.load_config()

    assert loaded.volc_api_key == ""


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
    original = AppConfigModel(
        translation_mode="economy",
        economy_asr_backend="local",
        economy_asr_mic_model="faster-whisper-medium",
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
    original = AppConfigModel(
        translation_mode="economy",
        economy_asr_backend="local",
        economy_asr_game_model="auto",
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
        economy_llm_api_key="llm-key",
        economy_openai_tts_api_key="tts-key",
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


@pytest.mark.parametrize(
    ("legacy", "mic", "game"),
    [
        ("auto", DEFAULT_MIC_ASR_MODEL, "auto"),
        ("faster-whisper-medium", "faster-whisper-medium", "faster-whisper-medium"),
        ("", DEFAULT_MIC_ASR_MODEL, DEFAULT_GAME_ASR_MODEL),
    ],
)
def test_legacy_single_asr_model_splits_per_channel(legacy: str, mic: str, game: str) -> None:
    cfg = AppConfigModel.model_validate({"economy_asr_local_model": legacy})
    assert (cfg.economy_asr_mic_model, cfg.economy_asr_game_model) == (mic, game)


def test_explicit_channel_models_win_over_legacy_field() -> None:
    cfg = AppConfigModel.model_validate(
        {"economy_asr_local_model": "auto", "economy_asr_mic_model": "qwen3-asr-0.6b"}
    )
    assert cfg.economy_asr_mic_model == "qwen3-asr-0.6b"
    assert cfg.economy_asr_game_model == "auto"


def test_invalid_field_keeps_other_settings_and_backs_up(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"duck_gain": 5, "source_language": "ja"}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)

    loaded = config_manager.load_config()

    assert loaded.source_language == "ja"
    assert loaded.duck_gain == AppConfigModel().duck_gain
    assert list(tmp_path.glob("config.json.bad-*"))


def test_unparseable_config_is_backed_up_before_failing(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)

    with pytest.raises(config_manager.ConfigValidationError):
        config_manager.load_config()

    (backup,) = tmp_path.glob("config.json.bad-*")
    assert backup.read_text(encoding="utf-8") == "{not json"


def test_stale_key_rewrite_keeps_secret_the_keyring_rejected(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"removed_setting": 1, "volc_api_key": "plain-key"}), encoding="utf-8"
    )
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)
    monkeypatch.setattr(config_manager, "_set_keyring_password", lambda *_a: False)

    loaded = config_manager.load_config()

    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert loaded.volc_api_key == "plain-key"
    assert on_disk["volc_api_key"] == "plain-key"
    assert "removed_setting" not in on_disk


def test_backup_leaves_out_secrets_already_in_keyring(tmp_path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"duck_gain": 5, "volc_api_key": "secret"}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "_config_path", lambda: cfg_file)

    config_manager.load_config()

    (backup,) = tmp_path.glob("config.json.bad-*")
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert saved["duck_gain"] == 5
    assert "volc_api_key" not in saved


def test_only_recent_bad_backups_are_kept(tmp_path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    for i in range(5):
        (tmp_path / f"config.json.bad-2020010{i}-000000").write_text("{}", encoding="utf-8")

    config_manager._backup_bad_config(cfg_file)

    assert len(list(tmp_path.glob("config.json.bad-*"))) == 3


def test_every_credential_field_is_stored_as_a_secret() -> None:
    markers = ("api_key", "access_token", "_iam_ak", "_iam_sk")
    credentials = {name for name in AppConfigModel.model_fields if any(m in name for m in markers)}

    assert credentials <= set(config_manager.SECRET_FIELDS)
