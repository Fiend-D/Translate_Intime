"""Barge-in drops the rest of the Volc TTS sentence that is still streaming."""

from __future__ import annotations

import time

import pytest

from python_protogen.common.events_pb2 import Type  # type: ignore[attr-defined]
from python_protogen.products.understanding.ast.ast_service_pb2 import (  # type: ignore[attr-defined]
    TranslateResponse,
)
from src.core import volc_engine
from src.core.volc_engine import VolcASTClient


def _client(audio: list[bytes]) -> VolcASTClient:
    return VolcASTClient(
        api_key="00000000-0000-0000-0000-000000000000",
        on_audio=audio.append,
    )


def _send(client: VolcASTClient, event: int, data: bytes = b"") -> None:
    response = TranslateResponse()
    response.event = event
    if data:
        response.data = data
    client._handle_response(response.SerializeToString())


def test_cancel_drops_rest_of_current_sentence_only() -> None:
    audio: list[bytes] = []
    client = _client(audio)
    _send(client, Type.TTSSentenceStart)
    _send(client, Type.TTSResponse, b"a1")
    assert client.cancel_current_tts() is True
    _send(client, Type.TTSResponse, b"a2")
    _send(client, Type.TTSResponse, b"a3")
    _send(client, Type.TTSSentenceEnd)
    _send(client, Type.TTSSentenceStart)
    _send(client, Type.TTSResponse, b"b1")
    assert audio == [b"a1", b"b1"]


def test_cancel_between_sentences_is_a_no_op() -> None:
    audio: list[bytes] = []
    client = _client(audio)
    _send(client, Type.TTSSentenceStart)
    _send(client, Type.TTSResponse, b"a1")
    _send(client, Type.TTSSentenceEnd)
    assert client.cancel_current_tts() is False
    _send(client, Type.TTSSentenceStart)
    _send(client, Type.TTSResponse, b"b1")
    assert audio == [b"a1", b"b1"]


def test_without_sentence_events_drop_until_stream_pauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(volc_engine.time, "monotonic", lambda: clock[0])
    audio: list[bytes] = []
    client = _client(audio)
    _send(client, Type.TTSResponse, b"a1")
    clock[0] += 0.1
    assert client.cancel_current_tts() is True
    _send(client, Type.TTSResponse, b"a2")
    clock[0] += 0.1
    _send(client, Type.TTSResponse, b"a3")
    clock[0] += 1.0
    _send(client, Type.TTSResponse, b"b1")
    assert audio == [b"a1", b"b1"]


def test_drop_has_failsafe_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [time.monotonic()]
    monkeypatch.setattr(volc_engine.time, "monotonic", lambda: clock[0])
    audio: list[bytes] = []
    client = _client(audio)
    _send(client, Type.TTSSentenceStart)
    assert client.cancel_current_tts() is True
    clock[0] += 20.0
    _send(client, Type.TTSResponse, b"late")
    assert audio == [b"late"]
