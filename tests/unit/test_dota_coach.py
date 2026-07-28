"""Unit tests for Dota coach HTTP bridge."""

from __future__ import annotations

from src.core.dota_coach import DotaCoachBridge, DEFAULT_COACH_URL


def test_ask_rejects_empty() -> None:
    bridge = DotaCoachBridge()
    assert bridge.ask("") is False
    assert bridge.ask("…") is False
    assert bridge.busy is False


def test_default_url() -> None:
    assert "3001" in DEFAULT_COACH_URL
    assert DEFAULT_COACH_URL.endswith("/ai/ask")
