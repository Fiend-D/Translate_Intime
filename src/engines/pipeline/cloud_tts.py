"""Cloud TTS backends: Doubao (Volcengine), CosyVoice (DashScope), OpenAI-compatible.

All return PCM16 mono 16 kHz, one HTTP/SDK call per sentence.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
import uuid
from typing import Any, Protocol

import httpx
import numpy as np

from src.engines.pipeline.tts_catalog import doubao_resource_id
from src.utils.audio_utils import float32_to_pcm16, resample
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.logger import logger

_TARGET_SR = 16000
_LOG_INTERVAL_SEC = 30.0
DOUBAO_TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
COSYVOICE_MODEL = "cosyvoice-v2"
_DOUBAO_DONE = 20000000


def encoded_audio_to_pcm16(data: bytes) -> bytes | None:
    """Decode wav/mp3/flac/ogg bytes to PCM16 mono 16 kHz."""
    if not data:
        return None
    import soundfile as sf

    samples, sr = sf.read(io.BytesIO(data), dtype="float32")
    if samples.ndim > 1:
        samples = np.mean(samples, axis=1)
    if not samples.size:
        return None
    return float32_to_pcm16(resample(samples.astype(np.float32), int(sr), _TARGET_SR))


def speed_to_percent(speed: float) -> int:
    """1.0 → 0, 0.7 → -30, 1.3 → 30 (Doubao speech_rate / Edge rate)."""
    return max(-50, min(100, round((float(speed) - 1.0) * 100)))


class _CloudTtsBase:
    _name = "云端语音"

    def __init__(self, *, voice_zh: str, voice_en: str, speed: float) -> None:
        self._voice_zh = (voice_zh or "").strip()
        self._voice_en = (voice_en or "").strip()
        self._speed = float(speed or 1.0)
        self._started = False
        self._last_err_at = 0.0

    @property
    def warming_up(self) -> bool:
        return False

    def _voice_for(self, language: str) -> str:
        lang = (language or "").lower()
        if lang.startswith(("zh", "yue")):
            return self._voice_zh or self._voice_en
        return self._voice_en or self._voice_zh

    def _warn(self, message: str) -> None:
        now = time.time()
        if now - self._last_err_at >= _LOG_INTERVAL_SEC:
            self._last_err_at = now
            logger.warning(f"{self._name}: {message}")


class _HttpTtsBase(_CloudTtsBase):
    def __init__(
        self,
        *,
        voice_zh: str,
        voice_en: str,
        speed: float,
        timeout: float,
        client: httpx.Client | None,
    ) -> None:
        super().__init__(voice_zh=voice_zh, voice_en=voice_en, speed=speed)
        self._timeout = float(timeout)
        self._client = client
        self._owns_client = client is None

    def start(self) -> None:
        self._started = True
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, trust_env=True)

    def stop(self) -> None:
        self._started = False
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


class DoubaoTts(_HttpTtsBase):
    """豆包语音合成大模型 (Seed-TTS 1.0 / 2.0) via the v3 HTTP unidirectional API."""

    _name = "豆包语音合成"

    def __init__(
        self,
        *,
        api_key: str = "",
        app_id: str = "",
        access_token: str = "",
        voice_zh: str = "zh_female_wanwanxiaohe_moon_bigtts",
        voice_en: str = "zh_female_shuangkuaisisi_moon_bigtts",
        speed: float = 1.0,
        url: str = DOUBAO_TTS_URL,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            voice_zh=voice_zh, voice_en=voice_en, speed=speed, timeout=timeout, client=client
        )
        self._api_key = (api_key or "").strip()
        self._app_id = (app_id or "").strip()
        self._access_token = (access_token or "").strip()
        self._url = url

    @property
    def configured(self) -> bool:
        return bool(self._api_key or (self._app_id and self._access_token))

    def _headers(self, voice: str) -> dict[str, str]:
        headers = {
            "X-Api-Resource-Id": doubao_resource_id(voice),
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        if self._app_id and self._access_token:
            headers["X-Api-App-Id"] = self._app_id
            headers["X-Api-Access-Key"] = self._access_token
        else:
            headers["X-Api-Key"] = self._api_key
        return headers

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text or not self._started or not self.configured or self._client is None:
            return None
        voice = self._voice_for(language)
        payload = {
            "user": {"uid": self._app_id or "translator-intime"},
            "req_params": {
                "text": text,
                "speaker": voice,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": _TARGET_SR,
                    "speech_rate": speed_to_percent(self._speed),
                },
            },
        }
        chunks: list[bytes] = []
        try:
            with self._client.stream(
                "POST", self._url, json=payload, headers=self._headers(voice)
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._warn(f"HTTP {resp.status_code} {resp.text[:200]}")
                    return None
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    code = int(event.get("code") or 0)
                    if code not in (0, _DOUBAO_DONE):
                        self._warn(f"{code} {event.get('message', '')}（音色 {voice}）")
                        return None
                    data = event.get("data")
                    if data:
                        chunks.append(base64.b64decode(data))
                    if code == _DOUBAO_DONE:
                        break
        except Exception as exc:
            self._warn(f"合成失败: {exc}")
            return None
        pcm = b"".join(chunks)
        return pcm[: len(pcm) & ~1] or None


class OpenAICompatTts(_HttpTtsBase):
    """POST {base}/audio/speech — OpenAI, SiliconFlow, Kokoro-FastAPI, self-hosted servers."""

    _name = "OpenAI 兼容语音合成"

    def __init__(
        self,
        *,
        url: str,
        model: str = "tts-1",
        api_key: str = "",
        voice_zh: str = "alloy",
        voice_en: str = "alloy",
        speed: float = 1.0,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            voice_zh=voice_zh, voice_en=voice_en, speed=speed, timeout=timeout, client=client
        )
        self._url = self.speech_url(url)
        self._model = (model or "").strip()
        self._api_key = (api_key or "").strip()

    @staticmethod
    def speech_url(url: str) -> str:
        url = (url or "").strip().rstrip("/")
        if not url or url.endswith("/audio/speech"):
            return url
        return f"{url}/audio/speech"

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text or not self._started or not self.configured or self._client is None:
            return None
        payload: dict[str, Any] = {
            "input": text,
            "voice": self._voice_for(language),
            "response_format": "wav",
            "speed": self._speed,
        }
        if self._model:
            payload["model"] = self._model
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            resp = self._client.post(self._url, json=payload, headers=headers)
            if resp.status_code >= 400:
                self._warn(f"HTTP {resp.status_code} {resp.text[:200]}")
                return None
            return encoded_audio_to_pcm16(resp.content)
        except Exception as exc:
            self._warn(f"合成失败: {exc}")
            return None


class CosyVoiceTts(_CloudTtsBase):
    """阿里云百炼 CosyVoice through the dashscope SDK (WebSocket per call)."""

    _name = "CosyVoice"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = COSYVOICE_MODEL,
        voice_zh: str = "longxiaochun_v2",
        voice_en: str = "loongbella_v2",
        speed: float = 1.0,
        timeout_ms: int = 20000,
    ) -> None:
        super().__init__(voice_zh=voice_zh, voice_en=voice_en, speed=speed)
        self._api_key = (api_key or "").strip()
        self._model = (model or COSYVOICE_MODEL).strip()
        self._timeout_ms = int(timeout_ms)
        self._sdk: Any | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def start(self) -> None:
        self._started = True
        if self._sdk is not None:
            return
        try:
            import dashscope
            from dashscope.audio import tts_v2

            dashscope.api_key = self._api_key
            self._sdk = tts_v2
        except ImportError:
            self._warn("未安装 dashscope，请执行: pip install dashscope")

    def stop(self) -> None:
        self._started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text or not self._started or not self.configured or self._sdk is None:
            return None
        sdk = self._sdk
        try:
            synth = sdk.SpeechSynthesizer(
                model=self._model,
                voice=self._voice_for(language),
                format=sdk.AudioFormat.PCM_16000HZ_MONO_16BIT,
                speech_rate=max(0.5, min(2.0, self._speed)),
            )
            audio = synth.call(text, timeout_millis=self._timeout_ms)
        except Exception as exc:
            self._warn(f"合成失败: {exc}")
            return None
        if not audio:
            return None
        pcm = bytes(audio)
        return pcm[: len(pcm) & ~1] or None


class _Tts(Protocol):
    @property
    def configured(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def synthesize(self, text: str, *, language: str) -> bytes | None: ...


class FallbackTts:
    """Use ``primary``; when it is unconfigured or fails, speak with ``fallback``."""

    def __init__(self, primary: _Tts, fallback: _Tts, *, name: str) -> None:
        self._primary = primary
        self._fallback = fallback
        self._name = name
        self._fallback_logged = False
        self._lock = threading.Lock()
        self._breaker = CircuitBreaker()

    @property
    def inner(self) -> _Tts:
        return self._primary

    @property
    def configured(self) -> bool:
        return self._primary.configured or self._fallback.configured

    @property
    def warming_up(self) -> bool:
        return bool(getattr(self._primary, "warming_up", False))

    def start(self) -> None:
        self._primary.start()
        self._fallback.start()

    def stop(self) -> None:
        self._primary.stop()
        self._fallback.stop()

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        if self._primary.configured and self._breaker.allow():
            pcm = self._primary.synthesize(text, language=language)
            if pcm or getattr(self._primary, "warming_up", False):
                self._breaker.record(True)
                return pcm
            if self._breaker.record(False):
                logger.warning(
                    f"{self._name}连续失败，{self._breaker.cooldown_s:.0f} 秒内直接使用 Edge TTS"
                )
        with self._lock:
            if not self._fallback_logged:
                self._fallback_logged = True
                reason = "未配置" if not self._primary.configured else "合成失败"
                logger.warning(f"{self._name}{reason}，已回退到 Edge TTS")
        return self._fallback.synthesize(text, language=language)


__all__ = [
    "CosyVoiceTts",
    "DoubaoTts",
    "FallbackTts",
    "OpenAICompatTts",
    "encoded_audio_to_pcm16",
    "speed_to_percent",
]
