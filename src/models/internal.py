"""Internal runtime data structures."""

from __future__ import annotations

from dataclasses import dataclass

from src.models.enums import Direction


@dataclass(slots=True)
class AudioChunk:
    direction: Direction
    sample_rate: int
    channels: int
    data: bytes
