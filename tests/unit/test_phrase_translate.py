"""Quick-phrase background translation."""

from __future__ import annotations

import threading

from src.core.phrase_translate import PhraseRequest, PhraseTranslator
from src.models.config import AppConfigModel


class _FakeMt:
    def __init__(self, warm_calls: int = 0, reply: str | None = "Hello") -> None:
        # The first ``warm_calls`` translations happen while the model still loads.
        self.warm_calls = warm_calls
        self.reply = reply
        self.started = 0
        self.stopped = 0
        self.calls: list[str] = []

    @property
    def warming_up(self) -> bool:
        return len(self.calls) <= self.warm_calls

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        self.calls.append(text)
        if len(self.calls) <= self.warm_calls:
            return None
        return self.reply


def _translator(mt: _FakeMt, config: list[AppConfigModel] | None = None, **kwargs):
    results: list[tuple[PhraseRequest, str | None]] = []
    done = threading.Event()
    built: list[AppConfigModel] = []
    cfg = config or [AppConfigModel()]

    def factory(c: AppConfigModel) -> _FakeMt:
        built.append(c)
        return mt

    def on_done(req: PhraseRequest, text: str | None) -> None:
        results.append((req, text))
        done.set()

    translator = PhraseTranslator(lambda: cfg[0], on_done, mt_factory=factory, **kwargs)
    return translator, results, done, built


def test_waits_for_a_loading_model_then_reports_translation() -> None:
    mt = _FakeMt(warm_calls=2)
    translator, results, done, _ = _translator(mt)
    assert translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert done.wait(5)
    translator.close()
    assert results[0][1] == "Hello"
    assert mt.calls == ["你好"] * 3
    assert mt.stopped == 1


def test_same_language_and_duplicates_skip_the_model() -> None:
    mt = _FakeMt()
    translator, results, done, built = _translator(mt)
    gate = threading.Event()
    original = mt.translate

    def slow(text: str, **kw: str) -> str | None:
        gate.wait(5)
        return original(text, **kw)

    mt.translate = slow  # type: ignore[method-assign]
    assert translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert not translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert translator.is_pending("p1")
    gate.set()
    assert done.wait(5)
    done.clear()
    assert translator.request(PhraseRequest("p2", "same", "en", "en"))
    assert done.wait(5)
    translator.close()
    assert [r[1] for r in results] == ["Hello", "same"]
    assert len(built) == 1


def test_failed_translation_reports_none() -> None:
    mt = _FakeMt(reply=None)
    translator, results, done, _ = _translator(mt)
    translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert done.wait(5)
    translator.close()
    assert results[0][1] is None


def test_idle_translator_releases_model() -> None:
    mt = _FakeMt()
    translator, _results, done, _ = _translator(mt, idle_release_s=0.05)
    translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert done.wait(5)
    for _ in range(100):
        if mt.stopped:
            break
        threading.Event().wait(0.02)
    assert mt.stopped == 1
    translator.close()


def test_changing_mt_settings_rebuilds_backend() -> None:
    mt = _FakeMt()
    config = [AppConfigModel(economy_mt_backend="nllb")]
    translator, _results, done, built = _translator(mt, config)
    translator.request(PhraseRequest("p1", "你好", "zh", "en"))
    assert done.wait(5)
    done.clear()
    config[0] = AppConfigModel(economy_mt_backend="hymt")
    translator.request(PhraseRequest("p2", "再见", "zh", "en"))
    assert done.wait(5)
    translator.close()
    assert [c.economy_mt_backend for c in built] == ["nllb", "hymt"]


def test_closed_translator_rejects_requests() -> None:
    translator, _results, _done, _ = _translator(_FakeMt())
    translator.close()
    assert not translator.request(PhraseRequest("p1", "你好", "zh", "en"))
