"""Subtitle data models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.models.enums import Direction


class SubtitleEntry(BaseModel):
    direction: Direction
    original_text: str
    translated_text: str
    is_final: bool = True
    timestamp: datetime = Field(default_factory=datetime.now)
