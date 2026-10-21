"""AutoTts falls back to an offline Chinese voice before the online edge-tts."""

from __future__ import annotations

from src.engines.pipeline.tts import AutoTts


class _Fake:
    def __init__(self, pcm: bytes | None = None, *, warming: bool = False) -> None:
        self.pcm = pcm
        self.warming_up = warming
        self.configured = pcm is not None
        self.started = False
        self.calls = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        self.calls += 1
        return self.pcm


def _auto(local_zh: _Fake, edge: _Fake) -> AutoTts:
    tts = AutoTts(prefer="kokoro", kokoro=_Fake(), edge=edge, local_zh=local_zh)  # type: ignore[arg-type]
    tts.start()
    return tts


def test_chinese_uses_local_voice_when_kokoro_has_no_chinese_model() -> None:
    melo, edge = _Fake(b"melo"), _Fake(b"edge")
    tts = _auto(melo, edge)

    assert tts.synthesize("你好", language="zh") == b"melo"
    assert melo.started is True
    assert edge.calls == 0


def test_english_never_starts_the_chinese_fallback() -> None:
    melo, edge = _Fake(b"melo"), _Fake(b"edge")
    tts = _auto(melo, edge)

    assert tts.synthesize("hello", language="en") == b"edge"
    assert melo.started is False


def test_failure_reason_names_the_missing_pieces() -> None:
    tts = _auto(_Fake(None, warming=True), _Fake(None))

    assert tts.synthesize("你好", language="zh") is None
    assert "MeloTTS" in tts.last_failure
