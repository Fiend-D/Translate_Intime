"""Kokoro model files resume across mirrors instead of restarting."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import httpx

from src.engines.pipeline import kokoro_tts


def test_chinese_is_synthesized_from_misaki_phonemes(tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    class _FakeKokoro:
        def create(self, text: str, **kwargs):
            calls.append((text, kwargs))
            return [0.0] * 240, 24000

    tts = kokoro_tts.KokoroOnnxTts(cache_dir=tmp_path, auto_download=False)
    tts._kokoro_zh = _FakeKokoro()
    tts._zh_g2p = lambda text: (f"ㄋㄧ3:{text}", None)

    tts._create_zh("你好。")

    assert calls == [
        ("ㄋㄧ3:你好。", {"voice": tts._pick_zh_voice(), "speed": tts._speed, "is_phonemes": True})
    ]


def test_zh_voice_keeps_choice_and_migrates_legacy_names(tmp_path) -> None:
    from src.models.config import AppConfigModel

    def pick(voice: str) -> str:
        tts = kokoro_tts.KokoroOnnxTts(cache_dir=tmp_path, voice_zh=voice, auto_download=False)
        tts._zh_voices = {"zf_001", "zf_003", "zm_010", "zm_050"}
        return tts._pick_zh_voice()

    assert pick("zm_050") == "zm_050"
    assert pick("zf_003") == "zf_003"
    assert pick("zm_yunxi") == "zm_010"
    assert pick("zm_999") == "zm_010"
    assert AppConfigModel(economy_kokoro_voice_zh="zm_yunxi").economy_kokoro_voice_zh == "zm_010"


def test_misaki_v11_emits_zhuyin_tokens() -> None:
    from misaki import zh

    phonemes, _ = zh.ZHG2P(version="1.1")("你好")

    assert any("\u3105" <= ch <= "\u312f" for ch in phonemes)
    assert any(ch.isdigit() for ch in phonemes)


def test_partial_download_continues_on_next_mirror(tmp_path, monkeypatch) -> None:
    payload = b"k" * 400
    monkeypatch.setitem(
        kokoro_tts._FILE_SHA256, "voices-v1.0.bin", hashlib.sha256(payload).hexdigest()
    )
    requests: list[tuple[str, str | None]] = []

    @contextmanager
    def fake_stream(_method, url, *, headers=None, **_kw):
        rng = (headers or {}).get("Range")
        requests.append((url, rng))
        start = int(rng.removeprefix("bytes=").rstrip("-")) if rng else 0
        if "first" in url and rng:
            raise httpx.ConnectError("mirror gone")

        def body():
            yield payload[start : start + 150]
            if "first" in url:
                raise httpx.ReadError("peer closed")
            yield payload[start + 150 :]

        yield SimpleNamespace(
            status_code=206 if start else 200,
            headers={"content-length": str(len(payload) - start)},
            raise_for_status=lambda: None,
            iter_bytes=lambda _n: body(),
        )

    monkeypatch.setattr(httpx, "stream", fake_stream)
    tts = kokoro_tts.KokoroOnnxTts(cache_dir=tmp_path)
    target = tmp_path / "voices-v1.0.bin"

    assert tts._ensure_file(target, ["https://first/v", "https://second/v"]) is True

    assert target.read_bytes() == payload
    assert requests[-1] == ("https://second/v", "bytes=150-")
    assert not (tmp_path / "voices-v1.0.bin.part").exists()


def test_hash_mismatch_tries_the_next_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(kokoro_tts._FILE_SHA256, "voices-v1.0.bin", "0" * 64)
    urls: list[str] = []

    def fake_fetch(url, dest, **_kw):
        urls.append(url)
        dest.write_bytes(b"other bytes")

    monkeypatch.setattr(kokoro_tts, "fetch_resumable", fake_fetch)
    tts = kokoro_tts.KokoroOnnxTts(cache_dir=tmp_path)

    assert tts._ensure_file(tmp_path / "voices-v1.0.bin", ["https://a/v", "https://b/v"]) is False
    assert urls == ["https://a/v", "https://b/v"]
    assert not (tmp_path / "voices-v1.0.bin").exists()


def test_unverified_file_is_kept_when_downloads_are_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(kokoro_tts._FILE_SHA256, "voices-v1.0.bin", "0" * 64)
    target = tmp_path / "voices-v1.0.bin"
    target.write_bytes(b"user copy")
    tts = kokoro_tts.KokoroOnnxTts(cache_dir=tmp_path, auto_download=False)

    assert tts._ensure_file(target, ["https://a/v"]) is False
    assert target.read_bytes() == b"user copy"
