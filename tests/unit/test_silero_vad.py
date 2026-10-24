"""Silero VAD model file recovery (download faked, no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.core import silero_vad
from src.core.silero_vad import SileroVadEngine


def test_truncated_model_is_redownloaded_once(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"truncated")
    downloads: list[Path] = []
    opened: list[bytes] = []

    def fake_urlretrieve(url: str, target: Path, *, reporthook=None) -> None:
        del url
        downloads.append(Path(target))
        Path(target).write_bytes(b"good")

    def fake_open(self: SileroVadEngine) -> None:
        data = self.model_path.read_bytes()
        opened.append(data)
        if data != b"good":
            raise RuntimeError("INVALID_PROTOBUF")

    monkeypatch.setattr(silero_vad, "_fetch", fake_urlretrieve)
    monkeypatch.setattr(SileroVadEngine, "_open_session", fake_open)
    monkeypatch.setattr(silero_vad, "SILERO_VAD_SHA256", hashlib.sha256(b"good").hexdigest())

    assert SileroVadEngine(model)._load() is True
    assert opened == [b"truncated", b"good"]
    assert downloads == [model.with_name("silero_vad.onnx.part")]
    assert model.read_bytes() == b"good"
    assert not model.with_name("silero_vad.onnx.part").exists()


def test_download_with_wrong_hash_is_rejected(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "silero_vad.onnx"
    fetched: list[str] = []

    def tampered(url: str, target: Path, *, reporthook=None) -> None:
        fetched.append(url)
        Path(target).write_bytes(b"<html>mirror error</html>")

    monkeypatch.setattr(silero_vad, "_fetch", tampered)

    assert SileroVadEngine(model)._load() is False
    assert len(fetched) == len(silero_vad.silero_vad_urls())
    assert not model.exists()
    assert not model.with_name("silero_vad.onnx.part").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_failed_download_leaves_no_partial_file(tmp_path: Path, monkeypatch, existing) -> None:
    model = tmp_path / "silero_vad.onnx"
    if existing:
        model.write_bytes(b"truncated")

    def broken_urlretrieve(url: str, target: Path, *, reporthook=None) -> None:
        del url
        Path(target).write_bytes(b"half")
        raise OSError("connection reset")

    def fake_open(self: SileroVadEngine) -> None:
        raise RuntimeError("INVALID_PROTOBUF")

    monkeypatch.setattr(silero_vad, "_fetch", broken_urlretrieve)
    monkeypatch.setattr(SileroVadEngine, "_open_session", fake_open)

    assert SileroVadEngine(model)._load() is False
    assert not model.exists()
    assert not model.with_name("silero_vad.onnx.part").exists()
