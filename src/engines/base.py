"""Shared contracts for translation engines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from src.models.enums import Direction

EngineMode = Literal["volc", "economy"]


@dataclass
class EngineCallbacks:
    """Pipeline-owned callbacks; engines only own transport."""

    on_source_text: Callable[[Direction, str, bool], None]
    on_translated_text: Callable[[Direction, str, bool], None]
    on_audio: Callable[[Direction, bytes], None]
    on_error: Callable[[str], None]
    on_status: Callable[[str], None]
    on_usage: Callable[[str, dict[str, Any]], None]
    on_engine_status: Callable[[Direction, str, str], None]
    should_defer_rotate: Callable[[Direction], bool]


class TranslationEngine(Protocol):
    """Transport adapter: one engine instance may serve both directions."""

    @property
    def engine_id(self) -> EngineMode: ...

    @property
    def active_directions(self) -> frozenset[Direction]: ...

    def start_direction(self, direction: Direction, *, play_voice: bool = False) -> bool: ...

    def stop_direction(self, direction: Direction) -> None: ...

    def send_pcm(self, direction: Direction, pcm: bytes) -> None: ...

    def close(self) -> None: ...
