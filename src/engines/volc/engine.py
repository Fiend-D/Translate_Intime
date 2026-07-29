"""Volc AST 2.0 transport engine (one VolcRuntime, outbound/inbound clients)."""

from __future__ import annotations

import contextlib

from src.core.volc_engine import VolcASTClient, VolcRuntime, resolve_volc_credentials
from src.engines.base import EngineCallbacks, EngineMode
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.utils.logger import logger


class VolcTranslationEngine:
    """Wraps VolcRuntime + VolcASTClient for a single pipeline session."""

    def __init__(self, *, config: AppConfigModel, callbacks: EngineCallbacks) -> None:
        self._config = config
        self._callbacks = callbacks
        self._runtime: VolcRuntime | None = None
        self._active: set[Direction] = set()

    @property
    def engine_id(self) -> EngineMode:
        return "volc"

    @property
    def active_directions(self) -> frozenset[Direction]:
        return frozenset(self._active)

    def start_direction(self, direction: Direction, *, play_voice: bool = False) -> bool:
        api_key, access_token, auth = resolve_volc_credentials(
            self._config.volc_api_key,
            self._config.volc_access_token,
        )
        if not api_key:
            return False

        logger.info(
            f"Volc auth mode={auth}, key_len={len(api_key)} channel={direction.value}"
        )
        if self._runtime is None:
            self._runtime = VolcRuntime()

        src = self._config.source_language
        tgt = self._config.target_language
        name = direction.value
        speaker_id = getattr(self._config, "volc_speaker_id", "") or ""
        speech_rate = int(getattr(self._config, "volc_speech_rate", 0) or 0)
        hotwords = list(getattr(self._config, "hotwords", None) or [])
        glossary = dict(getattr(self._config, "glossary", None) or {})

        if name in self._runtime.clients:
            self._active.add(direction)
            return True

        try:
            rotate_m = int(getattr(self._config, "volc_session_rotate_minutes", 12) or 0)
            cb = self._callbacks

            def _defer_rotate(d: Direction = direction) -> bool:
                return bool(cb.should_defer_rotate(d))

            common_kw = dict(
                api_key=api_key,
                access_token=access_token,
                speech_rate=speech_rate,
                hotwords=hotwords,
                glossary=glossary,
                on_error=cb.on_error,
                on_status=cb.on_status,
                on_usage=lambda payload, src_name=name: cb.on_usage(src_name, payload),
                session_rotate_minutes=rotate_m,
                should_defer_rotate=_defer_rotate,
            )
            mode = "s2s" if play_voice else "s2t"
            if direction == Direction.OUTBOUND:
                source_language, target_language = src, tgt
            else:
                source_language, target_language = tgt, src

            client = VolcASTClient(
                **common_kw,
                source_language=source_language,
                target_language=target_language,
                mode=mode,  # type: ignore[arg-type]
                speaker_id=speaker_id if mode == "s2s" else "",
                on_source_text=lambda text, final, d=direction: cb.on_source_text(
                    d, text, final
                ),
                on_translated_text=lambda text, final, d=direction: cb.on_translated_text(
                    d, text, final
                ),
                on_audio=lambda data, d=direction: cb.on_audio(d, data),
            )

            if hotwords or glossary:
                cb.on_status(f"热词 {len(hotwords)} 条 / 术语 {len(glossary)} 条已加载")
            if speech_rate:
                cb.on_status(f"同传语速: {speech_rate}")
            if mode == "s2s":
                voice_label = speaker_id or "原音色复刻"
                cb.on_status(f"火山语音输出音色: {voice_label}")

            if self._runtime.connect_client(name, client):
                self._active.add(direction)
                cb.on_engine_status(direction, "volc", "ready")
                return True
            cb.on_engine_status(direction, "volc", "error")
            return False
        except Exception as exc:
            logger.error(f"Volc channel start failed ({direction.value}): {exc}")
            self._callbacks.on_status(f"火山启动异常: {exc}")
            self._callbacks.on_engine_status(direction, "volc", "error")
            return False

    def stop_direction(self, direction: Direction) -> None:
        name = direction.value
        if self._runtime is not None:
            with contextlib.suppress(Exception):
                self._runtime.disconnect_client(name)
            if not self._runtime.clients:
                with contextlib.suppress(Exception):
                    self._runtime.stop()
                self._runtime = None
        self._active.discard(direction)

    def send_pcm(self, direction: Direction, pcm: bytes) -> None:
        if not pcm or self._runtime is None:
            return
        name = direction.value
        if name not in self._runtime.clients:
            return
        with contextlib.suppress(Exception):
            self._runtime.send_audio(name, pcm)

    def close(self) -> None:
        for direction in list(self._active):
            self.stop_direction(direction)
        if self._runtime is not None:
            with contextlib.suppress(Exception):
                self._runtime.stop()
            self._runtime = None
        self._active.clear()
