"""Translation session state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.models.enums import LanguageCode, SessionStatus


class TranslationSession(BaseModel):
    outbound_enabled: bool = False
    inbound_enabled: bool = False
    source_language: LanguageCode = LanguageCode.ZH
    target_language: LanguageCode = LanguageCode.EN
    status: SessionStatus = SessionStatus.IDLE
    started_at: datetime = Field(default_factory=datetime.now)

    def transition(self, status: SessionStatus) -> None:
        self.status = status

