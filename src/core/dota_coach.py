"""Bridge: send finalized mic ASR text to Dota 2 Tracker /ai/ask."""

from __future__ import annotations

import threading
from typing import Any

import httpx
from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_COACH_URL = "http://127.0.0.1:3001/ai/ask"


class DotaCoachBridge(QObject):
    """Non-blocking HTTP client for the local Dota tracker coach API."""

    # ok, question, answer_or_error
    finished = pyqtSignal(bool, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def ask(
        self,
        text: str,
        *,
        url: str = DEFAULT_COACH_URL,
        mode: str = "normal",
        source: str = "voice",
    ) -> bool:
        """Start a background ask. Returns False if already busy / empty text."""
        question = (text or "").strip()
        if not question or question == "…":
            return False
        if self._busy:
            return False
        self._busy = True
        endpoint = (url or DEFAULT_COACH_URL).strip() or DEFAULT_COACH_URL
        threading.Thread(
            target=self._run,
            args=(question, endpoint, mode, source),
            daemon=True,
            name="dota-coach-ask",
        ).start()
        return True

    def _run(self, question: str, url: str, mode: str, source: str) -> None:
        try:
            payload: dict[str, Any] = {
                "text": question,
                "question": question,
                "mode": "turbo" if mode == "turbo" else "normal",
                "source": source,
            }
            with httpx.Client(timeout=httpx.Timeout(45.0, connect=3.0)) as client:
                resp = client.post(url, json=payload)
            try:
                data = resp.json() if resp.content else {}
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            if resp.is_success:
                answer = str(data.get("text") or "").strip() or "—"
                self.finished.emit(True, question, answer)
            else:
                err = str(data.get("error") or resp.text or f"HTTP {resp.status_code}")
                self.finished.emit(False, question, err.strip() or "请求失败")
        except Exception as exc:  # noqa: BLE001 — show any transport error in UI
            self.finished.emit(False, question, str(exc))
        finally:
            self._busy = False
