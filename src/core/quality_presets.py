"""Quality preset helpers for VAD and streaming policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityPresetParams:
    vad_enabled: bool
    vad_game_enabled: bool
    vad_sensitivity: str
    vad_open_ms: int
    vad_hangover_ms: int
    vad_backend: str = "auto"
    vad_preroll_ms: int = 300
    vad_barge_in_ms: int = 200


_PRESETS: dict[str, QualityPresetParams] = {
    "quality": QualityPresetParams(
        vad_enabled=True,
        vad_game_enabled=False,
        vad_sensitivity="low",
        vad_open_ms=60,
        vad_hangover_ms=900,
    ),
    "balanced": QualityPresetParams(
        vad_enabled=True,
        vad_game_enabled=True,
        vad_sensitivity="medium",
        vad_open_ms=80,
        vad_hangover_ms=600,
    ),
    "saver": QualityPresetParams(
        vad_enabled=True,
        vad_game_enabled=True,
        vad_sensitivity="high",
        vad_open_ms=120,
        vad_hangover_ms=450,
    ),
    "turbo": QualityPresetParams(
        vad_enabled=True,
        vad_game_enabled=False,
        vad_sensitivity="medium",
        vad_open_ms=40,
        vad_hangover_ms=300,
    ),
}


def apply_quality_preset(name: str) -> QualityPresetParams:
    """Return preset parameters; unknown names fall back to balanced."""
    return _PRESETS.get((name or "balanced").strip().lower(), _PRESETS["balanced"])

