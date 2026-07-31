"""Application enum types."""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class LanguageCode(StrEnum):
    ZH = "zh"
    EN = "en"
    JA = "ja"
    KO = "ko"
    FR = "fr"
    DE = "de"
    ES = "es"
    RU = "ru"


class SessionStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
