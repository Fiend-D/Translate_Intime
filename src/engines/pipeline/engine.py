"""Economy translation engine: utterance → ASR → MT → TTS."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass

from src.engines.base import EngineCallbacks, EngineMode
from src.engines.pipeline.asr import AsrBackend, DashScopeAsr, UnconfiguredAsr
from src.engines.pipeline.kokoro_tts import KokoroOnnxTts
from src.engines.pipeline.live_captions_asr import LiveCaptionsAsr
from src.engines.pipeline.mt import ArgosMt, AutoMt, MtBackend, MyMemoryMt, UnconfiguredMt
from src.engines.pipeline.sentence_split import (
    ensure_terminal_punct,
    split_sentences,
)
from src.engines.pipeline.sherpa_asr import SherpaOnnxAsr
from src.engines.pipeline.tts import AutoTts, EdgePcmTts, TtsBackend, UnconfiguredTts
from src.engines.pipeline.utterance import UtteranceBuffer
from src.engines.pipeline.whisper_asr import FasterWhisperAsr, whisper_model_info
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.utils.logger import logger

_STATUS_CLOUD = "经济模式：阿里云 Fun-ASR + NLLB 本地翻译 + Kokoro TTS"
_STATUS_LOCAL = "经济模式：本地 ASR + NLLB 本地翻译 + Kokoro TTS"
_STATUS_LIVECAPTIONS = "经济模式：Windows Live Captions + NLLB 本地翻译 + Kokoro TTS"
_ERR_NO_ASR = "经济模式需要 DashScope API Key（配置项或环境变量 DASHSCOPE_API_KEY）"
_ERR_NO_SDK = "经济模式需要安装 dashscope：pip install 'dashscope>=1.20'"
_ERR_NO_SHERPA = (
    "经济模式本地 ASR 不可用：未安装 sherpa-onnx (pip install sherpa-onnx) "
    "或模型下载失败，请查看日志"
)
_ERR_NO_WHISPER = (
    "经济模式本地 ASR 不可用：未安装 faster-whisper (pip install faster-whisper) "
    "或模型下载失败，请查看日志"
)
_ERR_NO_LIVECAPTIONS = (
    "Windows Live Captions 不可用：仅支持 Windows 11 22H2+，"
    "需安装 uiautomation (pip install uiautomation) 并开启系统实时字幕功能"
)
_WARN_LC_FALLBACK = (
    "Live Captions 仅支持 Windows；已自动切换为本地 ASR（faster-whisper / sherpa）"
)
_ERR_LOG_INTERVAL = 5.0
_DIAG_LOG_INTERVAL = 5.0
_MT_LOADING_STATUS = "本地翻译模型加载中"
_ASR_OVERLAP_BYTES = 16000 * 2  # 1 second of PCM16 mono at 16 kHz
_FINAL_ASR_SILENCE = b"\x00\x00" * int(16000 * 0.4)
_FINAL_ASR_MAX_BYTES = 30 * 16000 * 2

# Local / offline ASR backends never require a DashScope API key.
LOCAL_ASR_BACKENDS = frozenset({"local", "live_captions", "sherpa", "whisper"})
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
    if pref in LOCAL_ASR_BACKENDS or pref == "dashscope":
        return pref
    return default_economy_asr_backend()


def economy_asr_requires_dashscope(config: AppConfigModel | object) -> bool:
    """True only for cloud DashScope / Fun-ASR backends."""
    return resolve_economy_asr_backend(config) not in LOCAL_ASR_BACKENDS


def _build_local_asr(config: AppConfigModel, device_pref: str) -> AsrBackend:
    local_model = getattr(config, "economy_asr_local_model", None) or "faster-whisper-medium"
    hotwords = list(getattr(config, "hotwords", None) or [])
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


def build_economy_backends(
    config: AppConfigModel,
) -> tuple[AsrBackend, MtBackend, TtsBackend]:
    """Factory helpers used by EconomyPipelineEngine and tests."""
    device_pref = getattr(config, "device_preference", "auto") or "auto"
    raw_pref = (getattr(config, "economy_asr_backend", None) or "").strip()
    asr_pref = resolve_economy_asr_backend(config)
    if raw_pref == "live_captions" and asr_pref == "local":
        logger.warning(_WARN_LC_FALLBACK)
    if asr_pref == "live_captions":
        # Windows Live Captions (系统级 ASR, 低占用高准确率, 仅 Win11 22H2+)
        asr: AsrBackend = LiveCaptionsAsr(device_preference=device_pref)
    elif asr_pref in LOCAL_ASR_BACKENDS:
        asr = _build_local_asr(config, device_pref)
    else:
        api_key = resolve_dashscope_api_key(config)
        model = getattr(config, "economy_asr_model", None) or "fun-asr-realtime"
        hotwords = list(getattr(config, "hotwords", None) or [])
        if api_key:
            asr = DashScopeAsr(api_key=api_key, model=model, hotwords=hotwords)
        else:
            asr = UnconfiguredAsr()

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
            device_preference=device_pref,
        )
    else:
        # auto → NLLB first, then Argos / MyMemory
        mt = AutoMt(
            prefer="auto",
            nllb_model=nllb_model,
            device_preference=device_pref,
        )

    voice_en = getattr(config, "economy_kokoro_voice_en", None) or "af_bella"
    voice_zh = getattr(config, "economy_kokoro_voice_zh", None) or "zf_xiaoxiao"
    speed = float(getattr(config, "economy_kokoro_speed", 0.92) or 0.92)
    tts_pref = getattr(config, "economy_tts_backend", "kokoro") or "kokoro"
    if tts_pref == "edge":
        tts: TtsBackend = EdgePcmTts()
    elif tts_pref == "kokoro":
        tts = AutoTts(
            prefer="kokoro",
            kokoro=KokoroOnnxTts(
                voice_en=voice_en,
                voice_zh=voice_zh,
                speed=speed,
                device_preference=device_pref,
            ),
            voice_en=voice_en,
            voice_zh=voice_zh,
        )
    else:
        tts = AutoTts(
            prefer="auto",
            kokoro=KokoroOnnxTts(
                voice_en=voice_en,
                voice_zh=voice_zh,
                speed=speed,
                device_preference=device_pref,
            ),
            voice_en=voice_en,
            voice_zh=voice_zh,
        )

    return asr, mt, tts


def _apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """Simple left-to-right replace of glossary keys (longer keys first)."""
    if not text or not glossary:
        return text
    out = text
    for src, dst in sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True):
        if src:
            out = out.replace(src, dst)
    return out


def _protect_glossary(
    text: str, glossary: dict[str, str]
) -> tuple[str, list[tuple[str, str]]]:
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
        asr: AsrBackend | None = None,
        mt: MtBackend | None = None,
        tts: TtsBackend | None = None,
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        raw_pref = (getattr(config, "economy_asr_backend", None) or "").strip()
        self._lc_fallback = False
        if asr is None and mt is None and tts is None:
            resolved = resolve_economy_asr_backend(config)
            self._lc_fallback = (
                raw_pref == "live_captions" and resolved == "local"
            )
            asr, mt, tts = build_economy_backends(config)
        self._asr: AsrBackend = asr or UnconfiguredAsr()
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
        tail_rms_threshold = float(
            getattr(config, "economy_utterance_tail_rms", 0.003) or 0.003
        )
        self._sentence_min_chars = int(
            getattr(config, "economy_sentence_min_chars", 4) or 4
        )
        self._sentence_pause_ms = int(
            getattr(config, "economy_sentence_pause_ms", 900) or 900
        )
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
        self._pending_text: dict[Direction, _PendingText] = {}
        self._asr_audio_tail: dict[Direction, bytes] = {
            Direction.OUTBOUND: b"",
            Direction.INBOUND: b"",
        }
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_err_at = 0.0
        self._last_diag_at = 0.0
        self._backends_started = False

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
        if getattr(self._asr, "warming_up", False):
            loading.append("本地 ASR")
        if getattr(self._mt, "warming_up", False):
            loading.append("NLLB 翻译")
        if getattr(self._tts, "warming_up", False):
            loading.append("Kokoro 语音")
        return tuple(loading)

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

    def _ensure_backends(self) -> None:
        if self._backends_started:
            return
        self._asr.start()
        self._mt.start()
        self._tts.start()
        self._backends_started = True

    def start_direction(self, direction: Direction, *, play_voice: bool = False) -> bool:
        asr_pref = resolve_economy_asr_backend(self._config)
        # 本地 ASR / Live Captions 允许在加载中启动 (warming_up); 云端 ASR 必须已 configured
        if asr_pref in LOCAL_ASR_BACKENDS:
            if not self._asr.configured and not getattr(self._asr, "warming_up", False):
                self._asr.start()
            if not self._asr.configured and not getattr(self._asr, "warming_up", False):
                if isinstance(self._asr, LiveCaptionsAsr):
                    msg = _ERR_NO_LIVECAPTIONS
                elif isinstance(self._asr, FasterWhisperAsr):
                    msg = _ERR_NO_WHISPER
                else:
                    msg = _ERR_NO_SHERPA
                self._callbacks.on_status(msg)
                self._callbacks.on_error(msg)
                self._callbacks.on_engine_status(direction, "economy", "error")
                logger.warning(msg)
                return False
        else:
            if not self._asr.configured:
                key = resolve_dashscope_api_key(self._config)
                msg = _ERR_NO_SDK if key else _ERR_NO_ASR
                self._callbacks.on_status(msg)
                self._callbacks.on_error(msg)
                self._callbacks.on_engine_status(direction, "economy", "error")
                logger.warning(msg)
                return False

        self._play_voice[direction] = play_voice
        self._ensure_backends()
        self._ensure_worker()
        self._active.add(direction)
        self._callbacks.on_engine_status(direction, "economy", "ready")
        if self._lc_fallback:
            logger.warning(_WARN_LC_FALLBACK)
            self._callbacks.on_status(_WARN_LC_FALLBACK)
            self._lc_fallback = False  # toast once per engine lifetime
        if asr_pref == "live_captions":
            status = _STATUS_LIVECAPTIONS
        elif asr_pref in LOCAL_ASR_BACKENDS:
            status = _STATUS_LOCAL
        else:
            status = _STATUS_CLOUD
        self._callbacks.on_status(status)
        speed = float(getattr(self._config, "economy_kokoro_speed", 0.92) or 0.92)
        logger.info(f"Kokoro speed={speed:.2f}, sentence TTS")
        if (
            asr_pref in LOCAL_ASR_BACKENDS
            and not self._asr.configured
            and getattr(self._asr, "warming_up", False)
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
            logger.info("Kokoro TTS 加载中，语音将在就绪后播放")
            self._callbacks.on_status("正在下载 Kokoro…")
        logger.info(f"Economy channel started: {direction.value} asr={asr_pref}")
        return True

    def stop_direction(self, direction: Direction) -> None:
        # Keep already-recognized text for the worker to translate after the
        # channel stops. Dropping it here loses the last sentence whenever the
        # user stops during the short sentence-stability window.
        self._asr_audio_tail[direction] = b""
        self._active.discard(direction)
        self._play_voice.pop(direction, None)
        buf = self._buffers.get(direction)
        if buf is not None:
            buf.clear()
        # Keep loaded local models resident. ``close()`` still releases them on
        # app exit or an explicit model/backend change.

    def send_pcm(self, direction: Direction, pcm: bytes) -> None:
        if direction not in self._active or not pcm:
            return
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
            self._rate_limited_error("经济模式队列已满，丢弃一句")

    def _log_flush(self, direction: Direction, pcm: bytes) -> None:
        pcm_ms = len(pcm) / 2.0 / 16000.0 * 1000.0
        self._rate_limited_diag(
            f"经济模式 utterance 已切分 {direction.value} "
            f"bytes={len(pcm)} ms={pcm_ms:.0f}"
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

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_active_buffers()
            self._flush_stable_text()
            try:
                job = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                self._process_job(job)
            except Exception as exc:
                self._rate_limited_error(f"经济模式处理失败: {exc}")
                logger.exception("Economy job failed")
            finally:
                self._queue.task_done()

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
        self._pending_text[job.direction] = _PendingText(
            text=combined,
            source_lang=job.source_lang,
            target_lang=job.target_lang,
            play_voice=job.play_voice,
            updated_at=time.monotonic(),
            created_at=pending.created_at if pending is not None else time.monotonic(),
            pcm=((pending.pcm if pending is not None else b"") + job.pcm)[
                -_FINAL_ASR_MAX_BYTES:
            ],
            chunk_count=(pending.chunk_count + 1 if pending is not None else 1),
        )
        # Show recognition immediately, but do not translate an unstable phrase.
        self._callbacks.on_source_text(job.direction, combined, False)

    def _flush_stable_text(self, *, force: bool = False) -> None:
        now = time.monotonic()
        base_pause_s = self._sentence_pause_ms / 1000.0
        for direction, pending in list(self._pending_text.items()):
            terminal = pending.text.rstrip().endswith(("。", "！", "？", ".", "!", "?"))
            if terminal:
                pause_s = min(base_pause_s, 0.35)
            elif direction == Direction.INBOUND:
                # Game/system audio commonly arrives in ~2 s speech bursts. A
                # 900 ms timeout finalizes every burst as a phrase; wait across
                # one burst boundary unless the accumulated text reaches a cap.
                pause_s = max(base_pause_s, 2.4)
            else:
                pause_s = base_pause_s
            max_wait_reached = (
                now - pending.created_at >= self._sentence_max_wait_ms / 1000.0
            )
            # Inbound ASR normally produces one hypothesis per ~2 s audio
            # block. Two blocks provide enough sentence context; translate at
            # once instead of spending another full pause window waiting for a
            # third block that would push visible latency beyond three seconds.
            context_ready = (
                direction == Direction.INBOUND
                and pending.chunk_count >= 2
                and now - pending.created_at >= 1.5
            )
            # Stability is about the recognized hypothesis, not raw capture.
            # Continuous game/system PCM can arrive indefinitely after ASR has
            # stopped changing the text; using buffer.last_push_at here keeps
            # postponing MT forever.
            if (
                not force
                and not max_wait_reached
                and not context_ready
                and now - pending.updated_at < pause_s
            ):
                continue
            self._pending_text.pop(direction, None)
            self._translate_stable_text(direction, pending)

    def _translate_stable_text(
        self, direction: Direction, pending: _PendingText
    ) -> None:
        final_text = self._asr.recognize(
            pending.pcm + _FINAL_ASR_SILENCE,
            language=pending.source_lang,
        )
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

        sentences = split_sentences(
            pending.text, min_chars=self._sentence_min_chars
        )
        if not sentences:
            self._callbacks.on_source_text(direction, "", True)
            return

        any_ok = False
        any_audio = False
        for sentence in sentences:
            self._callbacks.on_source_text(direction, sentence, False)
            translated = self._translate_sentence(
                sentence,
                source_lang=pending.source_lang,
                target_lang=pending.target_lang,
            )
            if not translated:
                if getattr(self._mt, "warming_up", False):
                    self._callbacks.on_status(_MT_LOADING_STATUS)
                    self._callbacks.on_source_text(direction, "", True)
                    return
                self._rate_limited_diag(
                    f"MT 无结果 sentence={sentence[:24]!r} "
                    f"{pending.source_lang}→{pending.target_lang}"
                )
                self._callbacks.on_source_text(direction, "", True)
                continue

            any_ok = True
            self._callbacks.on_translated_text(direction, translated, True)
            if pending.play_voice:
                tts_text = ensure_terminal_punct(translated)
                pcm = self._tts.synthesize(tts_text, language=pending.target_lang)
                if pcm:
                    any_audio = True
                    self._callbacks.on_audio(direction, pcm)
                elif getattr(self._tts, "warming_up", False):
                    self._callbacks.on_status("正在下载 Kokoro…")

        if not any_ok:
            self._rate_limited_error("经济模式翻译失败（MT 无结果）")
        elif (
            pending.play_voice
            and not any_audio
            and not getattr(self._tts, "warming_up", False)
        ):
            self._rate_limited_error("经济模式 TTS 失败（无音频）")

    def _translate_sentence(
        self, sentence: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        glossary = dict(getattr(self._config, "glossary", None) or {})
        protected, restores = _protect_glossary(sentence, glossary)
        translated = self._mt.translate(
            protected, source_lang=source_lang, target_lang=target_lang
        )
        if not translated:
            return None
        translated = _restore_glossary(translated.strip(), restores)
        # Also apply target-side glossary values / any remaining source keys.
        translated = _apply_glossary(translated, glossary)
        return translated.strip() or None

    def _asr_unavailable_message(self) -> str | None:
        """If ASR finished loading unsuccessfully, return a user-facing error."""
        if getattr(self._asr, "configured", False):
            return None
        if getattr(self._asr, "warming_up", False):
            return None
        if isinstance(self._asr, LiveCaptionsAsr):
            return _ERR_NO_LIVECAPTIONS
        if isinstance(self._asr, FasterWhisperAsr):
            return _ERR_NO_WHISPER
        if isinstance(self._asr, SherpaOnnxAsr):
            return _ERR_NO_SHERPA
        if getattr(self._asr, "_failed", False):
            return "经济模式 ASR 不可用（见日志）"
        return None

    def _process_job(self, job: _Job) -> None:
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
        text = self._asr.recognize(asr_pcm, language=job.source_lang)
        t_asr = (time.perf_counter() - t_asr_s) * 1000.0
        if not text:
            unavailable = self._asr_unavailable_message()
            if unavailable:
                self._rate_limited_error(unavailable)
            elif getattr(self._asr, "warming_up", False):
                self._rate_limited_diag("ASR 加载中，暂无识别结果")
            else:
                self._rate_limited_diag(
                    f"ASR 无结果 {job.direction.value} pcm_ms={pcm_ms:.0f}"
                )
            return
        text = text.strip()
        if not text:
            self._rate_limited_diag(
                f"ASR 无结果 {job.direction.value} pcm_ms={pcm_ms:.0f}"
            )
            return

        self._buffer_recognized_text(job, text)
        total = (time.perf_counter() - t0) * 1000.0
        logger.info(
            f"[ASR] 音频时长={pcm_ms:.0f}ms ASR={t_asr:.0f}ms "
            f"处理={total:.0f}ms | 短语: {text[:40]}"
        )

    def _rate_limited_error(self, msg: str) -> None:
        now = time.time()
        if now - self._last_err_at < _ERR_LOG_INTERVAL:
            return
        self._last_err_at = now
        self._callbacks.on_error(msg)
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
        self._worker = None
        self._drain_queue()

    def _stop_backends(self) -> None:
        if not self._backends_started:
            return
        with suppress(Exception):
            self._asr.stop()
        with suppress(Exception):
            self._mt.stop()
        with suppress(Exception):
            self._tts.stop()
        self._backends_started = False

    def close(self) -> None:
        for direction in list(self._active):
            self.stop_direction(direction)
        self._active.clear()
        self._play_voice.clear()
        for buf in self._buffers.values():
            buf.clear()
        self._stop_worker()
        self._stop_backends()
