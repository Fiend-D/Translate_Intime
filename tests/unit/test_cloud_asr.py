"""Qwen3-ASR-Flash and Doubao flash ASR request/response handling (HTTP mocked)."""

from __future__ import annotations

import base64
import json
import wave
from io import BytesIO

import httpx

from src.engines.pipeline.cloud_asr import DoubaoFlashAsr, QwenFlashAsr, pcm16_to_wav
from src.engines.pipeline.engine import (
    build_economy_backends,
    economy_asr_requires_dashscope,
)
from src.engines.pipeline.qwen_asr import QwenLocalAsr
from src.models.config import AppConfigModel
from src.models.enums import Direction

PCM = b"\x01\x00" * 1600


def _client(response: httpx.Response, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pcm16_to_wav_header() -> None:
    with wave.open(BytesIO(pcm16_to_wav(PCM))) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 1600


def test_qwen_flash_posts_wav_language_and_context() -> None:
    seen: list[httpx.Request] = []
    reply = httpx.Response(200, json={"choices": [{"message": {"content": "推中路"}}]})
    asr = QwenFlashAsr(api_key="sk-x", hotwords=["肉山", "推中"], client=_client(reply, seen))
    asr.start()
    assert asr.recognize(PCM, language="zh") == "推中路"

    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer sk-x"
    assert body["model"] == "qwen3-asr-flash"
    assert body["asr_options"]["language"] == "zh"
    assert body["messages"][0]["content"][0]["text"] == "肉山 推中"
    data_uri = body["messages"][1]["content"][0]["input_audio"]["data"]
    assert data_uri.startswith("data:audio/wav;base64,")
    assert base64.b64decode(data_uri.split(",", 1)[1])[:4] == b"RIFF"


def test_doubao_flash_uses_legacy_headers_and_reads_text() -> None:
    seen: list[httpx.Request] = []
    reply = httpx.Response(
        200,
        json={"result": {"text": "我们去打肉山"}},
        headers={"X-Api-Status-Code": "20000000"},
    )
    asr = DoubaoFlashAsr(
        app_id="123456", access_token="tok", hotwords=["肉山"], client=_client(reply, seen)
    )
    asr.start()
    assert asr.recognize(PCM, language="zh") == "我们去打肉山"

    headers = seen[0].headers
    assert headers["x-api-app-key"] == "123456"
    assert headers["x-api-access-key"] == "tok"
    assert headers["x-api-resource-id"] == "volc.bigasr.auc_turbo"
    body = json.loads(seen[0].content)
    assert json.loads(body["request"]["corpus"]["context"]) == {"hotwords": [{"word": "肉山"}]}


def test_doubao_flash_api_key_and_error_status() -> None:
    seen: list[httpx.Request] = []
    reply = httpx.Response(
        200, json={}, headers={"X-Api-Status-Code": "45000001", "X-Api-Message": "bad"}
    )
    asr = DoubaoFlashAsr(api_key="uuid-key", client=_client(reply, seen))
    asr.start()
    assert asr.recognize(PCM, language="en") is None
    assert seen[0].headers["x-api-key"] == "uuid-key"


def test_factory_routes_new_asr_backends(tmp_path) -> None:
    qwen = AppConfigModel(
        log_dir=str(tmp_path),
        economy_asr_backend="qwen_flash",
        economy_dashscope_api_key="sk-x",
        economy_mt_backend="mymemory",
    )
    doubao = AppConfigModel(
        log_dir=str(tmp_path),
        economy_asr_backend="doubao_flash",
        volc_api_key="00000000-0000-0000-0000-000000000000",
        economy_mt_backend="mymemory",
    )
    local_qwen = AppConfigModel(
        log_dir=str(tmp_path),
        economy_asr_backend="local",
        economy_asr_mic_model="qwen3-asr-0.6b",
        economy_mt_backend="mymemory",
    )
    assert isinstance(build_economy_backends(qwen)[0][Direction.INBOUND], QwenFlashAsr)
    assert economy_asr_requires_dashscope(qwen) is True
    asr = build_economy_backends(doubao)[0][Direction.OUTBOUND]
    assert isinstance(asr, DoubaoFlashAsr) and asr.configured
    assert economy_asr_requires_dashscope(doubao) is False
    local_asrs = build_economy_backends(local_qwen)[0]
    assert isinstance(local_asrs[Direction.OUTBOUND], QwenLocalAsr)
    assert not isinstance(local_asrs[Direction.INBOUND], QwenLocalAsr)
