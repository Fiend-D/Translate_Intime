"""TTS backends for the economy pipeline (PCM16 mono 16 kHz)."""

from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from src.engines.pipeline.kokoro_tts import KokoroOnnxTts
from src.utils.audio_utils import float32_to_pcm16, resample
from src.utils.logger import logger

_STUB_MSG = "经济模式尚未配置 TTS"
_LOG_INTERVAL_SEC = 30.0
_TARGET_SR = 16000

_EDGE_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
}


class TtsBackend(Protocol):
    @property
    def configured(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def synthesize(self, text: str, *, language: str) -> bytes | None: ...


class UnconfiguredTts:
    """Placeholder TTS that produces no audio."""

    def __init__(self) -> None:
        self._last_log_at = 0.0
        self._started = False

    @property
    def configured(self) -> bool:
        return False

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del language
        if not text or not self._started:
            return None
        now = time.time()
        if now - self._last_log_at >= _LOG_INTERVAL_SEC:
            self._last_log_at = now
            logger.warning(_STUB_MSG)
        return None


class EdgePcmTts:
    """edge-tts → mp3 → decode/resample to PCM16 mono 16 kHz."""

    def __init__(self) -> None:
        self._started = False
        self._edge_tts: Any | None = None
        self._last_err_at = 0.0
        try:
            import edge_tts

            self._edge_tts = edge_tts
        except ImportError:
            self._edge_tts = None

    @property
    def configured(self) -> bool:
        return self._edge_tts is not None

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True
        if self._edge_tts is None:
            logger.warning("未安装 edge-tts，请执行: pip install edge-tts")

    def stop(self) -> None:
        self._started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text or not self._started or self._edge_tts is None:
            return None
        edge_tts_module = self._edge_tts
        voice = _EDGE_VOICES.get((language or "en")[:2].lower(), _EDGE_VOICES["en"])
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            async def _run() -> None:
                communicate = edge_tts_module.Communicate(text, voice)
                await communicate.save(str(tmp_path))

            asyncio.run(_run())
            return self._mp3_to_pcm16(tmp_path)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"edge-tts synthesize failed: {exc}")
            return None
        finally:
            if tmp_path is not None:
                with suppress(Exception):
                    tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _mp3_to_pcm16(path: Path) -> bytes | None:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        data = resample(data.astype(np.float32), int(sr), _TARGET_SR)
        return float32_to_pcm16(data)


class Pyttsx3Tts:
    """Optional true-local offline TTS fallback (quality varies by OS)."""

    def __init__(self) -> None:
        self._started = False
        self._engine = None
        self._last_err_at = 0.0
        try:
            import pyttsx3

            self._pyttsx3 = pyttsx3
        except ImportError:
            self._pyttsx3 = None

    @property
    def configured(self) -> bool:
        return self._pyttsx3 is not None

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True
        if self._pyttsx3 is None:
            return
        try:
            self._engine = self._pyttsx3.init()
        except Exception as exc:
            logger.debug(f"pyttsx3 init failed: {exc}")
            self._engine = None

    def stop(self) -> None:
        self._started = False
        self._engine = None

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del language
        text = (text or "").strip()
        if not text or not self._started or self._engine is None:
            return None
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            self._engine.save_to_file(text, str(tmp_path))
            self._engine.runAndWait()
            import soundfile as sf

            data, sr = sf.read(str(tmp_path), dtype="float32")
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            data = resample(data.astype(np.float32), int(sr), _TARGET_SR)
            return float32_to_pcm16(data)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"pyttsx3 synthesize failed: {exc}")
            return None
        finally:
            if tmp_path is not None:
                with suppress(Exception):
                    tmp_path.unlink(missing_ok=True)


class AutoTts:
    """Prefer Kokoro ONNX; fall back to Edge TTS (then pyttsx3) when unavailable."""

    def __init__(
        self,
        *,
        prefer: str = "auto",
        kokoro: KokoroOnnxTts | None = None,
        edge: EdgePcmTts | None = None,
        pyttsx3: Pyttsx3Tts | None = None,
        voice_en: str | None = None,
        voice_zh: str | None = None,
    ) -> None:
        self._prefer = (prefer or "auto").lower()
        self._kokoro = kokoro or KokoroOnnxTts(
            voice_en=voice_en or "af_bella",
            voice_zh=voice_zh or "zf_xiaoxiao",
        )
        self._edge = edge or EdgePcmTts()
        self._pyttsx3 = pyttsx3 or Pyttsx3Tts()
        self._started = False
        self._fallback_logged = False

    @property
    def configured(self) -> bool:
        if self._prefer == "kokoro":
            return self._kokoro.configured
        if self._prefer == "edge":
            return self._edge.configured
        return self._kokoro.configured or self._edge.configured or self._pyttsx3.configured

    @property
    def warming_up(self) -> bool:
        if self._prefer in ("auto", "kokoro"):
            return bool(getattr(self._kokoro, "warming_up", False))
        return False

    def start(self) -> None:
        self._started = True
        if self._prefer in ("auto", "kokoro"):
            self._kokoro.start()
        if self._prefer in ("auto", "edge", "kokoro"):
            self._edge.start()
        if self._prefer == "auto":
            self._pyttsx3.start()

    def stop(self) -> None:
        self._started = False
        self._kokoro.stop()
        self._edge.stop()
        self._pyttsx3.stop()

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        if not text or not self._started:
            return None
        if self._prefer == "edge":
            return self._edge.synthesize(text, language=language)

        pcm = self._kokoro.synthesize(text, language=language)
        if pcm:
            return pcm
        if getattr(self._kokoro, "warming_up", False):
            return None

        pcm = self._edge.synthesize(text, language=language)
        if pcm:
            if not self._fallback_logged:
                self._fallback_logged = True
                logger.warning("Kokoro 不可用，已回退到 edge-tts")
            return pcm
        if self._prefer == "auto":
            return self._pyttsx3.synthesize(text, language=language)
        return None
