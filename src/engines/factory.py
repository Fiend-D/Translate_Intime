"""Factory for translation engines."""

from __future__ import annotations

from src.engines.base import EngineCallbacks, EngineMode, TranslationEngine
from src.models.config import AppConfigModel


def create_engine(
    mode: EngineMode,
    config: AppConfigModel,
    callbacks: EngineCallbacks,
) -> TranslationEngine:
    """Create an isolated engine instance for the requested mode."""
    if mode == "volc":
        from src.engines.volc.engine import VolcTranslationEngine

        return VolcTranslationEngine(config=config, callbacks=callbacks)
    if mode == "economy":
        from src.engines.pipeline.engine import EconomyPipelineEngine

        return EconomyPipelineEngine(config=config, callbacks=callbacks)
    raise ValueError(f"Unknown translation mode: {mode!r}")
