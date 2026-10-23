"""Translation session orchestrator — capture/VAD/UI own pipeline; engines own transport."""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from src.audio.session_ducker import Mode as DuckerMode
from src.audio.session_ducker import SessionDucker
from src.core.audio_capture import AudioCapture
from src.core.audio_player import AudioPlayer
from src.core.echo_canceller import (
    AecResult,
    EchoCanceller,
    LoopbackEchoCanceller,
    PlaybackReference,
)
from src.core.exceptions import EngineLoadError
from src.core.speech_gate import SpeechGate
from src.core.volc_engine import resolve_volc_credentials
from src.engines.base import EngineCallbacks, EngineMode, TranslationEngine
from src.engines.factory import create_engine
from src.models.config import AppConfigModel
from src.models.enums import Direction, LanguageCode, SessionStatus
from src.models.session import TranslationSession
from src.models.subtitle import SubtitleEntry
from src.utils.logger import SubtitleLogger, configure_logging, logger
from src.utils.resource_paths import resolve_log_dir


@dataclass
class DirectionState:
    """Per-direction subtitle pairing state."""

    direction: Direction
    source_lang: LanguageCode = LanguageCode.ZH
    target_lang: LanguageCode = LanguageCode.EN
    last_text: str = ""
    last_source: str = ""
    last_translated: str = ""
    utterance_started_at: float = 0.0


