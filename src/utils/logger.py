"""Loguru setup and subtitle log rotation / archive helpers."""

from __future__ import annotations

import contextlib
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from loguru import logger as logger

from src.models.subtitle import SubtitleEntry

_SUBTITLE_DATE_FORMAT = "%Y-%m-%dT%H-%M-%S"
_MAX_BYTES = 10 * 1024 * 1024
_LINE_PATTERN = re.compile(
    r"^\[(?P<timestamp>.+?)\] (?P<direction>OUTBOUND|INBOUND|TYPED) "
    r"\| ORIGINAL: (?P<original>.+?) \| TRANSLATED: (?P<translated>.+?)$"
)
_DIR_LABELS = {
    "OUTBOUND": "麦克风",
    "INBOUND": "游戏",
    "TYPED": "打字",
}


_APP_LOG_RETENTION_DAYS = 7
_configured: tuple[Path, bool] | None = None


def _remove_old_app_logs(log_dir: Path) -> None:
    # loguru only prunes files at rotation, which a short daily session never reaches.
    cutoff = time.time() - _APP_LOG_RETENTION_DAYS * 86400
    for path in log_dir.glob("app_*.log*"):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()


def configure_logging(log_dir: Path, debug: bool = False) -> None:
    """Configure global logging with rotation and console output (idempotent)."""
    global _configured
    log_dir = log_dir.expanduser().resolve()
    if _configured == (log_dir, debug):
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    _remove_old_app_logs(log_dir)
    level = "DEBUG" if debug else "INFO"

    logger.remove()
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention=f"{_APP_LOG_RETENTION_DAYS} days",
        level=level,
        encoding="utf-8",
    )
    logger.add(lambda msg: print(msg, end=""), level=level, colorize=True)
    _configured = (log_dir, debug)


def _is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    return not t or t == "…"


