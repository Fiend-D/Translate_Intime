"""Failing cloud backends are skipped for a cooldown instead of timing out per sentence."""

from __future__ import annotations

from src.engines.pipeline.llm_mt import FallbackMt
from src.utils import circuit_breaker
from src.utils.circuit_breaker import CircuitBreaker


def test_opens_after_threshold_then_probes_with_growing_cooldown(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr(circuit_breaker.time, "monotonic", lambda: now[0])
    breaker = CircuitBreaker(threshold=2, cooldown_s=10, max_cooldown_s=30)

    assert breaker.record(False) is False
    assert breaker.record(False) is True
    assert breaker.allow() is False

    now[0] = 11.0
    assert breaker.allow() is True  # half-open probe
    breaker.record(False)
    assert breaker.cooldown_s == 20
    assert breaker.allow() is False

    now[0] = 40.0
    assert breaker.allow() is True
    breaker.record(True)
    assert breaker.allow() is True
    assert breaker.cooldown_s == 10


class _Mt:
    configured = True

    def __init__(self, result: str | None) -> None:
        self.result = result
        self.calls = 0

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        self.calls += 1
        return self.result


def test_fallback_mt_stops_calling_a_dead_primary() -> None:
    primary, backup = _Mt(None), _Mt("你好")
    mt = FallbackMt(primary, backup)

    results = [mt.translate("hello", source_lang="en", target_lang="zh") for _ in range(10)]

    assert results == ["你好"] * 10
    assert primary.calls == 3
