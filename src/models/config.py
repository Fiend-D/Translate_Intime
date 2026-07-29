"""Application configuration model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HotkeyConfig(BaseModel):
    enabled: bool = True
    toggle_mic: str = "<ctrl>+<alt>+m"
    toggle_game: str = "<ctrl>+<alt>+g"
    stop_all: str = "<ctrl>+<alt>+s"
    toggle_mic_overlay: str = "<ctrl>+<alt>+1"
    toggle_game_overlay: str = "<ctrl>+<alt>+2"
    toggle_all_overlays: str = "<ctrl>+<alt>+h"
    music_play_pause: str = "<ctrl>+<alt>+p"
    music_stop: str = "<ctrl>+<alt>+x"
    music_prev: str = "<ctrl>+<alt>+<page_up>"
    music_next: str = "<ctrl>+<alt>+<page_down>"
    music_toggle_sidebar: str = "<ctrl>+<alt>+b"
    dota_coach_ask: str = "<ctrl>+<alt>+c"


class AppConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_language: str = "zh"
    target_language: str = "en"
    subtitle_font_size: int = Field(default=22, ge=12, le=72)
    subtitle_opacity: float = Field(default=0.88, ge=0.3, le=1.0)
    log_dir: str = "logs"
    debug_mode: bool = False
    subtitle_window_positions: dict[str, tuple[int, int, int, int]] = Field(
        default_factory=dict
    )

    enable_mic: bool = True
    enable_game: bool = True
    show_mic_subtitle: bool = True
    show_game_subtitle: bool = True
    play_mic_voice: bool = False
    play_game_voice: bool = False
    input_device: int | str | None = None
    output_device: int | str | None = None
    loopback_device: int | str | None = None

    use_volc: bool = True
    volc_api_key: str = ""
    volc_access_token: str = ""
    volc_speaker_id: str = ""
    volc_speech_rate: int = Field(default=0, ge=-50, le=100)
    volc_iam_ak: str = ""
    volc_iam_sk: str = ""
    volc_console_app_id: str = ""
    volc_session_rotate_minutes: int = Field(default=12, ge=0, le=240)

    hotwords: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    subtitle_history_lines: int = Field(default=2, ge=0, le=40)
    show_original_in_overlay: bool = True
    overlay_locked: bool = False
    theme_mode: Literal["dark", "light", "system"] = "dark"
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)

    music_folder: str = ""
    music_auto_next: bool = True
    show_advanced_devices: bool = False
    capture_backend: str = "auto"
    original_audio: Literal["duck", "mute", "mix"] = "mix"
    duck_gain: float = Field(default=0.2, ge=0.0, le=1.0)

    vad_enabled: bool = True
    vad_game_enabled: bool = True
    vad_backend: Literal["auto", "silero", "rms"] = "auto"
    vad_sensitivity: str = "medium"
    vad_open_ms: int = Field(default=80, ge=20, le=2000)
    vad_hangover_ms: int = Field(default=600, ge=20, le=5000)
    vad_barge_in_ms: int = Field(default=200, ge=40, le=2000)
    vad_preroll_ms: int = Field(default=300, ge=0, le=2000)
    quality_preset: str = "balanced"

    dota_coach_enabled: bool = True
    dota_coach_url: str = ""
    dota_coach_mode: str = "normal"
    dota_coach_arm_seconds: int = Field(default=12, ge=1, le=120)

    @field_validator("subtitle_window_positions", mode="before")
    @classmethod
    def _coerce_window_positions(cls, value: Any) -> dict[str, tuple[int, int, int, int]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("subtitle_window_positions must be a dict")
        allowed = {"outbound", "inbound"}
        invalid = set(value) - allowed
        if invalid:
            raise ValueError(f"Invalid window position keys: {', '.join(sorted(invalid))}")
        result: dict[str, tuple[int, int, int, int]] = {}
        for key, raw in value.items():
            if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                raise ValueError("window position must be a 4-item tuple")
            result[str(key)] = tuple(int(v) for v in raw)  # type: ignore[assignment]
        return result

    @model_validator(mode="after")
    def _languages_must_differ(self) -> "AppConfigModel":
        if self.source_language == self.target_language:
            raise ValueError("source_language and target_language must be different")
        return self

