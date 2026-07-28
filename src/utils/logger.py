"""Loguru setup and subtitle log rotation / archive helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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


def configure_logging(log_dir: Path, debug: bool = False) -> None:
    """Configure global logging with rotation and console output."""
    log_dir = log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    level = "DEBUG" if debug else "INFO"

    logger.remove()
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="7 days",
        level=level,
        encoding="utf-8",
    )
    logger.add(lambda msg: print(msg, end=""), level=level, colorize=True)


def _is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    return not t or t == "…"


class SubtitleLogger:
    """Writes finalized subtitle entries to a plain-text log (session + daily archive)."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir.expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self._new_session_path()
        self._bytes_written = 0
        self._session_started = False

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
        self._write_header(self.log_path)
        return self.log_path

    def _ensure_session(self) -> None:
        if not self._session_started:
            self.begin_session()

    def _write_header(self, path: Path) -> None:
        if path.exists() and path.stat().st_size > 0:
            return
        header = (
            f"# Translator InTime 翻译记录\n"
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
        return (
            f"[{ts}] {direction} | "
            f"ORIGINAL: {original} | TRANSLATED: {translated}\n"
        )

    def log(self, entry: SubtitleEntry) -> None:
        """Append a subtitle entry to the session file and daily archive."""
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
    rows: list[TranscriptLine] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        parsed = parse_subtitle_line(line, source_file=path.name)
        if parsed is not None:
            rows.append(parsed)
    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


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
