"""Privacy and retention behavior for local translation archives."""

from __future__ import annotations

import os
import time

from src.models.enums import Direction
from src.models.subtitle import SubtitleEntry
from src.utils.logger import SubtitleLogger


def _entry() -> SubtitleEntry:
    return SubtitleEntry(
        direction=Direction.OUTBOUND,
        original_text="secret original",
        translated_text="secret translation",
    )


def test_disabled_logger_does_not_create_plaintext_archive(tmp_path) -> None:
    logger = SubtitleLogger(tmp_path / "logs", enabled=False)

    logger.log(_entry())
    logger.log_typed("typed secret", "translated secret")

    assert not (tmp_path / "logs").exists()


def test_logger_removes_archives_older_than_retention(tmp_path) -> None:
    old = tmp_path / "archive_2020-01-01.txt"
    recent = tmp_path / "archive_2099-01-01.txt"
    old.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    expired = time.time() - 3 * 86400
    os.utime(old, (expired, expired))

    SubtitleLogger(tmp_path, enabled=True, retention_days=1)

    assert not old.exists()
    assert recent.exists()
