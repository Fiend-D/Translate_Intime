"""Utterance-level cloud ASR: Qwen3-ASR-Flash (DashScope) and Doubao flash (Volcengine)."""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
import wave
from typing import Any

import httpx

from src.utils.logger import logger

_LOG_INTERVAL_SEC = 30.0
_SAMPLE_RATE = 16000

QWEN_ASR_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_ASR_MODEL = "qwen3-asr-flash"
DOUBAO_FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
DOUBAO_FLASH_RESOURCE = "volc.bigasr.auc_turbo"
_DOUBAO_OK = "20000000"


def pcm16_to_wav(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


class _HttpAsrBase:
    def __init__(self, *, timeout: float, client: httpx.Client | None) -> None:
        self._timeout = float(timeout)
        self._client = client
        self._owns_client = client is None
        self._started = False
        self._last_err_at = 0.0

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, trust_env=True)

    def stop(self) -> None:
        self._started = False
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _warn(self, message: str) -> None:
        now = time.time()
        if now - self._last_err_at >= _LOG_INTERVAL_SEC:
            self._last_err_at = now
            logger.warning(message)


class QwenFlashAsr(_HttpAsrBase):
    """Qwen3-ASR-Flash through DashScope's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = QWEN_ASR_MODEL,
        hotwords: list[str] | None = None,
        url: str = QWEN_ASR_URL,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self._api_key = (api_key or "").strip()
        self._model = (model or QWEN_ASR_MODEL).strip()
        self._hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]
        self._url = url

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        if not pcm or not self._started or not self.configured or self._client is None:
            return None
        audio = base64.b64encode(pcm16_to_wav(pcm)).decode("ascii")
        messages: list[dict[str, Any]] = []
        if self._hotwords:
            # Qwen3-ASR biases recognition with free-form context text.
            messages.append({"role": "system", "content": [{"text": " ".join(self._hotwords)}]})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": f"data:audio/wav;base64,{audio}"},
                    }
                ],
            }
        )
        asr_options: dict[str, Any] = {"enable_itn": False}
        lang = (language or "")[:2].lower()
        if lang and lang != "au":
            asr_options["language"] = lang
        payload = {"model": self._model, "messages": messages, "asr_options": asr_options}
        try:
            resp = self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            self._warn(f"Qwen3-ASR-Flash 识别失败: {exc}")
            return None
        return str(text or "").strip() or None


class DoubaoFlashAsr(_HttpAsrBase):
    """Doubao (Seed-ASR) flash recognition: one HTTP request per utterance."""

    def __init__(
        self,
        *,
        api_key: str = "",
        app_id: str = "",
        access_token: str = "",
        resource_id: str = DOUBAO_FLASH_RESOURCE,
        hotwords: list[str] | None = None,
        url: str = DOUBAO_FLASH_URL,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self._api_key = (api_key or "").strip()
        self._app_id = (app_id or "").strip()
        self._access_token = (access_token or "").strip()
        self._resource_id = resource_id or DOUBAO_FLASH_RESOURCE
        self._hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]
        self._url = url

    @property
    def configured(self) -> bool:
        return bool(self._api_key or (self._app_id and self._access_token))

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        if self._app_id and self._access_token:
            headers["X-Api-App-Key"] = self._app_id
            headers["X-Api-Access-Key"] = self._access_token
        else:
            headers["X-Api-Key"] = self._api_key
        return headers

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        del language  # bigmodel auto-detects zh/en/ja/ko and dialects
        if not pcm or not self._started or not self.configured or self._client is None:
            return None
        request: dict[str, Any] = {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
        }
        if self._hotwords:
            hotwords = [{"word": w} for w in self._hotwords[:100]]
            request["corpus"] = {"context": json.dumps({"hotwords": hotwords}, ensure_ascii=False)}
        payload = {
            "user": {"uid": self._app_id or "translator-intime"},
            "audio": {"data": base64.b64encode(pcm16_to_wav(pcm)).decode("ascii")},
            "request": request,
        }
        try:
            resp = self._client.post(self._url, json=payload, headers=self._headers())
            status = resp.headers.get("X-Api-Status-Code", "")
            if status and status != _DOUBAO_OK:
                message = resp.headers.get("X-Api-Message", "")
                self._warn(f"豆包极速识别失败: {status} {message}")
                return None
            resp.raise_for_status()
            text = (resp.json().get("result") or {}).get("text", "")
        except Exception as exc:
            self._warn(f"豆包极速识别失败: {exc}")
            return None
        return str(text or "").strip() or None
