"""Volc AST-only translation session orchestrator."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from src.core.audio_capture import AudioCapture
from src.core.audio_player import AudioPlayer
from src.core.exceptions import EngineLoadError
from src.core.speech_gate import SpeechGate
from src.core.volc_engine import VolcASTClient, VolcRuntime, resolve_volc_credentials
from src.models.config import AppConfigModel
from src.models.enums import Direction, LanguageCode, SessionStatus
from src.models.session import TranslationSession
from src.models.subtitle import SubtitleEntry
from src.utils.logger import SubtitleLogger, configure_logging, logger


@dataclass
class DirectionState:
    """Per-direction subtitle pairing state."""

    direction: Direction
    source_lang: LanguageCode = LanguageCode.ZH
    target_lang: LanguageCode = LanguageCode.EN
    last_text: str = ""
    last_source: str = ""
    last_translated: str = ""


class TranslationPipeline(QObject):
    """Real-time dual-channel translation via Volc AST 2.0 only."""

    subtitle_ready = pyqtSignal(SubtitleEntry)
    status_changed = pyqtSignal(str)
    engine_status_changed = pyqtSignal(str, str, str)  # direction, engine_type, status
    latency_reported = pyqtSignal(int)
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str)
    usage_reported = pyqtSignal(str, dict)  # source, usage payload


    def __init__(self, config: AppConfigModel, registry: object | None = None) -> None:
        # registry kept optional for call-site compatibility; unused (Volc-only).
        del registry
        super().__init__()
        self._config = config
        self._session = TranslationSession()
        self._capture_outbound: AudioCapture | None = None
        self._capture_inbound: AudioCapture | None = None
        self._player: AudioPlayer | None = None
        self._directions: dict[Direction, DirectionState] = {}
        self._subtitle_logger = SubtitleLogger(Path(config.log_dir))
        self._running = False
        self._outbound_active = False
        self._inbound_active = False
        self._play_outbound_voice = False
        self._play_inbound_voice = False
        self._volc: VolcRuntime | None = None
        self._volc_started_at = 0.0
        self._input_device: int | str | None = None
        self._output_device: int | str | None = None
        self._loopback_device: int | str | None = None
        self._vad_outbound: SpeechGate | None = None
        self._vad_inbound: SpeechGate | None = None
        self._vad_diag_at = 0.0
        self._vad_pass_chunks = 0
        self._vad_drop_chunks = 0
        self._ducker = None

        configure_logging(Path(config.log_dir), config.debug_mode)

    @property
    def mode(self) -> str:
        return "volc"

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

    # Backward-compatible alias used by GUI
    def wants_volc(self) -> bool:
        return self.has_volc_credentials()

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
        del force_local  # local path removed
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

        if not self.has_volc_credentials():
            raise EngineLoadError("请先填写火山 API Key（当前仅支持火山同传）。")

        label = "麦克风" if direction == Direction.OUTBOUND else "游戏字幕"
        self.log_message.emit(f"正在连接火山同传（{label}）…")
        if not self._ensure_volc_channel(direction):
            raise EngineLoadError(f"火山同传连接失败（{label}）。请检查 API Key / 网络。")

        if opening_session:
            path = self._subtitle_logger.begin_session()
            self.log_message.emit(f"翻译留档：{path}")

        self.log_message.emit(f"火山同传已就绪（{label}）")

        if direction == Direction.OUTBOUND:
            self._vad_outbound = self._make_vad("mic")
            self._capture_outbound = AudioCapture(
                Direction.OUTBOUND,
                self._input_device,
                sample_rate=16000,
                channels=1,
            )
            self._capture_outbound.on_pcm = lambda pcm: self._volc_direct_pcm("outbound", pcm)
            self._capture_outbound.start()
            self._outbound_active = True
        else:
            loopback = self._resolve_loopback_device()
            self._loopback_device = loopback
            self._vad_inbound = self._make_vad("game")
            self._capture_inbound = AudioCapture(
                Direction.INBOUND,
                loopback,
                sample_rate=16000,
                channels=1,
            )
            self._capture_inbound.on_pcm = lambda pcm: self._volc_direct_pcm("inbound", pcm)
            self._capture_inbound.start()
            self._inbound_active = True
            self.log_message.emit(f"游戏声音捕获设备：{loopback!r}")

        want_player = self._play_outbound_voice or self._play_inbound_voice
        if want_player and self._player is None:
            self._player = AudioPlayer(self._output_device)
            self._player.start()

        was_running = self._running
        self._running = True
        self._volc_started_at = time.time()
        if not was_running:
            self._session = TranslationSession(
                outbound_enabled=self._outbound_active,
                inbound_enabled=self._inbound_active,
                source_language=LanguageCode(self._config.source_language),
                target_language=LanguageCode(self._config.target_language),
            )
            with contextlib.suppress(ValueError):
                self._session.transition(SessionStatus.STARTING)
            with contextlib.suppress(ValueError):
                self._session.transition(SessionStatus.RUNNING)
            self.status_changed.emit("running")

        logger.info(f"Channel started: {direction.value} mode=volc")
        self.log_message.emit(f"{label}通道已启动 · volc")
        if direction == Direction.OUTBOUND and self._vad_outbound is not None:
            sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
            preset = getattr(self._config, "quality_preset", "balanced") or "balanced"
            self.log_message.emit(f"麦克风 VAD 已启用（{sens} · 档位 {preset}）")
        elif direction == Direction.INBOUND and self._vad_inbound is not None:
            sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
            preset = getattr(self._config, "quality_preset", "balanced") or "balanced"
            self.log_message.emit(f"游戏字幕 VAD 已启用（{sens} · 档位 {preset}）")
        elif direction == Direction.INBOUND:
            self.log_message.emit("游戏字幕 VAD 已关闭（视频/游戏人声更完整）")

        if (
            (direction == Direction.OUTBOUND and self._play_outbound_voice)
            or (direction == Direction.INBOUND and self._play_inbound_voice)
        ):
            self._ensure_ducker()
            self._refresh_ducker_config()

    @property
    def subtitle_log_path(self):
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
                {"name": d.get("name", ""), "index": d.get("id")}
                for d in devices.get("input", [])
            ]
            backend = getattr(self._config, "capture_backend", "auto") or "auto"
            picked = resolve_capture_backend(
                str(backend),
                configured=self._loopback_device,
                devices=inputs,
            )
            if picked is not None:
                if self._loopback_device in (None, ""):
                    self.log_message.emit(
                        "未指定游戏声音来源，已自动选择系统捕获"
                        + ("（免驱动）" if str(picked).startswith("wasapi_proc_exclude:") else "")
                    )
                return picked
        except Exception as exc:
            logger.warning(f"Auto-select loopback failed: {exc}")
        raise EngineLoadError(
            "未找到可用的系统声音捕获（Loopback / monitor）。\n"
            "请打开「高级」，在「游戏声音」里手动选择正在播放视频的扬声器/耳机 Loopback。"
        )

    def _make_vad(self, profile: str) -> SpeechGate | None:
        if profile == "game":
            if not bool(getattr(self._config, "vad_game_enabled", False)):
                return None
        elif not bool(getattr(self._config, "vad_enabled", True)):
            return None
        sens = getattr(self._config, "vad_sensitivity", "medium") or "medium"
        open_ms = int(getattr(self._config, "vad_open_ms", 80) or 80)
        hangover_ms = int(getattr(self._config, "vad_hangover_ms", 600) or 600)
        return SpeechGate(
            profile=profile,
            sensitivity=str(sens),
            sample_rate=16000,
            open_ms=open_ms,
            hangover_ms=hangover_ms,
        )

    def _ensure_ducker(self) -> None:
        if self._ducker is not None:
            return
        from src.audio.session_ducker import SessionDucker

        mode = getattr(self._config, "original_audio", "duck") or "duck"
        gain = float(getattr(self._config, "duck_gain", 0.2) or 0.2)
        self._ducker = SessionDucker(mode=mode, duck_gain=gain)

    def _refresh_ducker_config(self) -> None:
        if self._ducker is None:
            return
        mode = getattr(self._config, "original_audio", "duck") or "duck"
        gain = float(getattr(self._config, "duck_gain", 0.2) or 0.2)
        self._ducker.configure(mode=mode, duck_gain=gain)

    def stop_channel(self, direction: Direction) -> None:
        if direction == Direction.OUTBOUND and not self._outbound_active:
            return
        if direction == Direction.INBOUND and not self._inbound_active:
            return

        name = "outbound" if direction == Direction.OUTBOUND else "inbound"
        if self._volc is not None:
            with contextlib.suppress(Exception):
                self._volc.disconnect_client(name)
            if not self._volc.clients:
                with contextlib.suppress(Exception):
                    self._volc.stop()
                self._volc = None

        if direction == Direction.OUTBOUND:
            if self._capture_outbound is not None:
                with contextlib.suppress(Exception):
                    self._capture_outbound.stop()
                self._capture_outbound = None
            self._vad_outbound = None
            self._outbound_active = False
            self._play_outbound_voice = False
        else:
            if self._capture_inbound is not None:
                with contextlib.suppress(Exception):
                    self._capture_inbound.stop()
                self._capture_inbound = None
            self._vad_inbound = None
            self._inbound_active = False
            self._play_inbound_voice = False

        dp = self._directions.get(direction)
        if dp is not None:
            dp.last_text = ""
            dp.last_source = ""
            dp.last_translated = ""

        other_wants = (
            (self._outbound_active and self._play_outbound_voice)
            or (self._inbound_active and self._play_inbound_voice)
        )
        if self._player is not None and not other_wants:
            with contextlib.suppress(Exception):
                self._player.stop()
            self._player = None

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

    def _ensure_volc_channel(self, direction: Direction) -> bool:
        api_key, access_token, auth = resolve_volc_credentials(
            self._config.volc_api_key,
            self._config.volc_access_token,
        )
        if not api_key:
            return False

        logger.info(f"Volc auth mode={auth}, key_len={len(api_key)} channel={direction.value}")
        if self._volc is None:
            self._volc = VolcRuntime()

        src = self._config.source_language
        tgt = self._config.target_language
        name = "outbound" if direction == Direction.OUTBOUND else "inbound"
        speaker_id = getattr(self._config, "volc_speaker_id", "") or ""
        speech_rate = int(getattr(self._config, "volc_speech_rate", 0) or 0)
        hotwords = list(getattr(self._config, "hotwords", None) or [])
        glossary = dict(getattr(self._config, "glossary", None) or {})

        if name in self._volc.clients:
            return True

        try:
            rotate_m = int(getattr(self._config, "volc_session_rotate_minutes", 12) or 0)

            def _defer_rotate(gate_name: str = name) -> bool:
                gate = (
                    self._vad_outbound
                    if gate_name == "outbound"
                    else self._vad_inbound
                )
                return bool(gate is not None and gate.is_open)

            common_kw = dict(
                api_key=api_key,
                access_token=access_token,
                speech_rate=speech_rate,
                hotwords=hotwords,
                glossary=glossary,
                on_error=self._on_volc_error,
                on_status=lambda msg: self.log_message.emit(msg),
                on_usage=lambda payload, src=name: self.usage_reported.emit(src, payload),
                session_rotate_minutes=rotate_m,
                should_defer_rotate=_defer_rotate,
            )
            if direction == Direction.OUTBOUND:
                mode = "s2s" if self._play_outbound_voice else "s2t"
                client = VolcASTClient(
                    **common_kw,
                    source_language=src,
                    target_language=tgt,
                    mode=mode,  # type: ignore[arg-type]
                    speaker_id=speaker_id if mode == "s2s" else "",
                    on_source_text=lambda text, final, d=Direction.OUTBOUND: self._on_volc_source(
                        d, text, final
                    ),
                    on_translated_text=lambda text, final, d=Direction.OUTBOUND: self._on_volc_translated(
                        d, text, final
                    ),
                    on_audio=lambda data: self._on_volc_audio(Direction.OUTBOUND, data),
                )
            else:
                mode = "s2s" if self._play_inbound_voice else "s2t"
                client = VolcASTClient(
                    **common_kw,
                    source_language=tgt,
                    target_language=src,
                    mode=mode,  # type: ignore[arg-type]
                    speaker_id=speaker_id if mode == "s2s" else "",
                    on_source_text=lambda text, final, d=Direction.INBOUND: self._on_volc_source(
                        d, text, final
                    ),
                    on_translated_text=lambda text, final, d=Direction.INBOUND: self._on_volc_translated(
                        d, text, final
                    ),
                    on_audio=lambda data: self._on_volc_audio(Direction.INBOUND, data),
                )

            if hotwords or glossary:
                self.log_message.emit(
                    f"热词 {len(hotwords)} 条 / 术语 {len(glossary)} 条已加载"
                )
            if speech_rate:
                self.log_message.emit(f"同传语速: {speech_rate}")

            if mode == "s2s":
                voice_label = speaker_id or "原音色复刻"
                self.log_message.emit(f"火山语音输出音色: {voice_label}")

            if self._volc.connect_client(name, client):
                self.engine_status_changed.emit(direction.value, "volc", "ready")
                return True
            self.engine_status_changed.emit(direction.value, "volc", "error")
            return False
        except Exception as exc:
            logger.error(f"Volc channel start failed ({direction.value}): {exc}")
            self.log_message.emit(f"火山启动异常: {exc}")
            return False

    def stop(self) -> None:
        if self._outbound_active:
            self.stop_channel(Direction.OUTBOUND)
        if self._inbound_active:
            self.stop_channel(Direction.INBOUND)
        self._running = False
        if self._volc is not None:
            with contextlib.suppress(Exception):
                self._volc.stop()
            self._volc = None
        if self._capture_outbound is not None:
            with contextlib.suppress(Exception):
                self._capture_outbound.stop()
            self._capture_outbound = None
        if self._capture_inbound is not None:
            with contextlib.suppress(Exception):
                self._capture_inbound.stop()
            self._capture_inbound = None
        if self._player is not None:
            with contextlib.suppress(Exception):
                self._player.stop()
            self._player = None
        self._outbound_active = False
        self._inbound_active = False
        with contextlib.suppress(ValueError):
            self._session.transition(SessionStatus.STOPPED)
        self.status_changed.emit("stopped")
        logger.info("Translation session stopped")

    def _on_volc_source(self, direction: Direction, text: str, is_final: bool) -> None:
        del is_final
        if not text.strip():
            return
        dp = self._directions.get(direction)
        if dp is None:
            return
        dp.last_source = text.strip()
        self._emit_volc_subtitle(direction, is_final=False)

    def _on_volc_translated(self, direction: Direction, text: str, is_final: bool) -> None:
        if not text.strip():
            return
        dp = self._directions.get(direction)
        if dp is None:
            return
        dp.last_translated = text.strip()
        self._emit_volc_subtitle(direction, is_final=is_final)

    def _emit_volc_subtitle(self, direction: Direction, *, is_final: bool = True) -> None:
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
                translated_text=translated or "…",
                is_final=is_final,
            )
        except ValueError:
            return
        self.subtitle_ready.emit(entry)
        if is_final:
            self._subtitle_logger.log(entry)
            latency_ms = int((time.time() - self._volc_started_at) * 1000) % 100000
            self.latency_reported.emit(min(latency_ms, 9999) if latency_ms > 0 else 0)
            dp.last_source = ""
            dp.last_translated = ""

    def _on_volc_audio(self, direction: Direction, data: bytes) -> None:
        if not data:
            return
        if direction == Direction.OUTBOUND and not self._play_outbound_voice:
            return
        if direction == Direction.INBOUND and not self._play_inbound_voice:
            return
        if self._ducker is not None:
            with contextlib.suppress(Exception):
                self._ducker.pulse()
        if self._player is not None:
            self._player.play(data)

    def _on_volc_error(self, message: str) -> None:
        self.log_message.emit(message)
        self.error_occurred.emit(message)

    def process_tick(self) -> None:
        """Volc uses direct PCM; tick kept as a lightweight no-op hook."""
        return

    def _volc_direct_pcm(self, name: str, pcm: bytes) -> None:
        if not pcm or self._volc is None:
            return
        if name not in self._volc.clients:
            return
        gate = self._vad_outbound if name == "outbound" else self._vad_inbound
        if gate is not None:
            ok = gate.accept(pcm)
            if name == "inbound":
                if ok:
                    self._vad_pass_chunks += 1
                else:
                    self._vad_drop_chunks += 1
                now = time.time()
                if now - self._vad_diag_at >= 5.0:
                    self._vad_diag_at = now
                    st = gate.stats()
                    total = self._vad_pass_chunks + self._vad_drop_chunks
                    if total:
                        msg = (
                            f"游戏捕获电平 RMS={st.rms:.4f} "
                            f"VAD={'开' if st.open else '关'} "
                            f"近5秒送出 {self._vad_pass_chunks}/{total}"
                        )
                        if st.rms < 0.002:
                            msg += " · 几乎无声，请确认「游戏声音」选的是正在播放视频的扬声器 Loopback"
                        elif self._vad_pass_chunks == 0:
                            msg += " · 有声但被 VAD 拦住，可在设置把灵敏度改「宽松」或暂时关闭 VAD"
                        self.log_message.emit(msg)
                        self._vad_pass_chunks = 0
                        self._vad_drop_chunks = 0
            if not ok:
                return
        self._volc.send_audio(name, pcm)

    def is_running(self) -> bool:
        return self._running

    @property
    def session(self) -> TranslationSession:
        return self._session
