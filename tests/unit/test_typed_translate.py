"""Tests for typed translation's in-memory TTS path."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from src.core.typed_translate import run_async, synthesize_edge_tts


@pytest.mark.asyncio
async def test_edge_tts_returns_in_memory_audio(monkeypatch) -> None:
    class FakeCommunicate:
        def __init__(self, text: str, voice: str) -> None:
            assert text == "hello"
            assert voice

        async def stream(self):
            yield {"type": "WordBoundary", "data": b"ignored"}
            yield {"type": "audio", "data": b"first"}
            yield {"type": "audio", "data": b"second"}

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate))

    audio = await synthesize_edge_tts("hello", "en")

    assert audio == b"firstsecond"


@pytest.mark.asyncio
async def test_edge_tts_rejects_empty_audio(monkeypatch) -> None:
    class FakeCommunicate:
        def __init__(self, text: str, voice: str) -> None:
            del text, voice

        async def stream(self):
            yield {"type": "metadata", "data": b""}

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate))

    with pytest.raises(RuntimeError, match="未返回音频"):
        await synthesize_edge_tts("hello", "en")


def test_run_async_rejects_running_event_loop_without_leaking_coroutine() -> None:
    async def sample() -> int:
        return 1

    async def caller() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            run_async(sample())

    asyncio.run(caller())
