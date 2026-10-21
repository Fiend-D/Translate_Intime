"""App log retention and log directory anchoring."""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.utils import logger as app_logger
from src.utils.resource_paths import PROJECT_ROOT, resolve_log_dir


def test_old_app_logs_are_removed_on_configure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_logger, "_configured", None)
    old = tmp_path / "app_2020-01-01.log"
    recent = tmp_path / "app_2099-01-01.log"
    subtitles = tmp_path / "subtitles_2020-01-01T00-00-00.txt"
    for path in (old, recent, subtitles):
        path.write_text("x", encoding="utf-8")
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    os.utime(subtitles, (stale, stale))

    app_logger.configure_logging(tmp_path)

    assert not old.exists()
    assert recent.exists()
    assert subtitles.exists()


def test_relative_log_dir_does_not_follow_working_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_log_dir("logs") == PROJECT_ROOT / "logs"
    assert resolve_log_dir(str(tmp_path / "abs")) == tmp_path / "abs"
    assert resolve_log_dir("~/x") == Path.home() / "x"
