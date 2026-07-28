# Contract: Subtitle Log Format

## Purpose

Defines the plain-text format for per-session subtitle logs so that users can review or archive conversations, and so that automated tests can parse expected output.

## File Location

`~/.config/translator_intime/logs/subtitles_YYYY-MM-DD_HH-MM-SS.txt`

One file per `TranslationSession` (created when the session enters `RUNNING`).

## Format Specification

Each line represents one `SubtitleEntry` and follows this exact structure:

```
[<ISO8601_TIMESTAMP>] <DIRECTION> | ORIGINAL: <original_text> | TRANSLATED: <translated_text>
```

### Fields

| Field | Format | Example |
|-------|--------|---------|
| `ISO8601_TIMESTAMP` | `YYYY-MM-DDTHH:MM:SS.sss+ZZ:ZZ` | `2026-06-18T14:32:10.123+08:00` |
| `DIRECTION` | `OUTBOUND` or `INBOUND` | `OUTBOUND` |
| `original_text` | Plain text, no newlines | `你好，能听到吗？` |
| `translated_text` | Plain text, no newlines | `Hello, can you hear me?` |

### Escaping Rules

- Newline characters (`\n`) in text are replaced with a single space.
- Pipe characters (`|`) in text are preserved as-is because the field is delimited by ` | ` (space-pipe-space), which is unlikely in natural speech.

## Example Log File

```text
[2026-06-18T14:32:10.123+08:00] OUTBOUND | ORIGINAL: 你好，能听到吗？ | TRANSLATED: Hello, can you hear me?
[2026-06-18T14:32:12.456+08:00] INBOUND | ORIGINAL: Yes, I can hear you loud and clear. | TRANSLATED: 是的，我能听得很清楚。
[2026-06-18T14:32:15.789+08:00] OUTBOUND | ORIGINAL: 太好了，我们开始吧。 | TRANSLATED: Great, let's get started.
```

## Rotation Policy

- A new log file is created at the start of every session.
- Old log files are NOT automatically deleted; the user is responsible for cleanup.
- A soft limit of 10 MB per file is recommended; if exceeded, the app starts a new file with a sequential suffix (`_001`, `_002`).

## Parsing Contract (for tests)

```python
import re
from datetime import datetime

LINE_PATTERN = re.compile(
    r"^\[(?P<timestamp>.+?)\] (?P<direction>OUTBOUND|INBOUND) "
    r"\| ORIGINAL: (?P<original>.+?) \| TRANSLATED: (?P<translated>.+?)$"
)

def parse_line(line: str) -> dict:
    m = LINE_PATTERN.match(line.strip())
    if not m:
        raise ValueError(f"Invalid log line: {line}")
    return {
        "timestamp": datetime.fromisoformat(m.group("timestamp")),
        "direction": m.group("direction"),
        "original": m.group("original"),
        "translated": m.group("translated"),
    }
```