class SubtitleLogger:
    """Writes finalized subtitle entries to a plain-text log (session + daily archive)."""

    def __init__(
        self,
        log_dir: Path,
        *,
        enabled: bool = True,
        retention_days: int = 30,
    ) -> None:
        self.log_dir = log_dir.expanduser()
        self.enabled = enabled
        self.retention_days = max(1, retention_days)
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self._new_session_path()
        self._bytes_written = 0
        self._session_started = False
        if self.enabled:
            self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        for path in list_subtitle_logs(self.log_dir):
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()

    def _new_session_path(self) -> Path:
        # Local time in filename is easier to browse on disk
        stamp = datetime.now().strftime(_SUBTITLE_DATE_FORMAT)
        return self.log_dir / f"subtitles_{stamp}.txt"

    def _daily_archive_path(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"archive_{day}.txt"

    def begin_session(self) -> Path:
        """Start a fresh per-session log file (call when a channel starts)."""
        self.log_path = self._new_session_path()
        self._bytes_written = 0
        self._session_started = True
        if self.enabled:
            self._write_header(self.log_path)
        return self.log_path

    def _ensure_session(self) -> None:
        if not self._session_started:
            self.begin_session()

    def _write_header(self, path: Path) -> None:
        if path.exists() and path.stat().st_size > 0:
            return
        header = (
            f"# 声渡 SoundFerry 翻译记录\n"
            f"# started: {datetime.now().isoformat(timespec='seconds')}\n"
            f"# format: [ISO8601] DIRECTION | ORIGINAL: … | TRANSLATED: …\n"
        )
        path.write_text(header, encoding="utf-8")
        self._bytes_written = len(header.encode("utf-8"))

    def _rotate_if_needed(self) -> None:
        if self._bytes_written >= _MAX_BYTES:
            stamp = datetime.now().strftime(_SUBTITLE_DATE_FORMAT)
            self.log_path = self.log_dir / f"subtitles_{stamp}.txt"
            self._bytes_written = 0
            self._write_header(self.log_path)

    def _format_line(self, entry: SubtitleEntry) -> str:
        ts = entry.timestamp.astimezone().isoformat(timespec="milliseconds")
        original = entry.original_text.replace("\n", " ")
        translated = entry.translated_text.replace("\n", " ")
        direction = entry.direction.value.upper()
        return f"[{ts}] {direction} | ORIGINAL: {original} | TRANSLATED: {translated}\n"

    def log(self, entry: SubtitleEntry) -> None:
        """Append a subtitle entry to the session file and daily archive."""
        if not self.enabled:
            return
        if not entry.is_final:
            return
        if _is_placeholder(entry.original_text) and _is_placeholder(entry.translated_text):
            return

        self._ensure_session()
        line = self._format_line(entry)
        self._rotate_if_needed()
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        self._bytes_written += len(line.encode("utf-8"))

        # Daily rollup for easy day-based review
        with self._daily_archive_path().open("a", encoding="utf-8") as f:
            f.write(line)

    def log_typed(self, original: str, translated: str) -> None:
        """Record a typed-translate turn into the same archives."""
        if not self.enabled:
            return
        orig = (original or "").strip()
        trans = (translated or "").strip()
        if not orig and not trans:
            return
        self._ensure_session()
        ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
        line = (
            f"[{ts}] TYPED | "
            f"ORIGINAL: {orig.replace(chr(10), ' ')} | "
            f"TRANSLATED: {trans.replace(chr(10), ' ')}\n"
        )
        self._rotate_if_needed()
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        self._bytes_written += len(line.encode("utf-8"))
        with self._daily_archive_path().open("a", encoding="utf-8") as f:
            f.write(line)

    def get_recent_path(self) -> Path:
        """Return the current subtitle log file path."""
        return self.log_path


def log_subtitle(entry: SubtitleEntry, log_dir: Path) -> None:
    """Convenience function to log a single subtitle entry."""
    SubtitleLogger(log_dir).log(entry)


@dataclass(frozen=True)
class TranscriptLine:
    timestamp: datetime
    direction: str
    original: str
    translated: str
    source_file: str = ""

    @property
    def direction_label(self) -> str:
        return _DIR_LABELS.get(self.direction, self.direction)


def parse_subtitle_line(line: str, *, source_file: str = "") -> TranscriptLine | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    m = _LINE_PATTERN.match(raw)
    if not m:
        return None
    try:
        ts = datetime.fromisoformat(m.group("timestamp"))
    except ValueError:
        ts = datetime.now(UTC)
    return TranscriptLine(
        timestamp=ts,
        direction=m.group("direction"),
        original=m.group("original"),
        translated=m.group("translated"),
        source_file=source_file,
    )


def list_subtitle_logs(log_dir: Path) -> list[Path]:
    """Newest-first list of subtitle session + daily archive files."""
    root = log_dir.expanduser()
    if not root.is_dir():
        return []
    files = [
        p
        for p in root.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".txt"
        and (p.name.startswith("subtitles_") or p.name.startswith("archive_"))
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def read_transcript_file(path: Path, *, limit: int | None = None) -> list[TranscriptLine]:
    """Parse a subtitle log file into structured lines (oldest → newest)."""
    if not path.is_file():
        return []
    maxlen = limit if limit is not None and limit > 0 else None
    rows: deque[TranscriptLine] = deque(maxlen=maxlen)
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parsed = parse_subtitle_line(line.rstrip("\r\n"), source_file=path.name)
            if parsed is not None:
                rows.append(parsed)
    return list(rows)


SubtitleText = Literal["both", "original", "translated"]

_CUE_MIN_SEC = 1.2
_CUE_MAX_SEC = 7.0


def _cue_text(row: TranscriptLine, text: SubtitleText) -> str:
    original, translated = row.original.strip(), row.translated.strip()
    if text == "original":
        return original
    if text == "translated":
        return translated or original
    return "\n".join(part for part in (original, translated) if part)


def _reading_seconds(content: str) -> float:
    cjk = sum(1 for ch in content if "\u3040" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    words = len(content.split()) if cjk == 0 else 0
    estimate = cjk / 6.0 + words / 2.5
    return min(_CUE_MAX_SEC, max(_CUE_MIN_SEC, estimate))


def _subtitle_cues(
    rows: list[TranscriptLine], text: SubtitleText
) -> list[tuple[float, float, str]]:
    """Relative (start, end, text) cues; each lasts until the next one or its reading time."""
    entries = [(row.timestamp.timestamp(), _cue_text(row, text)) for row in rows]
    entries = [(ts, body) for ts, body in entries if body]
    if not entries:
        return []
    origin = entries[0][0]
    cues: list[tuple[float, float, str]] = []
    for index, (ts, body) in enumerate(entries):
        start = max(0.0, ts - origin)
        if cues:
            start = max(start, cues[-1][1])
        end = start + _reading_seconds(body)
        if index + 1 < len(entries):
            end = min(end, max(start + 0.5, entries[index + 1][0] - origin))
        cues.append((start, end, body))
    return cues


def _clock(seconds: float, sep: str) -> str:
    ms_total = int(round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def format_transcript_srt(rows: list[TranscriptLine], *, text: SubtitleText = "both") -> str:
    blocks = [
        f"{i}\n{_clock(start, ',')} --> {_clock(end, ',')}\n{body}\n"
        for i, (start, end, body) in enumerate(_subtitle_cues(rows, text), start=1)
    ]
    return "\n".join(blocks)


def format_transcript_vtt(rows: list[TranscriptLine], *, text: SubtitleText = "both") -> str:
    blocks = [
        f"{_clock(start, '.')} --> {_clock(end, '.')}\n{body}\n"
        for start, end, body in _subtitle_cues(rows, text)
    ]
    return "WEBVTT\n\n" + "\n".join(blocks)


def format_transcript_text(rows: list[TranscriptLine]) -> str:
    lines = []
    for row in rows:
        ts = row.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{ts}] {row.direction_label}  {row.original}")
        if row.translated:
            lines.append(f"    → {row.translated}")
    return "\n".join(lines) + ("\n" if lines else "")


def format_transcript(
    rows: list[TranscriptLine], suffix: str, *, text: SubtitleText = "both"
) -> str:
    """Pick the export format from a file suffix (.srt / .vtt / .txt / .md)."""
    kind = suffix.lower().lstrip(".")
    if kind == "srt":
        return format_transcript_srt(rows, text=text)
    if kind == "vtt":
        return format_transcript_vtt(rows, text=text)
    if kind == "txt":
        return format_transcript_text(rows)
    return format_transcript_markdown(rows)


def format_transcript_markdown(rows: list[TranscriptLine]) -> str:
    """Human-readable export for copy/paste."""
    lines = ["# 翻译记录", ""]
    for row in rows:
        ts = row.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"## [{ts}] {row.direction_label}")
        lines.append(f"- 原文：{row.original}")
        lines.append(f"- 译文：{row.translated}")
        lines.append("")
    return "\n".join(lines)
