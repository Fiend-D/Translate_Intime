"""Application enum types."""

from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class LanguageCode(str, Enum):
    ZH = "zh"
    EN = "en"
    JA = "ja"
    KO = "ko"
    FR = "fr"
    DE = "de"
    ES = "es"
    RU = "ru"


class SessionStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"

