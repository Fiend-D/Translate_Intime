"""Usage tracking for Volc AST API consumption."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


@dataclass
class UsageState:
    session_tokens: int = 0
    session_cost: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_seconds: float = 0.0
    total_chars: int = 0
    today_seconds: float = 0.0
    today_chars: int = 0
    pack_remaining: str = ""
    last_update: datetime = field(default_factory=lambda: datetime.now(UTC))


class UsageTracker:
    def __init__(
        self,
        data_path: Path,
        *,
        on_update: Callable[[UsageState], None] | None = None,
    ) -> None:
        self._path = data_path
        self._on_update = on_update
        self._state = self._load()

    @property
    def state(self) -> UsageState:
        return self._state

    def _load(self) -> UsageState:
        if not self._path.exists():
            return UsageState()
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return UsageState(
                total_seconds=float(data.get("total_seconds", 0)),
                total_chars=int(data.get("total_chars", 0)),
                today_seconds=float(data.get("today_seconds", 0)),
                today_chars=int(data.get("today_chars", 0)),
                pack_remaining=str(data.get("pack_remaining", "")),
                total_tokens=int(data.get("total_tokens", 0)),
                total_cost=float(data.get("total_cost", 0)),
                session_tokens=int(data.get("session_tokens", 0)),
                session_cost=float(data.get("session_cost", 0)),
            )
        except Exception:
            return UsageState()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "total_seconds": self._state.total_seconds,
                "total_chars": self._state.total_chars,
                "today_seconds": self._state.today_seconds,
                "today_chars": self._state.today_chars,
                "pack_remaining": self._state.pack_remaining,
                "total_tokens": self._state.total_tokens,
                "total_cost": self._state.total_cost,
            }
            with self._path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def feed_usage_dict(self, source: str, payload: dict) -> None:
        billing = (payload.get("response_meta") or {}).get("Billing") or {}
        items = billing.get("Items") or []
        for item in items:
            unit = item.get("Unit")
            quantity = item.get("Quantity")
            cost = item.get("Cost")
            if unit == "Second":
                seconds = float(quantity) if quantity else 0
                self._state.total_seconds += seconds
                self._state.today_seconds += seconds
            elif unit == "Character":
                chars = int(quantity) if quantity else 0
                self._state.total_chars += chars
                self._state.today_chars += chars
            elif unit == "Token":
                tokens = int(quantity) if quantity else 0
                self._state.session_tokens += tokens
                self._state.total_tokens += tokens
            if cost:
                self._state.session_cost += float(cost)
                self._state.total_cost += float(cost)
        self._state.last_update = datetime.now(UTC)
        self._save()
        if self._on_update:
            self._on_update(self._state)

    def update_from_usage_response(self, payload: dict) -> None:
        self.feed_usage_dict("", payload)

    def reset_session(self) -> None:
        """重置当前会话的用量统计（不影响累计）。"""
        self._state.session_tokens = 0
        self._state.session_cost = 0.0
        self._state.last_update = datetime.now(UTC)
        self._save()
        if self._on_update:
            self._on_update(self._state)

    def update_pack_remaining(self, text: str) -> None:
        self._state.pack_remaining = text
        self._save()
        if self._on_update:
            self._on_update(self._state)
