"""Subtitle export formats built from archived transcript lines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.utils.logger import (
    TranscriptLine,
    format_transcript,
    format_transcript_srt,
    format_transcript_vtt,
    parse_subtitle_line,
)

T0 = datetime(2026, 10, 20, 8, 0, 0, tzinfo=UTC)


def _row(offset: float, original: str, translated: str) -> TranscriptLine:
    return TranscriptLine(T0 + timedelta(seconds=offset), "OUTBOUND", original, translated)


def test_srt_cues_are_numbered_bilingual_and_do_not_overlap() -> None:
    rows = [_row(0, "你好世界", "Hello world"), _row(1.0, "第二句", "Second line")]
    srt = format_transcript_srt(rows)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,000\n你好世界\nHello world\n")
    assert "2\n00:00:01,000 --> 00:00:02,200\n第二句\nSecond line\n" in srt


def test_vtt_header_and_translated_only_text() -> None:
    rows = [_row(0, "原文", "Translated"), _row(10, "只有原文", "")]
    vtt = format_transcript_vtt(rows, text="translated")
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> ")
    assert "Translated" in vtt and "原文\nTranslated" not in vtt
    assert "只有原文" in vtt


def test_long_gap_cue_ends_after_reading_time() -> None:
    rows = [_row(0, "short", "短"), _row(60, "next", "下一句")]
    srt = format_transcript_srt(rows, text="original")
    assert "00:00:00,000 --> 00:00:01,200\nshort" in srt
    assert "00:01:00,000 -->" in srt


def test_format_by_suffix_round_trips_archive_line() -> None:
    line = "[2026-10-20T16:00:00.000+08:00] INBOUND | ORIGINAL: gg wp | TRANSLATED: 打得好"
    row = parse_subtitle_line(line)
    assert row is not None
    assert format_transcript([row], ".SRT").startswith("1\n")
    assert format_transcript([row], ".vtt").startswith("WEBVTT")
    assert "gg wp" in format_transcript([row], ".txt")
    assert format_transcript([row], ".md").startswith("# 翻译记录")
