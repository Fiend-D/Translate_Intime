"""Application configuration model."""

from __future__ import annotations

import sys
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# Kokoro v1.0 Chinese voice names saved by older versions; v1.1-zh only ships numbered voices.
KOKORO_LEGACY_ZH_VOICES = {
    "zf_xiaoxiao": "zf_001",
    "zf_xiaobei": "zf_002",
    "zf_xiaoni": "zf_003",
    "zf_xiaoyi": "zf_004",
    "zm_yunxi": "zm_010",
    "zm_yunjian": "zm_011",
    "zm_yunxia": "zm_012",
    "zm_yunyang": "zm_013",
}


AsrBackend = Literal["dashscope", "local", "live_captions", "qwen_flash", "doubao_flash"]
TtsEngineId = Literal["auto", "kokoro", "melo", "edge", "doubao", "cosyvoice", "openai"]
TTS_BACKENDS: tuple[TtsEngineId, ...] = (
    "auto",
    "kokoro",
    "melo",
    "edge",
    "doubao",
    "cosyvoice",
    "openai",
)

_CHOICE_FIELDS: dict[str, tuple[frozenset[str], str]] = {
    "vad_sensitivity": (frozenset({"low", "medium", "high"}), "medium"),
    "quality_preset": (frozenset({"quality", "balanced", "saver", "turbo"}), "balanced"),
    "capture_backend": (frozenset({"auto", "loopback"}), "auto"),
    "dota_coach_mode": (frozenset({"normal", "turbo"}), "normal"),
    "economy_tts_backend": (frozenset(TTS_BACKENDS), "kokoro"),
}


DEFAULT_MIC_ASR_MODEL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
DEFAULT_GAME_ASR_MODEL = "auto"


def default_asr_backend() -> AsrBackend:
    """Live Captions on Windows; local models everywhere else."""
    return "live_captions" if _is_windows() else "local"


ThemeKey = Literal["system", "nola_light", "nola_dark", "mint_dark", "violet", "sand", "graphite"]
THEME_KEYS: tuple[ThemeKey, ...] = (
    "system",
    "nola_light",
    "nola_dark",
    "mint_dark",
    "violet",
    "sand",
    "graphite",
)
_LEGACY_THEMES: dict[str, ThemeKey] = {"dark": "nola_dark", "light": "nola_light"}
_DEFAULT_SUBTITLE_COLORS = ("#FFFFFF", "#141418")


def normalize_theme_key(value: object) -> ThemeKey:
    """Map stored theme values (including pre-v0.4 dark/light) onto a known key."""
    text = str(value or "").strip().lower()
    if text in _LEGACY_THEMES:
        return _LEGACY_THEMES[text]
    for key in THEME_KEYS:
        if key == text:
            return key
    return "system"


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
    dota_coach_ask: str = "<ctrl>+<alt>+k"


class AppConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_language: str = "zh"
    target_language: str = "en"
    subtitle_font_size: int = Field(default=22, ge=12, le=72)
    # Background plate only; subtitle text is always opaque
    subtitle_opacity: float = Field(default=0.88, ge=0.0, le=1.0)
    subtitle_text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_bg_color: str = Field(default="#141418", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_font_family: str = ""
    subtitle_follow_theme: bool = True
    log_dir: str = "logs"
    debug_mode: bool = False
    transcript_logging_enabled: bool = True
    transcript_retention_days: int = Field(default=30, ge=1, le=3650)
    subtitle_window_positions: dict[str, tuple[int, int, int, int]] = Field(default_factory=dict)

    enable_mic: bool = True
    enable_game: bool = True
    show_mic_subtitle: bool = True
    show_game_subtitle: bool = True
    play_mic_voice: bool = False
    play_game_voice: bool = False
    input_device: int | str | None = None
    output_device: int | str | None = None
    loopback_device: int | str | None = None
    # Indices shift when devices are plugged in/out; names re-identify the device.
    input_device_name: str = ""
    output_device_name: str = ""
    loopback_device_name: str = ""

    translation_mode: Literal["volc", "economy"] = "volc"
    volc_api_key: str = ""
    volc_access_token: str = ""
    volc_speaker_id: str = ""
    volc_speech_rate: int = Field(default=0, ge=-50, le=100)
    volc_iam_ak: str = ""
    volc_iam_sk: str = ""
    volc_console_app_id: str = ""
    volc_session_rotate_minutes: int = Field(default=12, ge=0, le=240)

    # Economy mode (Fun-ASR + NLLB local MT + Kokoro TTS)
    economy_dashscope_api_key: str = ""
    # qwen_flash = Qwen3-ASR-Flash (DashScope key); doubao_flash = 豆包极速识别 (Volc key).
    # Legacy sherpa|whisper values load as local; live_captions is Windows-only.
    economy_asr_backend: AsrBackend = Field(default_factory=lambda: default_asr_backend())
    economy_asr_model: str = "fun-asr-realtime"  # DashScope model (dashscope backend)
    # Local ASR model per channel: mic = what I say, game = what I hear.
    economy_asr_mic_model: str = DEFAULT_MIC_ASR_MODEL
    economy_asr_game_model: str = DEFAULT_GAME_ASR_MODEL
    economy_mt_backend: Literal["auto", "nllb", "argos", "mymemory", "llm", "hymt"] = "nllb"
    # OpenAI-compatible chat endpoint for economy_mt_backend="llm"
    economy_llm_base_url: str = ""
    economy_llm_model: str = ""
    economy_llm_api_key: str = ""
    economy_llm_context_turns: int = Field(default=3, ge=0, le=10)
    economy_mt_cache_size: int = Field(default=512, ge=0, le=10000)
    # Per-task compute device; empty follows device_preference.
    economy_asr_device: Literal["", "auto", "cpu", "cuda"] = ""
    economy_mt_device: Literal["", "auto", "cpu", "cuda"] = ""
    economy_tts_device: Literal["", "auto", "cpu", "cuda"] = ""
    economy_tts_backend: TtsEngineId = "kokoro"
    economy_nllb_model: str = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
    economy_offline_setup_done: bool = False  # first-run offline model dialog completed
    economy_kokoro_voice_en: str = "af_bella"
    economy_kokoro_voice_zh: str = "zf_001"
    # Speech speed for every economy TTS engine (name kept for existing configs).
    economy_kokoro_speed: float = Field(default=0.92, ge=0.7, le=1.3)
    economy_edge_voice_zh: str = "zh-CN-XiaoxiaoNeural"
    economy_edge_voice_en: str = "en-US-JennyNeural"
    # Doubao TTS reuses the Volc API Key (or AppID + Token) above.
    economy_doubao_voice_zh: str = "zh_female_wanwanxiaohe_moon_bigtts"
    economy_doubao_voice_en: str = "zh_female_shuangkuaisisi_moon_bigtts"
    # CosyVoice reuses economy_dashscope_api_key.
    economy_cosyvoice_model: str = "cosyvoice-v2"
    economy_cosyvoice_voice_zh: str = "longxiaochun_v2"
    economy_cosyvoice_voice_en: str = "loongbella_v2"
    # OpenAI-compatible /v1/audio/speech (OpenAI, SiliconFlow, GPT-SoVITS, Kokoro-FastAPI...)
    economy_openai_tts_url: str = ""
    economy_openai_tts_model: str = "tts-1"
    economy_openai_tts_api_key: str = ""
    economy_openai_voice_zh: str = "alloy"
    economy_openai_voice_en: str = "alloy"
    economy_sentence_min_chars: int = Field(default=4, ge=1, le=40)
    economy_sentence_pause_ms: int = Field(default=900, ge=300, le=3000)
    # Longest an unfinished sentence waits for a pause before it is translated anyway.
    economy_sentence_max_wait_ms: int = Field(default=7000, ge=1200, le=15000)
    # 设备偏好: auto (自动检测) / cpu / cuda
    device_preference: Literal["auto", "cpu", "cuda"] = "auto"
    economy_utterance_silence_ms: int = Field(default=300, ge=50, le=5000)
    economy_utterance_min_ms: int = Field(default=400, ge=50, le=10000)
    economy_utterance_max_ms: int = Field(default=8000, ge=500, le=60000)
    # 连续音频软切分: 累积 ≥soft_split_ms 且近 soft_split_quiet_ms 窗口 RMS <tail_rms
    economy_utterance_soft_split_ms: int = Field(default=6000, ge=500, le=60000)
    economy_utterance_soft_split_quiet_ms: int = Field(default=280, ge=50, le=2000)
    economy_utterance_tail_rms: float = Field(default=0.003, ge=0.0005, le=0.05)

    hotwords: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    subtitle_history_lines: int = Field(default=2, ge=0, le=40)
    show_original_in_overlay: bool = True
    overlay_locked: dict[str, bool] = Field(default_factory=dict)
    theme_mode: ThemeKey = "system"
    status_panel_visible: bool = True
    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)

    music_folder: str = ""
    music_auto_next: bool = True
    show_advanced_devices: bool = False
    capture_backend: str = "auto"
    original_audio: Literal["duck", "mute", "mix"] = "duck"
    duck_gain: float = Field(default=0.2, ge=0.0, le=1.0)

    vad_enabled: bool = True
    vad_game_enabled: bool = True
    vad_backend: Literal["auto", "silero", "rms"] = "auto"
    vad_sensitivity: str = "medium"
    vad_open_ms: int = Field(default=80, ge=20, le=2000)
    vad_hangover_ms: int = Field(default=600, ge=20, le=5000)
    vad_barge_in_ms: int = Field(default=200, ge=40, le=2000)
    vad_preroll_ms: int = Field(default=300, ge=0, le=2000)
    aec_enabled: bool = True
    quality_preset: str = "balanced"

    dota_coach_enabled: bool = True
    dota_coach_url: str = ""
    dota_coach_mode: str = "normal"
    dota_coach_arm_seconds: int = Field(default=12, ge=1, le=120)

    @model_validator(mode="before")
    @classmethod
    def _keep_custom_subtitle_colors(cls, data: Any) -> Any:
        # Configs written before subtitle_follow_theme existed keep any
        # user-picked overlay colours instead of silently switching to the theme.
        if isinstance(data, dict) and "subtitle_follow_theme" not in data:
            colors = (
                str(data.get("subtitle_text_color", _DEFAULT_SUBTITLE_COLORS[0])).upper(),
                str(data.get("subtitle_bg_color", _DEFAULT_SUBTITLE_COLORS[1])).upper(),
            )
            if colors != _DEFAULT_SUBTITLE_COLORS:
                data = {**data, "subtitle_follow_theme": False}
        return data

    @model_validator(mode="before")
    @classmethod
    def _split_legacy_asr_model(cls, data: Any) -> Any:
        # One shared economy_asr_local_model became per-channel models. A legacy
        # "auto" (streaming Zipformer) keeps the game channel; the mic channel
        # moves to SenseVoice. Any explicit model stays on both channels.
        if not isinstance(data, dict) or "economy_asr_local_model" not in data:
            return data
        legacy = str(data.get("economy_asr_local_model") or "").strip()
        data = {k: v for k, v in data.items() if k != "economy_asr_local_model"}
        if not legacy:
            return data
        data.setdefault("economy_asr_game_model", legacy)
        data.setdefault(
            "economy_asr_mic_model", DEFAULT_MIC_ASR_MODEL if legacy == "auto" else legacy
        )
        return data

    @field_validator("economy_asr_mic_model", "economy_asr_game_model", mode="before")
    @classmethod
    def _coerce_asr_model(cls, value: Any, info: ValidationInfo) -> str:
        text = str(value or "").strip()
        if text:
            return text
        if info.field_name == "economy_asr_mic_model":
            return DEFAULT_MIC_ASR_MODEL
        return DEFAULT_GAME_ASR_MODEL

    @field_validator("theme_mode", mode="before")
    @classmethod
    def _coerce_theme_mode(cls, value: Any) -> ThemeKey:
        return normalize_theme_key(value)

    @field_validator("economy_kokoro_voice_zh", mode="before")
    @classmethod
    def _migrate_kokoro_voice_zh(cls, value: Any) -> str:
        voice = str(value or "").strip() or "zf_001"
        return KOKORO_LEGACY_ZH_VOICES.get(voice, voice)

    @field_validator("economy_sentence_max_wait_ms", mode="before")
    @classmethod
    def _migrate_sentence_max_wait(cls, value: Any) -> Any:
        # 2800 ms was the old default (no UI sets it); it cut most sentences in half.
        return 7000 if value == 2800 else value

    @field_validator("economy_asr_backend", mode="before")
    @classmethod
    def _coerce_asr_backend(cls, value: Any) -> str:
        backend = str(value or "").strip()
        if not backend:
            return default_asr_backend()
        if backend in ("sherpa", "whisper"):
            return "local"
        if backend == "live_captions" and not _is_windows():
            return "local"
        return backend

    @field_validator(
        "vad_sensitivity",
        "quality_preset",
        "capture_backend",
        "dota_coach_mode",
        "economy_tts_backend",
        mode="before",
    )
    @classmethod
    def _coerce_known_choice(cls, value: Any, info: ValidationInfo) -> str:
        allowed, default = _CHOICE_FIELDS[str(info.field_name)]
        text = str(value or "").strip()
        return text if text in allowed else default

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
    def _languages_must_differ(self) -> AppConfigModel:
        if self.source_language == self.target_language:
            raise ValueError("source_language and target_language must be different")
        return self