class TranslationPipeline(QObject):
    """Real-time dual-channel translation with pluggable engines (volc / economy)."""

    subtitle_ready = pyqtSignal(SubtitleEntry)
    status_changed = pyqtSignal(str)
    engine_status_changed = pyqtSignal(str, str, str)  # direction, engine_type, status
    latency_reported = pyqtSignal(int)
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str)
    channel_lost = pyqtSignal(str, str)  # direction value, message
    usage_reported = pyqtSignal(str, dict)  # source, usage payload

    def __init__(self, config: AppConfigModel, registry: object | None = None) -> None:
        # registry kept optional for call-site compatibility; unused.
        del registry
        super().__init__()
        self._config = config
        self._session = TranslationSession()
        self._capture_outbound: AudioCapture | None = None
        self._capture_inbound: AudioCapture | None = None
        self._player: AudioPlayer | None = None
        self._directions: dict[Direction, DirectionState] = {}
        self._subtitle_logger = SubtitleLogger(
            resolve_log_dir(config.log_dir),
            enabled=config.transcript_logging_enabled,
            retention_days=config.transcript_retention_days,
        )
        self._running = False
        self._outbound_active = False
        self._inbound_active = False
        self._play_outbound_voice = False
        self._play_inbound_voice = False
        self._engine: TranslationEngine | None = None
        self._engine_built_with: tuple[tuple[str, str], ...] = ()
        self._input_device: int | str | None = None
        self._output_device: int | str | None = None
        self._loopback_device: int | str | None = None
        # Device the running game channel really captures; callers may reset _loopback_device.
        self._inbound_loopback: int | str | None = None
        # Game audio keeps flowing during TTS only after the feedback check fully ran.
        self._inbound_route_checked = False
        self._vad_outbound: SpeechGate | None = None
        self._vad_inbound: SpeechGate | None = None
        self._vad_outbound_filters = False
        self._vad_diag_at = 0.0
        self._vad_pass_chunks = 0
        self._vad_drop_chunks = 0
        self._ducker: SessionDucker | None = None

        # --- 回灌抑制（feedback suppression） ---
        # TTS 播放期间，输入音频**不丢弃**，而是缓存到 buffer。
        # 当 TTS 静音窗口解除（句间停顿够长）时，批量回放给翻译引擎。
        # 好处：连续说话中间停顿不会被截断；同时回灌不会触发新翻译。
        # 同时作用于 outbound (麦克风) 和 inbound (游戏字幕/loopback) 两个方向。
        self._tts_playing_until: float = 0.0
        self._tts_min_silence_sec: float = 2.0
        self._tts_silence_margin_sec: float = 0.5
        # 每个方向独立的回灌缓存
        self._feedback_buffers: dict[Direction, list[bytes]] = {
            Direction.OUTBOUND: [],
            Direction.INBOUND: [],
        }
        self._feedback_buffer_bytes: dict[Direction, int] = {
            Direction.OUTBOUND: 0,
            Direction.INBOUND: 0,
        }
        self._feedback_buffer_max_bytes: int = 5 * 16000 * 2  # 每方向最多延迟 5 秒
        # 译文只进虚拟线（会议软件），麦克风物理上听不到，无需闭麦缓存。
        self._outbound_echo_free = False
        self._aec: EchoCanceller | None = None
        # Our playback reaches the game capture (same device's monitor/loopback):
        # cancel it digitally instead of muting the game channel during TTS.
        self._inbound_feedback_route = False
        self._aec_inbound: LoopbackEchoCanceller | None = None
        self._reference: PlaybackReference | None = None
        self._barge_in_started_at: dict[Direction, float] = {
            Direction.OUTBOUND: 0.0,
            Direction.INBOUND: 0.0,
        }
        # 文本去重：8 秒内连续相同译文只播放一次
        self._last_played_text: dict[Direction, str] = {
            Direction.OUTBOUND: "",
            Direction.INBOUND: "",
        }
        self._last_played_at: dict[Direction, float] = {
            Direction.OUTBOUND: 0.0,
            Direction.INBOUND: 0.0,
        }
        self._text_dedup_window_sec: float = 8.0
        self._skip_next_audio_for: set[Direction] = set()
        # 保护 buffer 写入的锁（on_pcm 在录音线程被调用）
        self._feedback_buffer_lock = threading.Lock()

        configure_logging(resolve_log_dir(config.log_dir), config.debug_mode)

    @property
    def mode(self) -> str:
        if self._engine is not None:
            return self._engine.engine_id
        return getattr(self._config, "translation_mode", "volc") or "volc"

    def is_channel_active(self, direction: Direction) -> bool:
        if direction == Direction.OUTBOUND:
            return self._outbound_active
        return self._inbound_active

    def active_channels(self) -> list[Direction]:
        channels: list[Direction] = []
        if self._outbound_active:
            channels.append(Direction.OUTBOUND)
        if self._inbound_active:
            channels.append(Direction.INBOUND)
        return channels

    @property
    def loading_models(self) -> tuple[str, ...]:
        """Local economy backends currently downloading/loading, if any."""
        engine = self._engine
        names = getattr(engine, "loading_models", ()) if engine is not None else ()
        return tuple(str(name) for name in names)

    def has_volc_credentials(self) -> bool:
        key, token, auth = resolve_volc_credentials(
            self._config.volc_api_key,
            self._config.volc_access_token,
        )
        if not key:
            return False
        if auth == "legacy":
            return bool(token)
        return True

    def wants_volc(self) -> bool:
        """True when current mode is volc and credentials are present."""
        if self.mode != "volc":
            return False
        return self.has_volc_credentials()

    def can_start(self) -> tuple[bool, str]:
        """Whether a channel may be started under the current engine mode."""
        mode = self.mode
        if mode == "economy":
            from src.engines.pipeline.engine import (
                economy_asr_requires_dashscope,
                economy_stack_label,
                resolve_dashscope_api_key,
            )

            # Local / Live Captions / sherpa / whisper do not need DashScope.
            if not economy_asr_requires_dashscope(self._config):
                return True, economy_stack_label(self._config)
            if not resolve_dashscope_api_key(self._config):
                return (
                    False,
                    "请先填写 DashScope API Key（经济模式）或设置环境变量 DASHSCOPE_API_KEY，"
                    "或切换为「本地 ASR / Live Captions」。",
                )
            return True, economy_stack_label(self._config)
        if mode == "volc":
            if not self.has_volc_credentials():
                return False, "请先填写火山 API Key（当前为火山同传模式）。"
            return True, ""
        return False, f"未知翻译模式: {mode}"

    def set_translation_mode(self, mode: EngineMode) -> None:
        """Switch engine mode; stops active channels and closes the old engine."""
        current = getattr(self._config, "translation_mode", "volc") or "volc"
        if mode == current and self._engine is not None and self._engine.engine_id == mode:
            return

        if self._outbound_active or self._inbound_active:
            self.stop()

        self.close_engine()
        self._config = self._config.model_copy(update={"translation_mode": mode})

    def close_engine(self) -> None:
        if self._engine is not None:
            with contextlib.suppress(Exception):
                self._engine.close()
            self._engine = None

    def _engine_callbacks(self) -> EngineCallbacks:
        return EngineCallbacks(
            on_source_text=self._on_engine_source,
            on_translated_text=self._on_engine_translated,
            on_audio=self._on_engine_audio,
            on_error=self._on_engine_error,
            on_status=lambda msg: self.log_message.emit(msg),
            on_usage=lambda src, payload: self.usage_reported.emit(src, payload),
            on_engine_status=lambda d, eng, st: self.engine_status_changed.emit(d.value, eng, st),
            should_defer_rotate=self._should_defer_rotate,
            on_channel_lost=self._on_engine_channel_lost,
        )

    def _should_defer_rotate(self, direction: Direction) -> bool:
        gate = self._vad_outbound if direction == Direction.OUTBOUND else self._vad_inbound
        return bool(gate is not None and gate.is_open)

    def _engine_signature(self) -> tuple[tuple[str, str], ...]:
        """Settings baked into the economy engine's backends at construction."""
        data = self._config.model_dump(mode="json")
        keys = sorted(k for k in data if k.startswith("economy_") or k == "device_preference")
        return tuple((k, repr(data[k])) for k in keys)

    def _ensure_engine(self) -> TranslationEngine:
        mode: EngineMode = getattr(self._config, "translation_mode", "volc") or "volc"
        signature = self._engine_signature()
        if self._engine is not None and self._engine.engine_id == mode:
            stale = (
                mode == "economy"
                and bool(self._engine_built_with)
                and signature != self._engine_built_with
            )
            if not stale or self._engine.active_directions:
                return self._engine
        self.close_engine()
        self._engine = create_engine(mode, self._config, self._engine_callbacks())
        self._engine_built_with = signature
        return self._engine

    def start(
        self,
        outbound: bool = True,
        inbound: bool = True,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        loopback_device: int | str | None = None,
        play_outbound_voice: bool = False,
        play_inbound_voice: bool = False,
        force_local: bool = False,
    ) -> None:
        del force_local
        self._input_device = input_device
        self._output_device = output_device
        self._loopback_device = loopback_device
        self._play_outbound_voice = play_outbound_voice
        self._play_inbound_voice = play_inbound_voice

        if outbound and not self._outbound_active:
            self.start_channel(Direction.OUTBOUND, play_voice=play_outbound_voice)
        if inbound and not self._inbound_active:
            self.start_channel(Direction.INBOUND, play_voice=play_inbound_voice)

    def start_channel(
        self,
        direction: Direction,
        *,
        play_voice: bool | None = None,
        force_local: bool = False,
    ) -> None:
        del force_local
        if direction == Direction.OUTBOUND and self._outbound_active:
            return
        if direction == Direction.INBOUND and self._inbound_active:
            return

        opening_session = not self._outbound_active and not self._inbound_active

        if play_voice is not None:
            if direction == Direction.OUTBOUND:
                self._play_outbound_voice = play_voice
            else:
                self._play_inbound_voice = play_voice

        if direction == Direction.OUTBOUND:
            self._directions.setdefault(
                Direction.OUTBOUND,
                DirectionState(
                    direction=Direction.OUTBOUND,
                    source_lang=LanguageCode(self._config.source_language),
                    target_lang=LanguageCode(self._config.target_language),
                ),
            )
        else:
            self._directions.setdefault(
                Direction.INBOUND,
                DirectionState(
                    direction=Direction.INBOUND,
                    source_lang=LanguageCode(self._config.target_language),
                    target_lang=LanguageCode(self._config.source_language),
                ),
            )

        ok, reason = self.can_start()
        if not ok:
            raise EngineLoadError(reason)

        mode = self.mode
        label = "麦克风" if direction == Direction.OUTBOUND else "游戏字幕"
        engine_label = "火山同传" if mode == "volc" else "经济模式"
        self.log_message.emit(f"正在连接{engine_label}（{label}）…")

        engine = self._ensure_engine()
        voice = (
            self._play_outbound_voice
            if direction == Direction.OUTBOUND
            else self._play_inbound_voice
        )
        capture: AudioCapture | None = None
        player_created = False
        ducker_created = False
        previous_loopback = self._loopback_device
        try:
            if not engine.start_direction(direction, play_voice=voice):
                if mode == "volc":
                    hint = "请检查 API Key / 网络。"
                else:
                    from src.engines.pipeline.engine import economy_asr_requires_dashscope

                    if economy_asr_requires_dashscope(self._config):
                        hint = "请检查 DashScope Key / 是否已安装 dashscope。"
                    else:
                        hint = "请检查本地 ASR / Live Captions 是否可用（见日志）。"
                raise EngineLoadError(f"{engine_label}连接失败（{label}）。{hint}")

            if direction == Direction.OUTBOUND:
                self._validate_feedback_routes(Direction.OUTBOUND)
                self._vad_outbound_filters = bool(getattr(self._config, "vad_enabled", True))
                self._vad_outbound = self._make_vad(
                    "mic",
                    force=bool(self._play_outbound_voice),
                )
                capture = AudioCapture(
                    Direction.OUTBOUND,
                    self._input_device,
                    sample_rate=16000,
                    channels=1,
                )
                self._capture_outbound = capture
                capture.on_pcm = lambda pcm: self._engine_direct_pcm(Direction.OUTBOUND, pcm)
                capture.on_error = lambda exc: self._on_capture_error(Direction.OUTBOUND, exc)
            else:
                loopback = self._resolve_loopback_device()
                self._loopback_device = loopback
                self._validate_feedback_routes(Direction.INBOUND)
                self._vad_inbound = self._make_vad("game")
                capture = AudioCapture(
                    Direction.INBOUND,
                    loopback,
                    sample_rate=16000,
                    channels=1,
                )
                self._capture_inbound = capture
                capture.on_pcm = lambda pcm: self._engine_direct_pcm(Direction.INBOUND, pcm)
                capture.on_error = lambda exc: self._on_capture_error(Direction.INBOUND, exc)

            capture.start()
            active_capture = (
                self._capture_outbound if direction == Direction.OUTBOUND else self._capture_inbound
            )
            if active_capture is not capture or not capture.is_running():
                raise EngineLoadError(f"{label}音频采集在启动期间意外停止。")

            want_player = self._play_outbound_voice or self._play_inbound_voice
            if want_player and self._player is None:
                self._player = AudioPlayer(self._output_device)
                player_created = True
                # 真实播放时长反馈：用每段"wall clock"延长麦克风静音窗口
                # 解决 TTS 多段累积下静音窗口不足的问题
                self._player.on_segment_finished = self._on_tts_segment_finished
                self._player.start()
            self._refresh_outbound_echo_route()

            if voice:
                ducker_created = self._ducker is None
                self._ensure_ducker()
                self._refresh_ducker_config()

            next_session: TranslationSession | None = None
            if opening_session:
                next_session = TranslationSession(
                    outbound_enabled=direction == Direction.OUTBOUND,
                    inbound_enabled=direction == Direction.INBOUND,
                    source_language=LanguageCode(self._config.source_language),
                    target_language=LanguageCode(self._config.target_language),
                )
                with contextlib.suppress(ValueError):
                    next_session.transition(SessionStatus.STARTING)
                with contextlib.suppress(ValueError):
                    next_session.transition(SessionStatus.RUNNING)
                path = self._subtitle_logger.begin_session()
                self.log_message.emit(f"翻译留档：{path}")
        except Exception:
            self._rollback_channel_start(
                direction,
                engine,
                capture=capture,
                player_created=player_created,
                ducker_created=ducker_created,
                previous_loopback=previous_loopback,
            )
            raise

        if direction == Direction.OUTBOUND:
            self._outbound_active = True
        else:
            self._inbound_active = True
            self._inbound_loopback = self._loopback_device
            self.log_message.emit(f"游戏声音捕获设备：{self._loopback_device!r}")

        self.log_message.emit(f"{engine_label}已就绪（{label}）")
        if mode == "economy" and reason:
            self.log_message.emit(reason)

        was_running = self._running
        self._running = True
        if not was_running:
            assert next_session is not None
            self._session = next_session
            self.status_changed.emit("running")

        logger.info(f"Channel started: {direction.value} mode={mode}")
        self.log_message.emit(f"{label}通道已启动 · {mode}")
        if direction == Direction.OUTBOUND and self._vad_outbound is not None:
            sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
            preset = getattr(self._config, "quality_preset", "balanced") or "balanced"
            backend = self._vad_outbound.backend
            vad_mode = "过滤+抢话" if self._vad_outbound_filters else "抢话检测"
            self.log_message.emit(
                f"麦克风 VAD 已启用（{backend}/{sens} · 档位 {preset} · {vad_mode}）"
            )
        elif direction == Direction.INBOUND and self._vad_inbound is not None:
            sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
            preset = getattr(self._config, "quality_preset", "balanced") or "balanced"
            backend = self._vad_inbound.backend
            self.log_message.emit(f"游戏字幕 VAD 已启用（{backend}/{sens} · 档位 {preset}）")
        elif direction == Direction.INBOUND:
            self.log_message.emit("游戏字幕 VAD 已关闭（视频/游戏人声更完整）")

    def _rollback_channel_start(
        self,
        direction: Direction,
        engine: TranslationEngine,
        *,
        capture: AudioCapture | None,
        player_created: bool,
        ducker_created: bool,
        previous_loopback: int | str | None,
    ) -> None:
        """Release only resources allocated by a failed channel start."""
        if capture is not None:
            with contextlib.suppress(Exception):
                capture.stop()

        if direction == Direction.OUTBOUND:
            if self._capture_outbound is capture:
                self._capture_outbound = None
            self._vad_outbound = None
            self._vad_outbound_filters = False
            self._outbound_active = False
            self._play_outbound_voice = False
            self._refresh_echo_canceller()
        else:
            if self._capture_inbound is capture:
                self._capture_inbound = None
            self._vad_inbound = None
            self._inbound_active = False
            self._play_inbound_voice = False
            self._loopback_device = previous_loopback
            self._inbound_feedback_route = False
            self._refresh_echo_canceller()

        if player_created and self._player is not None:
            self._drop_echo_canceller()
            with contextlib.suppress(Exception):
                self._player.stop()
            self._player = None
            self._outbound_echo_free = False

        if ducker_created and self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.close()
            self._ducker = None

        with contextlib.suppress(Exception):
            engine.stop_direction(direction)

    @property
    def subtitle_log_path(self) -> Path:
        return self._subtitle_logger.get_recent_path()

    def log_typed_translation(self, original: str, translated: str) -> None:
        self._subtitle_logger.log_typed(original, translated)

    def _resolve_loopback_device(self) -> int | str:
        """Never fall back to the default mic for game capture — resolve a real loopback."""
        try:
            from src.audio.device_guard import resolve_capture_backend
            from src.audio.stream import list_audio_devices

            devices = list_audio_devices()
            inputs = [
                {"name": d.get("name", ""), "index": d.get("id")} for d in devices.get("input", [])
            ]
            backend = getattr(self._config, "capture_backend", "auto") or "auto"
            picked = resolve_capture_backend(
                str(backend),
                configured=self._loopback_device,
                devices=inputs,
            )
            if picked is not None:
                if self._loopback_device in (None, ""):
                    self.log_message.emit("未指定游戏声音来源，已自动选择系统 Loopback")
                if isinstance(picked, (int, str)):
                    return picked
        except Exception as exc:
            logger.warning(f"Auto-select loopback failed: {exc}")
        raise EngineLoadError(
            "未找到可用的系统声音捕获（Loopback / monitor）。\n"
            "请打开「高级」，在「游戏声音」里手动选择正在播放视频的扬声器/耳机 Loopback。"
        )

    def _device_name(self, device: int | str | None, *, kind: str) -> str:
        if device in (None, ""):
            return ""
        try:
            if kind == "output":
                for d in AudioPlayer.list_devices():
                    did = d.get("index", d.get("id"))
                    if str(did) == str(device):
                        return str(d.get("name", ""))
            else:
                from src.audio.stream import list_audio_devices

                for d in list_audio_devices().get("input", []):
                    did = d.get("index", d.get("id"))
                    if str(did) == str(device):
                        return str(d.get("name", ""))
                for d in AudioCapture.list_input_devices():
                    did = d.get("index", d.get("id"))
                    if str(did) == str(device):
                        return str(d.get("name", ""))
        except Exception as exc:
            logger.debug(f"device name lookup failed ({kind}): {exc}")
        return str(device)

    def _validate_feedback_routes(self, direction: Direction) -> None:
        """Detect routes where app playback is captured by the game channel.

        Such routes get a loopback echo canceller on the game channel (see
        ``_refresh_echo_canceller``), so a single headset still works.
        """
        inbound_active = self._inbound_active or direction == Direction.INBOUND
        voice_playback = self._play_outbound_voice or self._play_inbound_voice
        self._inbound_route_checked = False
        self._inbound_feedback_route = False
        if not inbound_active or not voice_playback:
            self._inbound_route_checked = True
            return

        loopback = (
            self._inbound_loopback
            if self._inbound_active and direction == Direction.OUTBOUND
            else self._loopback_device
        )
        try:
            from src.audio.device_guard import (
                is_process_exclude_capture,
                is_system_loopback_capture,
                shares_physical_output_path,
            )

            output_name = self._device_name(self._output_device, kind="output")
            loopback_name = self._device_name(loopback, kind="input")
            shared = shares_physical_output_path(
                output_name=output_name,
                output_device=self._output_device,
                loopback_name=loopback_name,
                loopback_device=loopback,
            ) or (
                self._output_device is None
                and is_system_loopback_capture(loopback_name, loopback)
                and not is_process_exclude_capture(loopback_name, loopback)
            )
            if shared:
                self._inbound_feedback_route = True
                self.log_message.emit(
                    "语音输出会被游戏字幕捕获（同一设备的 Loopback/监听），"
                    "已启用回采消除：译文不会再被当成游戏声音识别。"
                )
        except Exception as exc:
            logger.warning(f"回灌检查未能完成，播报期间将暂停游戏声道: {exc}")
            return
        self._inbound_route_checked = True

    def _make_vad(self, profile: str, *, force: bool = False) -> SpeechGate | None:
        if profile == "game":
            if not force and not bool(getattr(self._config, "vad_game_enabled", True)):
                return None
        elif not force and not bool(getattr(self._config, "vad_enabled", True)):
            return None
        sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
        open_ms = int(getattr(self._config, "vad_open_ms", 80) or 80)
        hangover_ms = int(getattr(self._config, "vad_hangover_ms", 600) or 600)
        backend = getattr(self._config, "vad_backend", "auto") or "auto"
        preroll_ms = int(getattr(self._config, "vad_preroll_ms", 300) or 300)
        # 游戏字幕场景强制 RMS VAD: 游戏声音是混合音频 (BGM/音效/对话),
        # Silero VAD 只检测人声, 对 BGM 和音效 prob=0 会导致全部音频被拦截.
        if profile == "game":
            backend = "rms"
        return SpeechGate(
            profile=profile,
            sensitivity=str(sens),
            sample_rate=16000,
            open_ms=open_ms,
            hangover_ms=hangover_ms,
            backend=str(backend),
            preroll_ms=preroll_ms,
        )

    def _ensure_ducker(self) -> None:
        if self._ducker is not None:
            return
        mode: DuckerMode = self._config.original_audio
        gain = float(self._config.duck_gain)
        self._ducker = SessionDucker(mode=mode, duck_gain=gain)

    def _refresh_ducker_config(self) -> None:
        if self._ducker is None:
            return
        mode: DuckerMode = self._config.original_audio
        gain = float(self._config.duck_gain)
        self._ducker.configure(mode=mode, duck_gain=gain)

    def stop_channel(self, direction: Direction) -> None:
        if direction == Direction.OUTBOUND and not self._outbound_active:
            return
        if direction == Direction.INBOUND and not self._inbound_active:
            return

        if self._engine is not None:
            with contextlib.suppress(Exception):
                self._engine.stop_direction(direction)

        if direction == Direction.OUTBOUND:
            if self._capture_outbound is not None:
                with contextlib.suppress(Exception):
                    self._capture_outbound.stop()
                self._capture_outbound = None
            self._refresh_echo_canceller()
            self._vad_outbound = None
            self._vad_outbound_filters = False
            self._barge_in_started_at[Direction.OUTBOUND] = 0.0
            self._outbound_active = False
            self._play_outbound_voice = False
        else:
            if self._capture_inbound is not None:
                with contextlib.suppress(Exception):
                    self._capture_inbound.stop()
                self._capture_inbound = None
            self._vad_inbound = None
            self._inbound_active = False
            self._inbound_loopback = None
            self._inbound_route_checked = False
            self._inbound_feedback_route = False
            self._refresh_echo_canceller()
            self._play_inbound_voice = False

        dp = self._directions.get(direction)
        if dp is not None:
            dp.last_text = ""
            dp.last_source = ""
            dp.last_translated = ""

        other_wants = (self._outbound_active and self._play_outbound_voice) or (
            self._inbound_active and self._play_inbound_voice
        )
        if self._player is not None and not other_wants:
            self._drop_echo_canceller()
            with contextlib.suppress(Exception):
                self._player.stop()
            self._player = None
            self._outbound_echo_free = False

        label = "麦克风" if direction == Direction.OUTBOUND else "游戏字幕"
        self.log_message.emit(f"{label}通道已停止")
        logger.info(f"Channel stopped: {direction.value}")

        if not self._outbound_active and not self._inbound_active:
            self._running = False
            if self._ducker is not None:
                with contextlib.suppress(Exception):
                    self._ducker.close()
                self._ducker = None
            with contextlib.suppress(ValueError):
                self._session.transition(SessionStatus.STOPPED)
            self.status_changed.emit("stopped")
        else:
            self.status_changed.emit("running")

    def stop(self) -> None:
        if self._outbound_active:
            self.stop_channel(Direction.OUTBOUND)
        if self._inbound_active:
            self.stop_channel(Direction.INBOUND)
        self._running = False
        self.close_engine()
        if self._capture_outbound is not None:
            with contextlib.suppress(Exception):
                self._capture_outbound.stop()
            self._capture_outbound = None
        if self._capture_inbound is not None:
            with contextlib.suppress(Exception):
                self._capture_inbound.stop()
            self._capture_inbound = None
        if self._player is not None:
            self._drop_echo_canceller()
            with contextlib.suppress(Exception):
                self._player.stop()
            self._player = None
            self._outbound_echo_free = False
        self._outbound_active = False
        self._inbound_active = False
        with contextlib.suppress(ValueError):
            self._session.transition(SessionStatus.STOPPED)
        self.status_changed.emit("stopped")
        logger.info("Translation session stopped")

    def _on_capture_error(self, direction: Direction, exc: Exception) -> None:
        """Device read loop died (unplugged/reset): ask the UI to stop the channel cleanly.

        Runs on the capture thread, so the full teardown (engine, player, ducker,
        session status) is left to ``stop_channel`` on the owner's side.
        """
        label = "麦克风" if direction == Direction.OUTBOUND else "游戏声音"
        message = f"{label}音频设备已断开，通道已停止：{exc}"
        logger.warning(message)
        self.engine_status_changed.emit(direction.value, self.mode, "error")
        self.channel_lost.emit(direction.value, message)

    def _on_engine_channel_lost(self, direction: Direction, message: str) -> None:
        """Engine gave up on a direction; same clean UI-side stop as a lost device."""
        logger.warning(message)
        self.engine_status_changed.emit(direction.value, self.mode, "error")
        self.channel_lost.emit(direction.value, message)

    def _on_engine_source(self, direction: Direction, text: str, is_final: bool) -> None:
        dp = self._directions.get(direction)
        if dp is None:
            return
        cleaned = (text or "").strip()
        # Empty final clears sticky partial (e.g. MT failed after source partial).
        if not cleaned:
            if is_final:
                dp.last_source = ""
                dp.last_translated = ""
                dp.last_text = ""
                dp.utterance_started_at = 0.0
            return
        if dp.utterance_started_at <= 0:
            dp.utterance_started_at = time.monotonic()
        dp.last_source = cleaned
        # Economy may send source final per sentence; pass is_final through.
        self._emit_engine_subtitle(direction, is_final=bool(is_final))

    def _on_engine_translated(self, direction: Direction, text: str, is_final: bool) -> None:
        dp = self._directions.get(direction)
        if dp is None:
            return
        cleaned = (text or "").strip()
        # Empty final clears sticky partial after MT failure.
        if not cleaned:
            if is_final:
                dp.last_source = ""
                dp.last_translated = ""
                dp.last_text = ""
            return
        # 规范化：去标点/空格，用于回灌识别
        normalized = "".join(ch for ch in cleaned if ch.isalnum())

        # --- 文本去重：8 秒内连续相同译文只播放一次 ---
        if is_final:
            now = time.time()
            if (
                normalized
                and normalized == self._last_played_text[direction]
                and now - self._last_played_at[direction] < self._text_dedup_window_sec
            ):
                # Skip audio but still refresh subtitle overlay.
                self._skip_next_audio_for.add(direction)
                dp.last_translated = cleaned
                self._emit_engine_subtitle(direction, is_final=is_final)
                return
            if normalized:
                self._last_played_text[direction] = normalized
                self._last_played_at[direction] = now
            # 真正的"新句"：解除句级丢弃标记
            self._skip_next_audio_for.discard(direction)

        dp.last_translated = cleaned
        self._emit_engine_subtitle(direction, is_final=is_final)

    def _emit_engine_subtitle(self, direction: Direction, *, is_final: bool = True) -> None:
        dp = self._directions.get(direction)
        if dp is None:
            return
        original = (dp.last_source or "").strip()
        translated = (dp.last_translated or "").strip()
        if not original and not translated:
            return
        pair = f"{original}||{translated}||{int(is_final)}"
        if pair == dp.last_text:
            return
        dp.last_text = pair
        try:
            entry = SubtitleEntry(
                direction=direction,
                original_text=original or "…",
                translated_text=(
                    translated or ("正在翻译…" if self.mode == "economy" and original else "…")
                ),
                is_final=is_final,
            )
        except ValueError:
            return
        self.subtitle_ready.emit(entry)
        if is_final:
            self._subtitle_logger.log(entry)
            if dp.utterance_started_at > 0:
                latency_ms = int((time.monotonic() - dp.utterance_started_at) * 1000)
                self.latency_reported.emit(max(0, min(latency_ms, 9999)))
            dp.last_source = ""
            dp.last_translated = ""
            dp.utterance_started_at = 0.0

    def _on_engine_audio(self, direction: Direction, data: bytes) -> None:
        if not data:
            return
        if direction == Direction.OUTBOUND and not self._play_outbound_voice:
            return
        if direction == Direction.INBOUND and not self._play_inbound_voice:
            return

        # 句级去重：当前正在丢弃该方向的所有音频段
        if direction in self._skip_next_audio_for:
            return  # 持续丢弃直到句边界取消

        # 用播放端"真实时长"做静音窗口（数据字节数 + 0.5s 余量，至少 2s）
        # PCM16 单声道 16kHz → 每样本 2 字节
        audio_len_sec = len(data) / 2.0 / 16000.0
        candidate_end = time.time() + max(
            self._tts_min_silence_sec,
            audio_len_sec + self._tts_silence_margin_sec,
        )
        if candidate_end > self._tts_playing_until:
            self._tts_playing_until = candidate_end

        if self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.pulse()
        if self._player is not None:
            self._player.play(data)
        else:
            dir_label = "麦克风" if direction == Direction.OUTBOUND else "游戏"
            logger.warning(
                f"{dir_label}通道语音输出被丢弃：AudioPlayer 未创建 "
                f"(output_device={self._output_device!r})"
            )

    def _on_tts_segment_finished(self, played_sec: float) -> None:
        """AudioPlayer 一段播完后调用。用 wall clock 精确延长静音窗口，
        避免 TTS 多段累积下窗口不足导致回灌。"""
        if played_sec <= 0:
            return
        # 段播完时, 若队列仍有待播段, 继续保持静音窗口
        queue_remaining = self._player_queue_remaining()
        # ``played_sec`` has already elapsed when this callback runs. Adding it
        # again would mute capture for another full segment duration.
        candidate = time.time() + queue_remaining + self._tts_silence_margin_sec
        if candidate > self._tts_playing_until:
            self._tts_playing_until = candidate
        if queue_remaining <= 0 and self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.release()

    def _player_queue_remaining(self) -> float:
        """估算 AudioPlayer 队列中剩余音频时长 (秒)."""
        if self._player is None:
            return 0.0
        queued = getattr(self._player, "queued_seconds", None)
        if isinstance(queued, int | float):
            return float(queued)
        try:
            qsize = self._player.queue_size
        except Exception:
            qsize = 0
        # 无时长信息时按每段 ~1.5 秒估算
        return qsize * 1.5

    def _refresh_outbound_echo_route(self) -> None:
        """Mic cannot hear playback that only goes into a virtual cable."""
        from src.core.audio_player import is_virtual_device_name

        player = self._player
        echo_free = False
        if player is not None and bool(getattr(player, "is_virtual_output", False)):
            mic_name = self._device_name(self._input_device, kind="input")
            echo_free = not is_virtual_device_name(mic_name)
        if echo_free and not self._outbound_echo_free:
            logger.info("译文输出为虚拟线且麦克风为物理设备：麦克风通道不闭麦")
        self._outbound_echo_free = echo_free
        self._refresh_echo_canceller()

    def _refresh_echo_canceller(self) -> None:
        """Cancel our TTS where it leaks back: mic (speakers) and game loopback.

        Both cancellers read one ``PlaybackReference`` fed by the player.
        """
        player = self._player
        mic = (
            player is not None
            and self._capture_outbound is not None
            and not self._outbound_echo_free
            and bool(getattr(self._config, "aec_enabled", True))
        )
        loop = player is not None and self._inbound_feedback_route
        if not mic:
            self._aec = None
        if not loop:
            self._aec_inbound = None
        if player is None or not (mic or loop):
            self._drop_echo_canceller()
            return
        if self._reference is None:
            self._reference = PlaybackReference(origin=time.monotonic())
            player.on_reference = self._reference.push
        if mic and self._aec is None:
            self._aec = EchoCanceller(self._reference)
            logger.info("麦克风通道已启用软件回声消除")
        if loop and self._aec_inbound is None:
            self._aec_inbound = LoopbackEchoCanceller(self._reference)
            logger.info("游戏字幕通道已启用回采消除（本应用语音输出）")

    def _drop_echo_canceller(self) -> None:
        player = self._player
        if player is not None:
            player.on_reference = None
        self._aec = None
        self._aec_inbound = None
        self._reference = None

    def _cancel_pending_tts(self) -> None:
        """Drop translations queued for synthesis so they do not play after an interrupt."""
        cancel = getattr(self._engine, "cancel_pending_audio", None)
        if not callable(cancel):
            return
        for direction in (Direction.OUTBOUND, Direction.INBOUND):
            with contextlib.suppress(Exception):
                cancel(direction)

    def _on_engine_error(self, message: str) -> None:
        self.log_message.emit(message)
        self.error_occurred.emit(message)

    def process_tick(self) -> None:
        """Engines use direct PCM; tick kept as a lightweight no-op hook."""
        return

    def _engine_direct_pcm(self, direction: Direction, pcm: bytes) -> None:
        if not pcm or self._engine is None:
            return
        if direction not in self._engine.active_directions:
            return
        # 两个方向都走回灌抑制：TTS 播放期间音频入缓存，
        # 窗口解除时回放给翻译引擎。
        self._handle_pcm_with_feedback_suppression(direction, pcm)

    def _handle_pcm_with_feedback_suppression(self, direction: Direction, pcm: bytes) -> None:
        """统一回灌抑制：TTS 播放期间入 buffer；窗口解除时回放。

        同时适用于 outbound (麦克风) 和 inbound (游戏字幕/loopback) 两个方向。
        关键点：buffer 写入必须在录音线程**快**（O(1)），
        否则会卡住采集。实际回放放在主流程外。

        回灌抑制窗口由两部分共同决定:
        1. ``_tts_playing_until`` — 基于音频时长 + 段完成回调的估算
        2. ``AudioPlayer.is_playing`` — 真实播放状态 (队列有数据或流活跃)
        任一为 True 都保持静音窗口, 避免估算不准导致回灌.
        """
        now = time.time()
        aec_result: AecResult | None = None
        aec: EchoCanceller | LoopbackEchoCanceller | None = (
            self._aec if direction == Direction.OUTBOUND else self._aec_inbound
        )
        if aec is not None:
            try:
                aec_result = aec.process(pcm, time.monotonic())
            except Exception as exc:
                logger.warning(f"回声消除异常，已停用：{exc}")
                if direction == Direction.OUTBOUND:
                    self._aec = None
                else:
                    # Without cancellation the game channel would recapture our TTS.
                    self._aec_inbound = None
                    self._inbound_route_checked = False
            else:
                if not aec_result.pcm:
                    return
                pcm = aec_result.pcm
        gate = self._vad_outbound if direction == Direction.OUTBOUND else self._vad_inbound
        result = gate.process(pcm) if gate is not None else None

        # inbound 方向的 VAD 诊断日志 (原 _engine_direct_pcm inbound 分支)
        if direction == Direction.INBOUND and result is not None:
            if result.passed:
                self._vad_pass_chunks += 1
            else:
                self._vad_drop_chunks += 1
            if now - self._vad_diag_at >= 5.0:
                self._vad_diag_at = now
                self._log_inbound_vad_diag(gate)

        # 双重判定: 时间窗口 OR 真实播放状态
        player_busy = self._player is not None and self._player.is_playing
        in_silence = (now < self._tts_playing_until) or player_busy
        if direction == Direction.OUTBOUND and self._outbound_echo_free:
            in_silence = False
        # Routes where playback reaches the game capture are refused at start
        # (_validate_feedback_routes), and the game/video voice does not pause for
        # our TTS: muting or "barge-in" there would only cut every translation off.
        if direction == Direction.INBOUND and self._inbound_route_checked:
            in_silence = False
        if not in_silence:
            self._barge_in_started_at[direction] = 0.0
            # 窗口已解除：先把 buffer 残留（来自上次静音期）回放，再发当前帧
            self._flush_feedback_buffer(direction)
            self._send_pcm_with_preroll(direction, pcm, gate_result=result)
            return

        # TTS 正在播放：缓存当前帧
        # Economy utterance segmentation must see capture activity even though
        # the PCM itself is held back, otherwise every TTS segment creates a
        # false 300 ms end-of-speech gap and cuts words at arbitrary positions.
        notify_pending = getattr(self._engine, "notify_pcm_pending", None)
        if callable(notify_pending):
            with contextlib.suppress(Exception):
                notify_pending(direction)

        overflow = False
        if result is not None and result.opened_now and result.preroll:
            overflow = self._append_feedback_buffer(direction, result.preroll)
        # Same filtering as the live path: replaying VAD-rejected noise after TTS
        # would hand the engine audio it would never have received otherwise.
        filters = direction == Direction.INBOUND or self._vad_outbound_filters
        if result is None or result.passed or not filters:
            overflow = self._append_feedback_buffer(direction, pcm) or overflow
        if overflow:
            self._end_tts_mute_for_buffer_limit(direction)
            return

        if result is None or not result.passed:
            self._barge_in_started_at[direction] = 0.0
            return
        if (
            aec_result is not None
            and aec_result.far_active
            and aec_result.converged
            and not aec_result.near_end_dominant
        ):
            # VAD fired on residual echo of our own TTS, not on the user.
            self._barge_in_started_at[direction] = 0.0
            return

        if self._barge_in_started_at[direction] <= 0:
            self._barge_in_started_at[direction] = now
            return

        barge_ms = int(getattr(self._config, "vad_barge_in_ms", 200) or 200)
        if (now - self._barge_in_started_at[direction]) * 1000 < barge_ms:
            return

        self._end_tts_mute_for_barge_in(direction)

    def _log_inbound_vad_diag(self, gate: SpeechGate | None) -> None:
        """每 5 秒输出一次游戏字幕 VAD 诊断信息。"""
        if gate is None:
            return
        st = gate.stats()
        total = self._vad_pass_chunks + self._vad_drop_chunks
        if not total:
            return
        msg = (
            f"游戏捕获电平 {st.backend} RMS={st.rms:.4f} "
            f"VAD={'开' if st.open else '关'} "
            f"近5秒送出 {self._vad_pass_chunks}/{total}"
        )
        if st.backend == "silero":
            msg += f" prob={st.speech_prob:.2f}"
        if isinstance(self._aec_inbound, LoopbackEchoCanceller):
            muted = self._aec_inbound.take_realign_muted_seconds()
            if muted >= 0.05:
                msg += f" · 回声消除对齐中静音 {muted:.1f}s"
                logger.info(f"[AEC] 近5秒对齐中静音 {muted:.1f}s")
        if st.rms < 0.002:
            msg += " · 几乎无声，请确认「游戏声音」选的是正在播放视频的扬声器 Loopback"
        elif self._vad_pass_chunks == 0:
            msg += " · 有声但被 VAD 拦住，可在设置把灵敏度改「宽松」或暂时关闭 VAD"
        self.log_message.emit(msg)
        self._vad_pass_chunks = 0
        self._vad_drop_chunks = 0

    def _append_feedback_buffer(self, direction: Direction, pcm: bytes) -> bool:
        if not pcm:
            return False
        with self._feedback_buffer_lock:
            buf = self._feedback_buffers[direction]
            buf.append(pcm)
            self._feedback_buffer_bytes[direction] += len(pcm)
            return self._feedback_buffer_bytes[direction] > self._feedback_buffer_max_bytes

    def _end_tts_mute_for_buffer_limit(self, direction: Direction) -> None:
        """Prefer complete input over TTS once the feedback delay reaches its cap."""
        self._barge_in_started_at[direction] = 0.0
        self._tts_playing_until = time.time()
        if self._player is not None:
            with contextlib.suppress(Exception):
                self._player.clear_queue()
        self._cancel_pending_tts()
        if self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.release()
        self.log_message.emit("输入语音持续，已停止译文播放以保证字幕完整")
        logger.info(f"Feedback buffer limit reached ({direction.value}); preserving input")
        self._flush_feedback_buffer(direction)

    def _end_tts_mute_for_barge_in(self, direction: Direction) -> None:
        self._barge_in_started_at[direction] = 0.0
        self._tts_playing_until = time.time()
        if self._player is not None:
            with contextlib.suppress(Exception):
                self._player.clear_queue()
        self._cancel_pending_tts()
        if self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.release()
        label = "麦克风" if direction == Direction.OUTBOUND else "游戏字幕"
        self.log_message.emit(f"检测到{label}抢话，提前结束 TTS 静音")
        logger.info(f"Barge-in detected ({direction.value}); ending TTS mute early")
        self._flush_feedback_buffer(direction)

    def _flush_feedback_buffer(self, direction: Direction) -> None:
        """把缓存的音频批量送回翻译引擎。"""
        with self._feedback_buffer_lock:
            buf = self._feedback_buffers[direction]
            if not buf:
                return
            chunks = buf
            self._feedback_buffers[direction] = []
            self._feedback_buffer_bytes[direction] = 0
        if self._engine is None or direction not in self._engine.active_directions:
            return
        try:
            self._engine.send_pcm(direction, b"".join(chunks))
        except Exception as exc:
            logger.debug(f"flush feedback buffer ({direction.value}) failed: {exc}")

    def _send_pcm_with_preroll(
        self, direction: Direction, pcm: bytes, *, gate_result: object | None = None
    ) -> None:
        """正常路径：实时发送音频（窗口已解除）。"""
        if self._engine is None or direction not in self._engine.active_directions:
            return
        gate = self._vad_outbound if direction == Direction.OUTBOUND else self._vad_inbound
        # outbound 受 _vad_outbound_filters 开关控制；inbound 始终应用 VAD
        apply_vad = gate is not None and (
            direction == Direction.INBOUND or self._vad_outbound_filters
        )
        if apply_vad:
            assert gate is not None
            result = gate_result if gate_result is not None else gate.process(pcm)
            if not getattr(result, "passed", False):
                return
            preroll = (
                getattr(result, "preroll", b"") if getattr(result, "opened_now", False) else b""
            )
            if preroll:
                pcm = preroll + pcm
        with contextlib.suppress(Exception):
            self._engine.send_pcm(direction, pcm)

    def is_running(self) -> bool:
        return self._running

    @property
    def session(self) -> TranslationSession:
        return self._session
