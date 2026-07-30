"""Economy translation engine: utterance → ASR → MT → TTS."""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass

from src.engines.base import EngineCallbacks, EngineMode
from src.engines.pipeline.asr import AsrBackend, DashScopeAsr, UnconfiguredAsr
from src.engines.pipeline.kokoro_tts import KokoroOnnxTts
from src.engines.pipeline.live_captions_asr import LiveCaptionsAsr
from src.engines.pipeline.mt import ArgosMt, AutoMt, MtBackend, MyMemoryMt, UnconfiguredMt
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
_ERR_LOG_INTERVAL = 5.0
_MT_LOADING_STATUS = "本地翻译模型加载中"


@dataclass
class _Job:
    direction: Direction
    pcm: bytes
    source_lang: str
    target_lang: str
    play_voice: bool


def resolve_dashscope_api_key(config: AppConfigModel) -> str:
    key = (getattr(config, "economy_dashscope_api_key", "") or "").strip()
    if key:
        return key
    return (os.environ.get("DASHSCOPE_API_KEY") or "").strip()


def build_economy_backends(
    config: AppConfigModel,
) -> tuple[AsrBackend, MtBackend, TtsBackend]:
    """Factory helpers used by EconomyPipelineEngine and tests."""
    device_pref = getattr(config, "device_preference", "auto") or "auto"
    asr_pref = getattr(config, "economy_asr_backend", "dashscope") or "dashscope"
    if asr_pref == "live_captions":
        # Windows Live Captions (系统级 ASR, 低占用高准确率, 仅 Win11 22H2+)
        asr: AsrBackend = LiveCaptionsAsr(device_preference=device_pref)
    elif asr_pref == "local":
        local_model = getattr(config, "economy_asr_local_model", None) or "faster-whisper-medium"
        hotwords = list(getattr(config, "hotwords", None) or [])
        # faster-whisper 后端 (Whisper medium, 噪声鲁棒, 接近辅助字幕准确率)
        if whisper_model_info(local_model):
            asr = FasterWhisperAsr(
                model_id=local_model,
                device_preference=device_pref,
                hotwords=hotwords,
            )
        else:
            # sherpa-onnx 后端 (SenseVoice 离线 / Zipformer 流式)
            asr = SherpaOnnxAsr(
                model_id=local_model,
                device_preference=device_pref,
                hotwords=hotwords,
            )
    else:
        api_key = resolve_dashscope_api_key(config)
        model = getattr(config, "economy_asr_model", None) or "fun-asr-realtime"
        if api_key:
            asr = DashScopeAsr(api_key=api_key, model=model)
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

    voice_en = getattr(config, "economy_kokoro_voice_en", None) or "af_heart"
    voice_zh = getattr(config, "economy_kokoro_voice_zh", None) or "zf_xiaoxiao"
    tts_pref = getattr(config, "economy_tts_backend", "kokoro") or "kokoro"
    if tts_pref == "edge":
        tts: TtsBackend = EdgePcmTts()
    elif tts_pref == "kokoro":
        tts = AutoTts(
            prefer="kokoro",
            kokoro=KokoroOnnxTts(
                voice_en=voice_en,
                voice_zh=voice_zh,
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
                device_preference=device_pref,
            ),
            voice_en=voice_en,
            voice_zh=voice_zh,
        )

    return asr, mt, tts


