"""Economy translation engine: utterance → ASR → MT → TTS."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, replace

from src.engines.base import EngineCallbacks, EngineMode
from src.engines.pipeline.asr import AsrBackend, DashScopeAsr, UnconfiguredAsr
from src.engines.pipeline.cloud_asr import DoubaoFlashAsr, QwenFlashAsr
from src.engines.pipeline.cloud_tts import CosyVoiceTts, DoubaoTts, FallbackTts, OpenAICompatTts
from src.engines.pipeline.kokoro_tts import KokoroOnnxTts
from src.engines.pipeline.live_captions_asr import LiveCaptionsAsr
from src.engines.pipeline.llm_mt import CachedMt, ChatCompletionMt, FallbackMt, HyMtLocal
from src.engines.pipeline.mt import ArgosMt, AutoMt, MtBackend, MyMemoryMt, UnconfiguredMt
from src.engines.pipeline.qwen_asr import QwenLocalAsr, is_qwen_asr_model
from src.engines.pipeline.sentence_split import (
    ensure_terminal_punct,
    split_sentences,
)
from src.engines.pipeline.sherpa_asr import SherpaOnnxAsr
from src.engines.pipeline.sherpa_tts import MeloSherpaTts
from src.engines.pipeline.tts import AutoTts, EdgePcmTts, TtsBackend, UnconfiguredTts
from src.engines.pipeline.tts_catalog import DEFAULT_TTS_VOICES, tts_engine
from src.engines.pipeline.utterance import UtteranceBuffer
from src.engines.pipeline.whisper_asr import FasterWhisperAsr, whisper_model_info
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.utils.logger import logger

_ASR_LABELS = {
    "live_captions": "Windows Live Captions",
    "local": "本地识别",
    "qwen_flash": "Qwen3-ASR-Flash 云端识别",
    "doubao_flash": "豆包极速识别",
    "dashscope": "阿里云 Fun-ASR",
}
_MT_LABELS = {
    "nllb": "NLLB 本地翻译",
    "llm": "大模型翻译",
    "hymt": "Hy-MT2 本地翻译",
    "auto": "自动翻译",
    "mymemory": "MyMemory 在线翻译",
    "argos": "Argos 本地翻译",
}
_ERR_NO_ASR = "经济模式需要 DashScope API Key（配置项或环境变量 DASHSCOPE_API_KEY）"
_ERR_NO_SDK = "经济模式需要安装 dashscope：pip install 'dashscope>=1.20'"
_ERR_NO_VOLC_ASR = "豆包极速识别需要火山 API Key（或 AppID + Access Token），请在火山设置中填写"
_ERR_NO_SHERPA = (
    "经济模式本地 ASR 不可用：未安装 sherpa-onnx (pip install sherpa-onnx) "
    "或模型下载失败，请查看日志"
)
_ERR_NO_WHISPER = (
    "经济模式本地 ASR 不可用：未安装 faster-whisper (pip install faster-whisper) "
    "或模型下载失败，请查看日志"
)
_ERR_NO_QWEN_ASR = (
    "Qwen3-ASR 不可用：安装版（EXE）未包含 torch，请改用 Whisper 或 SenseVoice"
    if getattr(sys, "frozen", False)
    else "Qwen3-ASR 不可用：需要 torch 与支持 Qwen3-ASR 的 transformers，或模型下载失败，请查看日志"
)
_ERR_NO_LIVECAPTIONS = (
    "Windows Live Captions 不可用：仅支持 Windows 11 22H2+，"
    "需安装 uiautomation (pip install uiautomation) 并开启系统实时字幕功能"
)
_WARN_LC_FALLBACK = "Live Captions 仅支持 Windows；已自动切换为本地 ASR（faster-whisper / sherpa）"
_ERR_LOG_INTERVAL = 5.0
_DIAG_LOG_INTERVAL = 5.0
_MT_LOADING_STATUS = "本地翻译模型加载中"
_ASR_OVERLAP_BYTES = 16000 * 2  # 1 second of PCM16 mono at 16 kHz
_FINAL_ASR_SILENCE = b"\x00\x00" * int(16000 * 0.4)
_FINAL_ASR_MAX_BYTES = 30 * 16000 * 2

# Local / offline ASR backends never require a DashScope API key.
LOCAL_ASR_BACKENDS = frozenset({"local", "live_captions", "sherpa", "whisper"})
DASHSCOPE_ASR_BACKENDS = frozenset({"dashscope", "qwen_flash"})
CLOUD_ASR_BACKENDS = DASHSCOPE_ASR_BACKENDS | {"doubao_flash"}
# Preferred default when unset; non-Windows always remaps live_captions → local.
DEFAULT_ECONOMY_ASR_BACKEND = "live_captions"


def live_captions_supported() -> bool:
    """Windows Live Captions is Win11-only; never usable on Linux/macOS."""
    return sys.platform.startswith("win")


def default_economy_asr_backend() -> str:
    """Platform-aware default: Live Captions on Windows, local ASR elsewhere."""
    if live_captions_supported():
        return DEFAULT_ECONOMY_ASR_BACKEND
    return "local"


@dataclass
class _Job:
    direction: Direction
    pcm: bytes
    source_lang: str
    target_lang: str
    play_voice: bool


@dataclass
class _PendingText:
    text: str
    source_lang: str
    target_lang: str
    play_voice: bool
    updated_at: float
    created_at: float
    pcm: bytes
    chunk_count: int


@dataclass
class _TtsJob:
    direction: Direction
    text: str
    language: str
    generation: int = 0


def resolve_dashscope_api_key(config: AppConfigModel) -> str:
    key = (getattr(config, "economy_dashscope_api_key", "") or "").strip()
    if key:
        return key
    return (os.environ.get("DASHSCOPE_API_KEY") or "").strip()


def resolve_economy_asr_backend(config: AppConfigModel | object) -> str:
    """Normalize economy ASR backend id.

    Empty/unknown → platform default. On non-Windows, ``live_captions`` always
    falls back to ``local`` (Live Captions cannot capture game PCM on Linux).
    """
    pref = (getattr(config, "economy_asr_backend", None) or "").strip()
    if not pref:
        return default_economy_asr_backend()
    if pref == "live_captions" and not live_captions_supported():
        return "local"
    if pref in LOCAL_ASR_BACKENDS or pref in CLOUD_ASR_BACKENDS:
        return pref
    return default_economy_asr_backend()


def economy_asr_requires_dashscope(config: AppConfigModel | object) -> bool:
    """True only for cloud DashScope backends (Fun-ASR / Qwen3-ASR-Flash)."""
    return resolve_economy_asr_backend(config) in DASHSCOPE_ASR_BACKENDS


def task_device(config: AppConfigModel | object, task: str) -> str:
    """Per-task device (asr / mt / tts); empty falls back to the global preference."""
    specific = (getattr(config, f"economy_{task}_device", "") or "").strip()
    return specific or (getattr(config, "device_preference", "auto") or "auto")


def _volc_asr_credentials(config: AppConfigModel) -> tuple[str, str, str]:
    """(api_key, app_id, access_token) for Doubao ASR from the Volc settings."""
    from src.core.volc_engine import resolve_volc_credentials

    key, token, mode = resolve_volc_credentials(
        getattr(config, "volc_api_key", "") or "",
        getattr(config, "volc_access_token", "") or "",
    )
    if mode == "legacy":
        return "", key, token
    return key, "", ""


def channel_asr_model(config: AppConfigModel, direction: Direction) -> str:
    """Local ASR model id for one channel (mic = OUTBOUND, game = INBOUND)."""
    if direction == Direction.OUTBOUND:
        return config.economy_asr_mic_model
    return config.economy_asr_game_model


def _build_local_asr(config: AppConfigModel, device_pref: str, local_model: str) -> AsrBackend:
    hotwords = list(getattr(config, "hotwords", None) or [])
    if is_qwen_asr_model(local_model):
        return QwenLocalAsr(
            model_id=local_model,
            device_preference=device_pref,
            hotwords=hotwords,
        )
    if whisper_model_info(local_model):
        return FasterWhisperAsr(
            model_id=local_model,
            device_preference=device_pref,
            hotwords=hotwords,
        )
    return SherpaOnnxAsr(
        model_id=local_model,
        device_preference=device_pref,
        hotwords=hotwords,
    )


ChannelAsr = dict[Direction, AsrBackend]


def build_channel_asr(config: AppConfigModel) -> ChannelAsr:
    """One ASR per channel; channels with the same backend/model share an instance."""
    asr_pref = resolve_economy_asr_backend(config)
    if asr_pref == "live_captions" or asr_pref not in LOCAL_ASR_BACKENDS:
        shared = _build_shared_asr(config, asr_pref)
        return {Direction.OUTBOUND: shared, Direction.INBOUND: shared}
    device = task_device(config, "asr")
    by_model: dict[str, AsrBackend] = {}
    result: ChannelAsr = {}
    for direction in (Direction.OUTBOUND, Direction.INBOUND):
        model = channel_asr_model(config, direction)
        if model not in by_model:
            by_model[model] = _build_local_asr(config, device, model)
        result[direction] = by_model[model]
    return result


def _build_shared_asr(config: AppConfigModel, asr_pref: str) -> AsrBackend:
    hotwords = list(getattr(config, "hotwords", None) or [])
    if asr_pref == "live_captions":
        # Windows Live Captions (系统级 ASR, 低占用高准确率, 仅 Win11 22H2+)
        asr: AsrBackend = LiveCaptionsAsr(device_preference=task_device(config, "asr"))
    elif asr_pref == "qwen_flash":
        asr = QwenFlashAsr(api_key=resolve_dashscope_api_key(config), hotwords=hotwords)
    elif asr_pref == "doubao_flash":
        api_key, app_id, token = _volc_asr_credentials(config)
        asr = DoubaoFlashAsr(api_key=api_key, app_id=app_id, access_token=token, hotwords=hotwords)
    else:
        api_key = resolve_dashscope_api_key(config)
        model = getattr(config, "economy_asr_model", None) or "fun-asr-realtime"
        if api_key:
            asr = DashScopeAsr(api_key=api_key, model=model, hotwords=hotwords)
        else:
            asr = UnconfiguredAsr()
    return asr


def economy_stack_label(config: AppConfigModel) -> str:
    """Status line naming the active economy ASR + MT + TTS engines."""
    asr = _ASR_LABELS.get(resolve_economy_asr_backend(config), "云端识别")
    mt = _MT_LABELS.get(getattr(config, "economy_mt_backend", "nllb") or "nllb", "翻译")
    tts = tts_engine(getattr(config, "economy_tts_backend", "kokoro")).title
    return f"经济模式：{asr} + {mt} + {tts}"


def _tts_voices(config: AppConfigModel, engine_id: str) -> tuple[str, str]:
    default_zh, default_en = DEFAULT_TTS_VOICES[engine_id]
    zh_field, en_field = f"economy_{engine_id}_voice_zh", f"economy_{engine_id}_voice_en"
    zh = (getattr(config, zh_field, "") or "").strip() or default_zh
    en = (getattr(config, en_field, "") or "").strip() or default_en
    return zh, en


def _build_kokoro(config: AppConfigModel, prefer: str) -> TtsBackend:
    voice_zh, voice_en = _tts_voices(config, "kokoro")
    return AutoTts(
        prefer=prefer,
        kokoro=KokoroOnnxTts(
            voice_en=voice_en,
            voice_zh=voice_zh,
            speed=float(getattr(config, "economy_kokoro_speed", 0.92) or 0.92),
            device_preference=task_device(config, "tts"),
        ),
        edge=_build_edge(config),
        voice_en=voice_en,
        voice_zh=voice_zh,
        local_zh=MeloSherpaTts(speed=float(getattr(config, "economy_kokoro_speed", 0.92) or 0.92)),
    )


def _build_edge(config: AppConfigModel) -> EdgePcmTts:
    voice_zh, voice_en = _tts_voices(config, "edge")
    speed = float(getattr(config, "economy_kokoro_speed", 0.92) or 0.92)
    return EdgePcmTts(voice_zh=voice_zh, voice_en=voice_en, speed=speed)


def build_tts(config: AppConfigModel) -> TtsBackend:
    """Economy TTS for ``economy_tts_backend``; cloud engines fall back to Edge TTS."""
    engine_id = getattr(config, "economy_tts_backend", "kokoro") or "kokoro"
    speed = float(getattr(config, "economy_kokoro_speed", 0.92) or 0.92)
    if engine_id == "edge":
        return _build_edge(config)
    if engine_id == "auto":
        return _build_kokoro(config, "auto")
    if engine_id == "melo":
        return FallbackTts(MeloSherpaTts(speed=speed), _build_edge(config), name="MeloTTS")
    if engine_id == "doubao":
        api_key, app_id, token = _volc_asr_credentials(config)
        voice_zh, voice_en = _tts_voices(config, "doubao")
        primary: TtsBackend = DoubaoTts(
            api_key=api_key,
            app_id=app_id,
            access_token=token,
            voice_zh=voice_zh,
            voice_en=voice_en,
            speed=speed,
        )
        return FallbackTts(primary, _build_edge(config), name="豆包语音合成")
    if engine_id == "cosyvoice":
        voice_zh, voice_en = _tts_voices(config, "cosyvoice")
        primary = CosyVoiceTts(
            api_key=resolve_dashscope_api_key(config),
            model=getattr(config, "economy_cosyvoice_model", "") or "cosyvoice-v2",
            voice_zh=voice_zh,
            voice_en=voice_en,
            speed=speed,
        )
        return FallbackTts(primary, _build_edge(config), name="CosyVoice")
    if engine_id == "openai":
        voice_zh, voice_en = _tts_voices(config, "openai")
        primary = OpenAICompatTts(
            url=getattr(config, "economy_openai_tts_url", "") or "",
            model=getattr(config, "economy_openai_tts_model", "") or "",
            api_key=getattr(config, "economy_openai_tts_api_key", "") or "",
            voice_zh=voice_zh,
            voice_en=voice_en,
            speed=speed,
        )
        return FallbackTts(primary, _build_edge(config), name="OpenAI 兼容语音合成")
    return _build_kokoro(config, "kokoro")


def build_economy_backends(
    config: AppConfigModel,
) -> tuple[ChannelAsr, MtBackend, TtsBackend]:
    """Factory helpers used by EconomyPipelineEngine and tests."""
    mt_device = task_device(config, "mt")
    raw_pref = (getattr(config, "economy_asr_backend", None) or "").strip()
    if raw_pref == "live_captions" and resolve_economy_asr_backend(config) == "local":
        logger.warning(_WARN_LC_FALLBACK)
    asrs = build_channel_asr(config)

    nllb_model = (
        getattr(config, "economy_nllb_model", None)
        or "JustFrederik/nllb-200-distilled-600M-ct2-int8"
    )
    mt_pref = getattr(config, "economy_mt_backend", "nllb") or "nllb"
    if mt_pref == "argos":
        mt: MtBackend = ArgosMt()
    elif mt_pref == "mymemory":
        mt = MyMemoryMt()
    elif mt_pref == "nllb":
        mt = AutoMt(
            prefer="nllb",
            nllb_model=nllb_model,
            device_preference=mt_device,
        )
    elif mt_pref == "llm":
        llm = ChatCompletionMt(
            base_url=getattr(config, "economy_llm_base_url", "") or "",
            model=getattr(config, "economy_llm_model", "") or "",
            api_key=getattr(config, "economy_llm_api_key", "") or "",
            glossary=dict(getattr(config, "glossary", None) or {}),
            context_turns=int(getattr(config, "economy_llm_context_turns", 3) or 0),
        )
        if not llm.configured:
            logger.warning("LLM 翻译未配置接口地址或模型，改用 NLLB 本地翻译")
        mt = FallbackMt(
            llm,
            AutoMt(prefer="auto", nllb_model=nllb_model, device_preference=mt_device),
            name="LLM 翻译",
        )
    elif mt_pref == "hymt":
        # Local first: a failed Hy-MT2 load falls back to NLLB, not straight to the cloud.
        mt = FallbackMt(
            HyMtLocal(device_preference=mt_device),
            AutoMt(prefer="auto", nllb_model=nllb_model, device_preference=mt_device),
            name="Hy-MT2",
        )
    else:
        # auto → NLLB first, then Argos / MyMemory
        mt = AutoMt(
            prefer="auto",
            nllb_model=nllb_model,
            device_preference=mt_device,
        )
    cache_size = int(getattr(config, "economy_mt_cache_size", 512) or 0)
    if cache_size > 0:
        mt = CachedMt(mt, max_entries=cache_size)

    tts = build_tts(config)

    return asrs, mt, tts


_PENDING_TEXT_MAX_CHARS = 600
_COMMIT_DEDUP_WINDOW_SEC = 6.0
_HEAD_TRIM_MIN_CHARS = 4


def _dedup_key(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _trim_repeated_head(previous: str, current: str) -> str:
    """Remove a leading part of ``current`` that repeats the end of ``previous``."""
    prev_key = _dedup_key(previous)
    # Map each alnum char of ``current`` to its index so the cut keeps punctuation intact.
    positions = [i for i, ch in enumerate(current) if ch.isalnum()]
    cur_key = "".join(current[i].casefold() for i in positions)
    if not cur_key:
        return current
    # CJK characters carry more information each than Latin letters.
    min_chars = _HEAD_TRIM_MIN_CHARS - 1 if not cur_key[0].isascii() else _HEAD_TRIM_MIN_CHARS
    best = 0
    for count in range(min(len(prev_key), len(cur_key) - 1), min_chars - 1, -1):
        if prev_key.endswith(cur_key[:count]):
            best = count
            break
    if not best:
        return current
    cut = positions[best - 1] + 1
    rest = current[cut:].lstrip(" ,，、.。!！?？;；:：")
    return rest or current


def _apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """Simple left-to-right replace of glossary keys (longer keys first)."""
    if not text or not glossary:
        return text
    out = text
    for src, dst in sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True):
        if src:
            out = out.replace(src, dst)
    return out


def _protect_glossary(text: str, glossary: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    """Replace glossary keys in source with placeholders before MT.

    Returns ``(protected_text, [(placeholder, target_value), ...])``.
    """
    if not text or not glossary:
        return text, []
    protected = text
    restores: list[tuple[str, str]] = []
    idx = 0
    for src, dst in sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not src or src not in protected:
            continue
        placeholder = f"__G{idx}__"
        protected = protected.replace(src, placeholder)
        restores.append((placeholder, dst))
        idx += 1
    return protected, restores


def _restore_glossary(text: str, restores: list[tuple[str, str]]) -> str:
    if not text or not restores:
        return text
    out = text
    for placeholder, dst in restores:
        out = out.replace(placeholder, dst)
    return out


class EconomyPipelineEngine:
    """Isolated economy path; capture/VAD/UI stay in the pipeline."""

    def __init__(
        self,
        *,
        config: AppConfigModel,
        callbacks: EngineCallbacks,
        asr: AsrBackend | ChannelAsr | None = None,
        mt: MtBackend | None = None,
        tts: TtsBackend | None = None,
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        raw_pref = (getattr(config, "economy_asr_backend", None) or "").strip()
        self._lc_fallback = False
        if asr is None and mt is None and tts is None:
            resolved = resolve_economy_asr_backend(config)
            self._lc_fallback = raw_pref == "live_captions" and resolved == "local"
            asr, mt, tts = build_economy_backends(config)
        if isinstance(asr, dict):
            self._asrs: ChannelAsr = dict(asr)
        else:
            shared = asr or UnconfiguredAsr()
            self._asrs = {Direction.OUTBOUND: shared, Direction.INBOUND: shared}
        self._started_asrs: list[AsrBackend] = []
        self._mt: MtBackend = mt or UnconfiguredMt()
        self._tts: TtsBackend = tts or UnconfiguredTts()
        self._active: set[Direction] = set()
        self._play_voice: dict[Direction, bool] = {}
        silence = int(getattr(config, "economy_utterance_silence_ms", 300) or 300)
        min_ms = int(getattr(config, "economy_utterance_min_ms", 400) or 400)
        max_ms = int(getattr(config, "economy_utterance_max_ms", 8000) or 8000)
        # 连续音频软切分: ≥soft_split_ms 且近 soft_split_quiet_ms 窗口安静时切分.
        soft_split_ms = int(getattr(config, "economy_utterance_soft_split_ms", 6000) or 6000)
        soft_split_quiet_ms = int(
            getattr(config, "economy_utterance_soft_split_quiet_ms", 280) or 280
        )
        tail_rms_threshold = float(getattr(config, "economy_utterance_tail_rms", 0.003) or 0.003)
        self._sentence_min_chars = int(getattr(config, "economy_sentence_min_chars", 4) or 4)
        self._sentence_pause_ms = int(getattr(config, "economy_sentence_pause_ms", 900) or 900)
        self._sentence_max_wait_ms = int(
            getattr(config, "economy_sentence_max_wait_ms", 2800) or 2800
        )
        self._buffers: dict[Direction, UtteranceBuffer] = {
            Direction.OUTBOUND: UtteranceBuffer(
                end_silence_ms=silence,
                min_ms=min_ms,
                max_ms=max_ms,
                soft_split_ms=soft_split_ms,
                soft_split_quiet_ms=soft_split_quiet_ms,
                tail_rms_threshold=tail_rms_threshold,
            ),
            Direction.INBOUND: UtteranceBuffer(
                end_silence_ms=silence,
                min_ms=min_ms,
                max_ms=max_ms,
                soft_split_ms=soft_split_ms,
                soft_split_quiet_ms=soft_split_quiet_ms,
                tail_rms_threshold=tail_rms_threshold,
            ),
        }
        # Bound work-in-flight so slow local inference cannot grow latency/memory forever.
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=8)
        self._tts_queue: queue.Queue[_TtsJob | None] = queue.Queue(maxsize=8)
        self._tts_generation: dict[Direction, int] = {
            Direction.OUTBOUND: 0,
            Direction.INBOUND: 0,
        }
        self._deferred_asr_jobs: dict[Direction, _Job] = {}
        self._pending_text: dict[Direction, _PendingText] = {}
        # Last time speech PCM reached the engine; a sentence ends at a real pause.
        self._last_pcm_at: dict[Direction, float] = {}
        self._last_committed: dict[Direction, tuple[str, float]] = {}
        self._asr_audio_tail: dict[Direction, bytes] = {
            Direction.OUTBOUND: b"",
            Direction.INBOUND: b"",
        }
        self._worker: threading.Thread | None = None
        self._tts_worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._tts_stop_event = threading.Event()
        self._last_err_at = 0.0
        self._last_warn_at: dict[str, float] = {}
        self._last_diag_at = 0.0
        self._backends_started = False
        self._tts_started = False

    @property
    def engine_id(self) -> EngineMode:
        return "economy"

    @property
    def active_directions(self) -> frozenset[Direction]:
        return frozenset(self._active)

    @property
    def loading_models(self) -> tuple[str, ...]:
        """Names of local backends still downloading or loading in the background."""
        loading: list[str] = []
        if any(getattr(asr, "warming_up", False) for asr in self._unique_asrs()):
            loading.append("本地 ASR")
        if getattr(self._mt, "warming_up", False):
            loading.append("NLLB 翻译")
        if self._tts_started and getattr(self._tts, "warming_up", False):
            loading.append(self._tts_title())
        return tuple(loading)

    def _tts_title(self) -> str:
        return tts_engine(getattr(self._config, "economy_tts_backend", "kokoro")).title

    def _langs_for(self, direction: Direction) -> tuple[str, str]:
        src = self._config.source_language
        tgt = self._config.target_language
        if direction == Direction.OUTBOUND:
            return src, tgt
        return tgt, src

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="economy-pipeline-worker",
            daemon=True,
        )
        self._worker.start()

    def _ensure_tts_worker(self) -> None:
        if self._tts_worker is not None and self._tts_worker.is_alive():
            return
        self._tts_stop_event.clear()
        self._tts_worker = threading.Thread(
            target=self._tts_worker_loop,
            name="economy-tts-worker",
            daemon=True,
        )
        self._tts_worker.start()

    def _asr_for(self, direction: Direction) -> AsrBackend:
        return self._asrs[direction]

    def _unique_asrs(self) -> list[AsrBackend]:
        unique: list[AsrBackend] = []
        for asr in self._asrs.values():
            if all(asr is not seen for seen in unique):
                unique.append(asr)
        return unique

    def _ensure_asr_started(self, asr: AsrBackend) -> None:
        if any(asr is started for started in self._started_asrs):
            return
        asr.start()
        self._started_asrs.append(asr)

    def _ensure_backends(self) -> None:
        if self._backends_started:
            return
        self._mt.start()
        self._backends_started = True

    def _ensure_tts_started(self) -> None:
        # Loading a voice model costs RAM and time, so wait for a channel that speaks.
        if self._tts_started:
            return
        self._tts.start()
        self._tts_started = True

    def start_direction(self, direction: Direction, *, play_voice: bool = False) -> bool:
        asr_pref = resolve_economy_asr_backend(self._config)
        asr = self._asr_for(direction)
        # 本地 ASR / Live Captions 允许在加载中启动 (warming_up); 云端 ASR 必须已 configured
        if asr_pref in LOCAL_ASR_BACKENDS:
            if not asr.configured and not getattr(asr, "warming_up", False):
                self._ensure_asr_started(asr)
            if not asr.configured and not getattr(asr, "warming_up", False):
                if isinstance(asr, LiveCaptionsAsr):
                    msg = _ERR_NO_LIVECAPTIONS
                elif isinstance(asr, FasterWhisperAsr):
                    msg = _ERR_NO_WHISPER
                elif isinstance(asr, QwenLocalAsr):
                    msg = _ERR_NO_QWEN_ASR
                else:
                    msg = _ERR_NO_SHERPA
                self._callbacks.on_status(msg)
                self._callbacks.on_error(msg)
                self._callbacks.on_engine_status(direction, "economy", "error")
                logger.warning(msg)
                return False
        else:
            if not asr.configured:
                key = resolve_dashscope_api_key(self._config)
                if asr_pref == "doubao_flash":
                    msg = _ERR_NO_VOLC_ASR
                elif asr_pref == "qwen_flash":
                    msg = _ERR_NO_ASR
                else:
                    msg = _ERR_NO_SDK if key else _ERR_NO_ASR
                self._callbacks.on_status(msg)
                self._callbacks.on_error(msg)
                self._callbacks.on_engine_status(direction, "economy", "error")
                logger.warning(msg)
                return False

        self._play_voice[direction] = play_voice
        self._ensure_asr_started(asr)
        self._ensure_backends()
        if play_voice:
            self._ensure_tts_started()
        self._ensure_worker()
        self._active.add(direction)
        self._callbacks.on_engine_status(direction, "economy", "ready")
        if self._lc_fallback:
            logger.warning(_WARN_LC_FALLBACK)
            self._callbacks.on_status(_WARN_LC_FALLBACK)
            self._lc_fallback = False  # toast once per engine lifetime
        self._callbacks.on_status(economy_stack_label(self._config))
        speed = float(getattr(self._config, "economy_kokoro_speed", 0.92) or 0.92)
        tts_title = self._tts_title()
        if play_voice:
            logger.info(f"TTS={tts_title} speed={speed:.2f}, sentence TTS")
        if (
            asr_pref in LOCAL_ASR_BACKENDS
            and not asr.configured
            and getattr(asr, "warming_up", False)
        ):
            warn = "ASR 加载中，识别将在就绪后输出"
            logger.warning(warn)
            self._callbacks.on_status(warn)
        if not getattr(self._mt, "configured", False):
            if getattr(self._mt, "warming_up", False):
                warn = "NLLB 本地翻译模型加载中，识别仍可用；译文将在模型就绪后输出"
                logger.warning(warn)
                self._callbacks.on_status(warn)
            else:
                logger.warning("经济模式 MT 尚未就绪，将在可用时回退或等待加载")
        if (
            not getattr(self._tts, "configured", False)
            and play_voice
            and getattr(self._tts, "warming_up", False)
        ):
            logger.info(f"{tts_title} 加载中，语音将在就绪后播放")
            self._callbacks.on_status(f"正在准备 {tts_title}…")
        model = (
            channel_asr_model(self._config, direction)
            if asr_pref in LOCAL_ASR_BACKENDS and asr_pref != "live_captions"
            else asr_pref
        )
        logger.info(f"Economy channel started: {direction.value} asr={asr_pref} model={model}")
        return True

    def stop_direction(self, direction: Direction) -> None:
        # Keep already-recognized text for the worker to translate after the
        # channel stops. Dropping it here loses the last sentence whenever the
        # user stops during the short sentence-stability window.
        buf = self._buffers.get(direction)
        if buf is not None:
            chunk = buf.flush()
            if chunk:
                self._log_flush(direction, chunk)
                self._enqueue(direction, chunk)
        self._asr_audio_tail[direction] = b""
        self._active.discard(direction)
        self._play_voice.pop(direction, None)
        # Keep loaded local models resident. ``close()`` still releases them on
        # app exit or an explicit model/backend change.

    def send_pcm(self, direction: Direction, pcm: bytes) -> None:
        if direction not in self._active or not pcm:
            return
        self._last_pcm_at[direction] = time.monotonic()
        buf = self._buffers[direction]
        buf.push(pcm)
        chunk = buf.poll()
        if chunk:
            self._log_flush(direction, chunk)
            self._enqueue(direction, chunk)

    def notify_pcm_pending(self, direction: Direction) -> None:
        """Prevent TTS feedback buffering from looking like an end-of-speech gap."""
        if direction not in self._active:
            return
        self._last_pcm_at[direction] = time.monotonic()
        self._buffers[direction].mark_capture_active()

    def _enqueue(self, direction: Direction, pcm: bytes) -> None:
        source_lang, target_lang = self._langs_for(direction)
        job = _Job(
            direction=direction,
            pcm=pcm,
            source_lang=source_lang,
            target_lang=target_lang,
            play_voice=bool(self._play_voice.get(direction, False)),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with suppress(queue.Empty):
                stale = self._queue.get_nowait()
                if stale is not None:
                    self._queue.task_done()
            with suppress(queue.Full):
                self._queue.put_nowait(job)
            self._rate_limited_warning("经济模式队列已满，丢弃一句")

    def _log_flush(self, direction: Direction, pcm: bytes) -> None:
        pcm_ms = len(pcm) / 2.0 / 16000.0 * 1000.0
        self._rate_limited_diag(
            f"经济模式 utterance 已切分 {direction.value} bytes={len(pcm)} ms={pcm_ms:.0f}"
        )

    def _poll_active_buffers(self) -> None:
        """Flush utterances whose silence gap elapsed even if VAD stopped PCM."""
        for direction in list(self._active):
            buf = self._buffers.get(direction)
            if buf is None:
                continue
            chunk = buf.poll()
            if chunk:
                self._log_flush(direction, chunk)
                self._enqueue(direction, chunk)

    def _defer_asr_job(self, job: _Job) -> None:
        """Retain captured audio until a warming local ASR becomes ready."""
        previous = self._deferred_asr_jobs.get(job.direction)
        pcm = (previous.pcm if previous is not None else b"") + job.pcm
        self._deferred_asr_jobs[job.direction] = _Job(
            direction=job.direction,
            pcm=pcm[-_FINAL_ASR_MAX_BYTES:],
            source_lang=job.source_lang,
            target_lang=job.target_lang,
            play_voice=job.play_voice,
        )

    def _retry_deferred_asr_jobs(self) -> None:
        for direction, job in list(self._deferred_asr_jobs.items()):
            asr = self._asr_for(direction)
            if getattr(asr, "warming_up", False):
                continue
            if not getattr(asr, "configured", False):
                unavailable = self._asr_unavailable_message(asr)
                if unavailable:
                    self._rate_limited_error(unavailable)
                    self._deferred_asr_jobs.pop(direction, None)
                continue
            self._deferred_asr_jobs.pop(direction, None)
            self._process_job(job)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            # A failure here (e.g. transcript file locked) must not kill the only worker.
            try:
                self._retry_deferred_asr_jobs()
                self._poll_active_buffers()
                self._flush_stable_text()
            except Exception as exc:
                if not self._stop_event.is_set():
                    self._rate_limited_error(f"经济模式处理失败: {exc}")
                    logger.exception("Economy housekeeping failed")
                    self._stop_event.wait(0.2)
            try:
                job = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if job is None:
                self._queue.task_done()
                break
            try:
                self._process_job(job)
            except Exception as exc:
                if self._stop_event.is_set():
                    continue
                self._rate_limited_error(f"经济模式处理失败: {exc}")
                logger.exception("Economy job failed")
            finally:
                self._queue.task_done()

    def _enqueue_tts(self, direction: Direction, text: str, language: str) -> None:
        self._ensure_tts_worker()
        job = _TtsJob(
            direction=direction,
            text=text,
            language=language,
            generation=self._tts_generation[direction],
        )
        try:
            self._tts_queue.put_nowait(job)
        except queue.Full:
            with suppress(queue.Empty):
                self._tts_queue.get_nowait()
                self._tts_queue.task_done()
            with suppress(queue.Full):
                self._tts_queue.put_nowait(job)
            self._rate_limited_warning("经济模式语音队列已满，丢弃一条过期播报")

    def cancel_pending_audio(self, direction: Direction) -> None:
        """Skip queued and in-flight synthesis for ``direction`` (barge-in)."""
        self._tts_generation[direction] += 1

    def _tts_job_stale(self, job: _TtsJob) -> bool:
        return job.generation != self._tts_generation[job.direction]

    def _tts_worker_loop(self) -> None:
        while not self._tts_stop_event.is_set():
            try:
                job = self._tts_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if job is None:
                self._tts_queue.task_done()
                break
            try:
                while getattr(self._tts, "warming_up", False) and not self._tts_stop_event.wait(
                    0.1
                ):
                    pass
                if self._tts_stop_event.is_set() or self._tts_job_stale(job):
                    continue
                pcm = self._tts.synthesize(job.text, language=job.language)
                if self._tts_stop_event.is_set() or self._tts_job_stale(job):
                    continue
                if pcm:
                    self._callbacks.on_audio(job.direction, pcm)
                elif getattr(self._tts, "warming_up", False):
                    self._callbacks.on_status("正在下载 Kokoro…")
                else:
                    why = getattr(self._tts, "last_failure", "") or "详见日志"
                    self._rate_limited_warning(f"经济模式 TTS 失败（无音频）：{why}")
            except Exception as exc:
                self._rate_limited_error(f"经济模式 TTS 失败: {exc}")
                logger.exception("Economy TTS job failed")
            finally:
                self._tts_queue.task_done()

    @staticmethod
    def _join_recognized_text(previous: str, current: str) -> str:
        previous = previous.strip()
        current = current.strip()
        if not previous:
            return current
        if not current:
            return previous

        # Offline ASR commonly returns a growing hypothesis. Prefer the newer
        # version so newly-added punctuation is not lost ("Hello" -> "Hello.").
        previous_folded = previous.casefold()
        current_folded = current.casefold()
        if current_folded.startswith(previous_folded):
            return current
        if previous_folded.startswith(current_folded):
            return previous

        previous_words = previous.split()
        current_words = current.split()
        max_overlap = min(len(previous_words), len(current_words), 12)
        overlap = 0
        for count in range(max_overlap, 0, -1):
            left = [word.casefold().strip(".,!?;:\"'") for word in previous_words[-count:]]
            right = [word.casefold().strip(".,!?;:\"'") for word in current_words[:count]]
            if left == right:
                overlap = count
                break
        if overlap:
            # Replace the overlapping tail with the latest hypothesis. This
            # preserves punctuation/casing corrections supplied by the ASR.
            return " ".join(previous_words[:-overlap] + current_words).strip()

        if not overlap and previous_words and current_words:
            previous_last = previous_words[-1].casefold().strip(".,!?;:\"'")
            current_first = current_words[0].casefold().strip(".,!?;:\"'")
            if len(previous_last) >= 3 and current_first.startswith(previous_last):
                return " ".join(previous_words[:-1] + current_words).strip()
            elif len(current_first) >= 3 and previous_last.startswith(current_first):
                # The new first word is only a shorter partial repeat. Keep the
                # complete previous word and append any genuinely new words.
                novel_words = current_words[1:]
                if not novel_words:
                    return previous
                return " ".join(previous_words + novel_words).strip()

        # Chinese/Japanese ASR output usually has no spaces, so token overlap
        # cannot work. Merge the longest shared character tail/head instead.
        # Requiring at least two characters avoids accidental one-letter merges.
        max_chars = min(len(previous), len(current), 80)
        for count in range(max_chars, 1, -1):
            if previous[-count:].casefold() == current[:count].casefold():
                return f"{previous[:-count]}{current}".strip()

        novel = current
        joiner = " " if previous[-1].isascii() and novel[0].isascii() else ""
        return f"{previous}{joiner}{novel}".strip()

    def _buffer_recognized_text(self, job: _Job, text: str) -> None:
        pending = self._pending_text.get(job.direction)
        previous = pending.text if pending is not None else ""
        combined = self._join_recognized_text(previous, text)
        if len(combined) > _PENDING_TEXT_MAX_CHARS:
            # Only reachable while MT is still loading: keep the newest speech bounded.
            combined = combined[-_PENDING_TEXT_MAX_CHARS:]
        self._pending_text[job.direction] = _PendingText(
            text=combined,
            source_lang=job.source_lang,
            target_lang=job.target_lang,
            play_voice=job.play_voice,
            updated_at=time.monotonic(),
            created_at=pending.created_at if pending is not None else time.monotonic(),
            pcm=((pending.pcm if pending is not None else b"") + job.pcm)[-_FINAL_ASR_MAX_BYTES:],
            chunk_count=(pending.chunk_count + 1 if pending is not None else 1),
        )
        # Show recognition immediately, but do not translate an unstable phrase.
        self._callbacks.on_source_text(job.direction, combined, False)

    def _flush_stable_text(self, *, force: bool = False) -> None:
        now = time.monotonic()
        base_pause_s = self._sentence_pause_ms / 1000.0
        for direction, pending in list(self._pending_text.items()):
            if getattr(self._mt, "warming_up", False):
                continue
            if not force and not self._sentence_complete(direction, pending, now, base_pause_s):
                continue
            self._pending_text.pop(direction, None)
            # The overlap tail only belongs to the pending utterance. Keeping
            # it after commit makes the next sentence start with old speech.
            self._asr_audio_tail[direction] = b""
            self._translate_stable_text(direction, pending)

    def _sentence_complete(
        self, direction: Direction, pending: _PendingText, now: float, pause_s: float
    ) -> bool:
        """Whether the accumulated recognition is a finished sentence worth translating.

        Streaming ASR (zipformer) emits no punctuation, so the end of a sentence is
        a real pause in the speech: no PCM for ``pause_s`` and nothing captured
        that still waits for recognition. The time since the last ASR update is
        not a pause; it also grows while the next phrase is being spoken.
        """
        if pending.text.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
            return now - pending.updated_at >= min(pause_s, 0.35)
        elapsed = now - pending.created_at
        max_wait_s = self._sentence_max_wait_ms / 1000.0
        if elapsed >= max_wait_s * 1.5:
            # Speech that never pauses (or continuous audio without VAD).
            return True
        buffer = self._buffers.get(direction)
        if (buffer is not None and buffer.has_audio) or not self._queue.empty():
            # Captured speech is not recognized yet; it may finish this sentence.
            return False
        last_pcm = self._last_pcm_at.get(direction)
        if last_pcm is None:
            return now - pending.updated_at >= pause_s
        # PCM stops only after the VAD hangover (~600 ms), so half the pause on top
        # of it is already a ~1 s silence: longer than breaths inside a sentence.
        quiet = now - last_pcm
        if elapsed >= max_wait_s:
            # Long sentence: settle for the next short breath between phrases.
            return quiet >= 0.25
        return quiet >= pause_s / 2

    def _dedupe_committed(self, direction: Direction, text: str) -> str | None:
        """Drop a repeat of the last committed sentence; trim a re-heard leading tail.

        Returns None when the whole text is a recent duplicate.
        """
        now = time.monotonic()
        previous, at = self._last_committed.get(direction, ("", 0.0))
        self._last_committed[direction] = (text, now)
        if not previous or now - at > _COMMIT_DEDUP_WINDOW_SEC:
            return text
        current_key = _dedup_key(text)
        previous_key = _dedup_key(previous)
        if not current_key:
            return text
        if current_key == previous_key or (len(current_key) >= 4 and current_key in previous_key):
            logger.debug(f"跳过重复识别结果: {text[:24]!r}")
            self._last_committed[direction] = (previous, at)
            return None
        trimmed = _trim_repeated_head(previous, text)
        if trimmed != text:
            self._last_committed[direction] = (trimmed, now)
        return trimmed

    def _requeue_untranslated(
        self, direction: Direction, pending: _PendingText, sentences: list[str]
    ) -> None:
        joiner = " " if all(s.isascii() for s in sentences) else ""
        text = joiner.join(s.strip() for s in sentences)
        newer = self._pending_text.get(direction)
        if newer is not None:
            text = self._join_recognized_text(text, newer.text)
        now = time.monotonic()
        self._pending_text[direction] = replace(
            pending, text=text, pcm=b"", chunk_count=1, updated_at=now, created_at=now
        )
        # Otherwise the retry looks like a repeat of what was just committed.
        self._last_committed.pop(direction, None)

    def _translate_stable_text(self, direction: Direction, pending: _PendingText) -> None:
        # A single chunk was already decoded as a whole utterance; a second pass
        # over the same audio only adds one ASR run of latency before MT.
        final_text = None
        if pending.chunk_count > 1:
            final_text = self._asr_for(direction).recognize(
                pending.pcm + _FINAL_ASR_SILENCE,
                language=pending.source_lang,
            )
        if self._stop_event.is_set():
            return
        if final_text:
            final_text = final_text.strip()
            provisional_len = len("".join(ch for ch in pending.text if ch.isalnum()))
            final_len = len("".join(ch for ch in final_text if ch.isalnum()))
            # A final pass can correct chunk-boundary words, but a much shorter
            # result must not erase the beginning of the accumulated sentence.
            if final_len >= max(1, int(provisional_len * 0.85)):
                changed = final_text != pending.text
                pending.text = final_text
                if changed:
                    self._callbacks.on_source_text(direction, final_text, False)

        deduped = self._dedupe_committed(direction, pending.text)
        if deduped is None:
            self._callbacks.on_source_text(direction, "", True)
            return
        pending.text = deduped

        sentences = split_sentences(pending.text, min_chars=self._sentence_min_chars)
        if not sentences:
            self._callbacks.on_source_text(direction, "", True)
            return

        any_ok = False
        for index, sentence in enumerate(sentences):
            self._callbacks.on_source_text(direction, sentence, False)
            translated = self._translate_sentence(
                sentence,
                source_lang=pending.source_lang,
                target_lang=pending.target_lang,
            )
            if self._stop_event.is_set():
                return
            if not translated:
                if getattr(self._mt, "warming_up", False):
                    # MT began loading (e.g. switched to its fallback): keep the rest queued.
                    self._callbacks.on_status(_MT_LOADING_STATUS)
                    self._requeue_untranslated(direction, pending, sentences[index:])
                    return
                self._rate_limited_diag(
                    f"MT 无结果 字数={len(sentence)} {pending.source_lang}→{pending.target_lang}"
                )
                self._callbacks.on_source_text(direction, "", True)
                continue

            any_ok = True
            self._callbacks.on_translated_text(direction, translated, True)
            if pending.play_voice:
                tts_text = ensure_terminal_punct(translated)
                self._enqueue_tts(direction, tts_text, pending.target_lang)

        if not any_ok:
            self._rate_limited_warning("经济模式翻译失败（MT 无结果）")

    def _translate_sentence(
        self, sentence: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        glossary = dict(getattr(self._config, "glossary", None) or {})
        protected, restores = _protect_glossary(sentence, glossary)
        translated = self._mt.translate(protected, source_lang=source_lang, target_lang=target_lang)
        if not translated:
            return None
        translated = _restore_glossary(translated.strip(), restores)
        # Also apply target-side glossary values / any remaining source keys.
        translated = _apply_glossary(translated, glossary)
        return translated.strip() or None

    @staticmethod
    def _asr_unavailable_message(asr: AsrBackend) -> str | None:
        """If ASR finished loading unsuccessfully, return a user-facing error."""
        if getattr(asr, "configured", False):
            return None
        if getattr(asr, "warming_up", False):
            return None
        if isinstance(asr, LiveCaptionsAsr):
            return _ERR_NO_LIVECAPTIONS
        if isinstance(asr, FasterWhisperAsr):
            return _ERR_NO_WHISPER
        if isinstance(asr, SherpaOnnxAsr):
            return _ERR_NO_SHERPA
        if isinstance(asr, QwenLocalAsr):
            return _ERR_NO_QWEN_ASR
        if getattr(asr, "_failed", False):
            return "经济模式 ASR 不可用（见日志）"
        return None

    def _process_job(self, job: _Job) -> None:
        asr = self._asr_for(job.direction)
        if not getattr(asr, "configured", False) and getattr(asr, "warming_up", False):
            self._defer_asr_job(job)
            self._rate_limited_diag("ASR 加载中，已保留音频等待识别")
            return
        pcm_ms = len(job.pcm) / 2.0 / 16000.0 * 1000.0
        t0 = time.perf_counter()
        self._rate_limited_diag(
            f"经济模式 job 开始 {job.direction.value} "
            f"pcm_ms={pcm_ms:.0f} src={job.source_lang}→{job.target_lang}"
        )

        t_asr_s = time.perf_counter()
        overlap = self._asr_audio_tail[job.direction]
        asr_pcm = overlap + job.pcm
        self._asr_audio_tail[job.direction] = asr_pcm[-_ASR_OVERLAP_BYTES:]
        text = asr.recognize(asr_pcm, language=job.source_lang)
        if self._stop_event.is_set():
            return
        t_asr = (time.perf_counter() - t_asr_s) * 1000.0
        if not text:
            unavailable = self._asr_unavailable_message(asr)
            if unavailable:
                self._rate_limited_error(unavailable)
            elif getattr(asr, "warming_up", False):
                self._asr_audio_tail[job.direction] = overlap
                self._defer_asr_job(job)
                self._rate_limited_diag("ASR 加载中，已保留音频等待识别")
            else:
                self._rate_limited_diag(f"ASR 无结果 {job.direction.value} pcm_ms={pcm_ms:.0f}")
            return
        text = text.strip()
        if not text:
            self._rate_limited_diag(f"ASR 无结果 {job.direction.value} pcm_ms={pcm_ms:.0f}")
            return

        self._buffer_recognized_text(job, text)
        total = (time.perf_counter() - t0) * 1000.0
        # Speech content stays out of the app log unless debugging is enabled.
        logger.info(
            f"[ASR] 音频时长={pcm_ms:.0f}ms ASR={t_asr:.0f}ms 处理={total:.0f}ms | 字数={len(text)}"
        )
        logger.debug(f"[ASR] 短语: {text[:40]}")

    def _rate_limited_error(self, msg: str) -> None:
        now = time.time()
        if now - self._last_err_at < _ERR_LOG_INTERVAL:
            return
        self._last_err_at = now
        self._callbacks.on_error(msg)
        self._callbacks.on_status(msg)

    def _rate_limited_warning(self, msg: str) -> None:
        """Per-sentence degradation: log line only, never an error dialog."""
        now = time.time()
        if now - self._last_warn_at.get(msg, 0.0) < _ERR_LOG_INTERVAL:
            return
        self._last_warn_at[msg] = now
        logger.warning(msg)
        self._callbacks.on_status(msg)

    def _rate_limited_diag(self, msg: str) -> None:
        now = time.time()
        if now - self._last_diag_at < _DIAG_LOG_INTERVAL:
            return
        self._last_diag_at = now
        logger.info(msg)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                with suppress(Exception):
                    self._queue.task_done()

    def _stop_worker(self) -> None:
        self._stop_event.set()
        with suppress(Exception):
            self._queue.put_nowait(None)
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        if worker is None or not worker.is_alive():
            self._worker = None
        self._drain_queue()

    def _stop_tts_worker(self) -> None:
        self._tts_stop_event.set()
        with suppress(Exception):
            self._tts_queue.put_nowait(None)
        worker = self._tts_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        if worker is None or not worker.is_alive():
            self._tts_worker = None
        while True:
            try:
                self._tts_queue.get_nowait()
            except queue.Empty:
                break
            else:
                with suppress(Exception):
                    self._tts_queue.task_done()

    def _stop_backends(self) -> None:
        for asr in self._started_asrs:
            with suppress(Exception):
                asr.stop()
        self._started_asrs.clear()
        if self._tts_started:
            with suppress(Exception):
                self._tts.stop()
            self._tts_started = False
        if not self._backends_started:
            return
        with suppress(Exception):
            self._mt.stop()
        self._backends_started = False

    def close(self) -> None:
        for direction in list(self._active):
            self.stop_direction(direction)
        self._active.clear()
        self._play_voice.clear()
        for buf in self._buffers.values():
            buf.clear()
        self._stop_worker()
        self._stop_tts_worker()
        self._deferred_asr_jobs.clear()
        self._pending_text.clear()
        self._stop_backends()
