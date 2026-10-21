"""faster-whisper picks CUDA from CTranslate2's own device count."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.engines.pipeline.whisper_asr import _resolve_device


@pytest.mark.parametrize(("count", "expected"), [(1, "cuda"), (0, "cpu")])
def test_auto_follows_ctranslate2_gpu_count(monkeypatch, count: int, expected: str) -> None:
    fake = SimpleNamespace(get_cuda_device_count=lambda: count)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)

    assert _resolve_device("auto") == expected


def test_explicit_preference_wins(monkeypatch) -> None:
    fake = SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)

    assert _resolve_device("cuda") == "cuda"
    assert _resolve_device("cpu") == "cpu"