def _apply_glossary(text: str, glossary: dict[str, str]) -> str:
    if not text or not glossary:
        return text
    out = text
    # Longer keys first to avoid partial stomping.
    for src, dst in sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True):
        if src:
            out = out.replace(src, dst)
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
        if asr is None and mt is None and tts is None:
            asr, mt, tts = build_economy_backends(config)
        self._asr: AsrBackend = asr or UnconfiguredAsr()
        self._mt: MtBackend = mt or UnconfiguredMt()
        self._tts: TtsBackend = tts or UnconfiguredTts()
        self._active: set[Direction] = set()
        self._play_voice: dict[Direction, bool] = {}
        silence = int(getattr(config, "economy_utterance_silence_ms", 300) or 300)
        min_ms = int(getattr(config, "economy_utterance_min_ms", 400) or 400)
        max_ms = int(getattr(config, "economy_utterance_max_ms", 8000) or 8000)
        # 连续音频 (如游戏原声) 的软切分参数: 累积 ≥soft_split_ms 且尾部
        # RMS <tail_rms_threshold 时立即切分, 避免等到 max_ms 才吐出.
        # soft_split_ms=5000: 游戏原声连续, 3 秒切分太频繁会产生大量短片段
        # 送进 SenseVoice 产生噪点; 5 秒在延迟和切分频率间取得平衡.
        soft_split_ms = int(getattr(config, "economy_utterance_soft_split_ms", 5000) or 5000)
        tail_rms_threshold = float(
            getattr(config, "economy_utterance_tail_rms", 0.003) or 0.003
        )
        self._buffers: dict[Direction, UtteranceBuffer] = {
            Direction.OUTBOUND: UtteranceBuffer(
                end_silence_ms=silence,
                min_ms=min_ms,
                max_ms=max_ms,
                soft_split_ms=soft_split_ms,
                tail_rms_threshold=tail_rms_threshold,
            ),
            Direction.INBOUND: UtteranceBuffer(
                end_silence_ms=silence,
                min_ms=min_ms,
                max_ms=max_ms,
                soft_split_ms=soft_split_ms,
                tail_rms_threshold=tail_rms_threshold,
            ),
        }
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_err_at = 0.0
        self._backends_started = False

    @property
    def engine_id(self) -> EngineMode:
        return "economy"

    @property
    def active_directions(self) -> frozenset[Direction]:
        return frozenset(self._active)

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
        asr_pref = getattr(self._config, "economy_asr_backend", "dashscope") or "dashscope"
        # 本地 ASR / Live Captions 允许在加载中启动 (warming_up); 云端 ASR 必须已 configured
        if asr_pref in ("local", "live_captions"):
            # 首次调用: 先触发 start() 让 ASR 进入 warming_up 状态
            # (FasterWhisperAsr / SherpaOnnxAsr / LiveCaptionsAsr.start() 会异步加载)
            if not self._asr.configured and not getattr(self._asr, "warming_up", False):
                self._asr.start()
            # 再次检查: 若仍非 configured 且非 warming_up, 说明此前加载已失败
            if not self._asr.configured and not getattr(self._asr, "warming_up", False):
                # 根据后端类型选择错误提示
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
        if asr_pref == "live_captions":
            status = _STATUS_LIVECAPTIONS
        elif asr_pref == "local":
            status = _STATUS_LOCAL
        else:
            status = _STATUS_CLOUD
        self._callbacks.on_status(status)
        if asr_pref in ("local", "live_captions") and not self._asr.configured:
            if getattr(self._asr, "warming_up", False):
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
        if not getattr(self._tts, "configured", False) and play_voice:
            if getattr(self._tts, "warming_up", False):
                logger.info("Kokoro TTS 加载中，语音将在就绪后播放")
                self._callbacks.on_status("正在下载 Kokoro…")
        logger.info(f"Economy channel started: {direction.value} asr={asr_pref}")
        return True

    def stop_direction(self, direction: Direction) -> None:
        self._active.discard(direction)
        self._play_voice.pop(direction, None)
        buf = self._buffers.get(direction)
        if buf is not None:
            buf.clear()
        if not self._active:
            self._stop_backends()

    def send_pcm(self, direction: Direction, pcm: bytes) -> None:
        if direction not in self._active or not pcm:
            return
        buf = self._buffers[direction]
        buf.push(pcm)
        chunk = buf.poll()
        if chunk:
            self._enqueue(direction, chunk)

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
            self._rate_limited_error("经济模式队列已满，丢弃一句")

    def _poll_active_buffers(self) -> None:
        """Flush utterances whose silence gap elapsed even if VAD stopped PCM."""
        for direction in list(self._active):
            buf = self._buffers.get(direction)
            if buf is None:
                continue
            chunk = buf.poll()
            if chunk:
                self._enqueue(direction, chunk)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_active_buffers()
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

    def _process_job(self, job: _Job) -> None:
        pcm_ms = len(job.pcm) / 2.0 / 16000.0 * 1000.0
        t0 = time.perf_counter()

        t_asr_s = time.perf_counter()
        text = self._asr.recognize(job.pcm, language=job.source_lang)
        t_asr = (time.perf_counter() - t_asr_s) * 1000.0
        if not text:
            return
        text = text.strip()
        if not text:
            return
        self._callbacks.on_source_text(job.direction, text, True)

        t_mt_s = time.perf_counter()
        translated = self._mt.translate(
            text, source_lang=job.source_lang, target_lang=job.target_lang
        )
        t_mt = (time.perf_counter() - t_mt_s) * 1000.0
        if not translated:
            if getattr(self._mt, "warming_up", False):
                self._callbacks.on_status(_MT_LOADING_STATUS)
                return
            self._rate_limited_error("经济模式翻译失败（MT 无结果）")
            return
        glossary = dict(getattr(self._config, "glossary", None) or {})
        translated = _apply_glossary(translated.strip(), glossary)
        self._callbacks.on_translated_text(job.direction, translated, True)

        if not job.play_voice:
            total = (time.perf_counter() - t0) * 1000.0
            logger.info(
                f"[延迟] 音频时长={pcm_ms:.0f}ms ASR={t_asr:.0f}ms MT={t_mt:.0f}ms "
                f"处理={total:.0f}ms | 原文: {text[:20]}"
            )
            return

        t_tts_s = time.perf_counter()
        pcm = self._tts.synthesize(translated, language=job.target_lang)
        t_tts = (time.perf_counter() - t_tts_s) * 1000.0
        if pcm:
            total = (time.perf_counter() - t0) * 1000.0
            logger.info(
                f"[延迟] 音频时长={pcm_ms:.0f}ms ASR={t_asr:.0f}ms MT={t_mt:.0f}ms TTS={t_tts:.0f}ms "
                f"处理={total:.0f}ms | 原文: {text[:20]}"
            )
            self._callbacks.on_audio(job.direction, pcm)
        elif getattr(self._tts, "warming_up", False):
            self._callbacks.on_status("正在下载 Kokoro…")
        else:
            self._rate_limited_error("经济模式 TTS 失败（无音频）")

    def _rate_limited_error(self, msg: str) -> None:
        now = time.time()
        if now - self._last_err_at < _ERR_LOG_INTERVAL:
            return
        self._last_err_at = now
        self._callbacks.on_error(msg)
        self._callbacks.on_status(msg)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    def _stop_worker(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        self._worker = None
        self._drain_queue()

    def _stop_backends(self) -> None:
        if not self._backends_started:
            return
        with_suppress = __import__("contextlib").suppress
        with with_suppress(Exception):
            self._asr.stop()
        with with_suppress(Exception):
            self._mt.stop()
        with with_suppress(Exception):
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
