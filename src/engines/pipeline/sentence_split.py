"""Sentence segmentation helpers for economy-mode MT/TTS."""

from __future__ import annotations

import re

_TERMINAL_PUNCT = frozenset("。！？!?.;")
_CLOSING_PUNCT = frozenset("\"'”’）)]}》】」』")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_PUNCT_ONLY_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)
_NON_BREAKING_ABBREVIATION_RE = re.compile(
    r"(?<![A-Za-z])(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|e\.g|i\.e)\.$",
    re.IGNORECASE,
)
_INITIAL_RE = re.compile(r"(?:^|\s)[A-Za-z]\.$")
_ACRONYM_RE = re.compile(r"(?:[A-Za-z]\.){2,}$")


def _cjk_majority(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha() or _CJK_RE.match(ch)]
    if not letters:
        return bool(_CJK_RE.search(text))
    cjk = sum(1 for ch in letters if _CJK_RE.match(ch))
    return cjk >= max(1, (len(letters) + 1) // 2)


def is_cjk_majority(text: str) -> bool:
    """Public helper: True when text is mostly CJK characters."""
    return _cjk_majority(text or "")


def is_punct_only(text: str) -> bool:
    """True for empty / whitespace / punctuation-only strings."""
    s = (text or "").strip()
    return (not s) or bool(_PUNCT_ONLY_RE.match(s))


def _effective_len(text: str) -> int:
    """Length for merge decisions: each CJK glyph counts as 2."""
    return sum(2 if _CJK_RE.match(ch) else 1 for ch in text)


def ensure_terminal_punct(s: str) -> str:
    """Append sentence-final punctuation when missing (helps TTS prosody)."""
    text = (s or "").strip()
    if not text:
        return text
    if text[-1] in _TERMINAL_PUNCT:
        return text
    if _cjk_majority(text):
        return text + "。"
    return text + "."


def _period_is_boundary(text: str, index: int) -> bool:
    """Reject period boundaries inside numbers, abbreviations, and initials."""
    previous = text[index - 1] if index > 0 else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous.isdigit() and following.isdigit():
        return False
    if following and following.isalnum():
        return False

    prefix = text[: index + 1]
    if _NON_BREAKING_ABBREVIATION_RE.search(prefix) or _ACRONYM_RE.search(prefix):
        return False

    next_non_space = text[index + 1 :].lstrip()[:1]
    return not (
        next_non_space and _INITIAL_RE.search(prefix) and next_non_space.isupper()
    )


def _raw_sentences(text: str) -> list[str]:
    """Scan sentence boundaries while retaining punctuation with each sentence."""
    parts: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        boundary = char == "\n" or char in _TERMINAL_PUNCT
        if char == "." and boundary:
            boundary = _period_is_boundary(text, index)
        if char in "，," and index + 1 < len(text) and text[index + 1] == char:
            boundary = True
            index += 1
        if not boundary:
            index += 1
            continue

        end = index + 1
        while end < len(text) and (
            text[end] in _TERMINAL_PUNCT or text[end] in _CLOSING_PUNCT
        ):
            end += 1
        part = text[start:end].strip()
        if part:
            parts.append(part)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        index = start

    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def split_sentences(text: str, *, min_chars: int = 4) -> list[str]:
    """Split zh/en text on 。！？!?.;\\n and long ，/, runs.

    Merge tiny fragments; keep order; strip empties.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    parts = _raw_sentences(raw)
    if not parts:
        return [raw]

    min_n = max(1, int(min_chars))
    merged: list[str] = []
    for part in parts:
        if not merged:
            merged.append(part)
            continue
        previous_is_complete = merged[-1][-1:] in _TERMINAL_PUNCT
        part_is_complete = part[-1:] in _TERMINAL_PUNCT
        if (
            (_effective_len(part) < min_n and not part_is_complete)
            or (_effective_len(merged[-1]) < min_n and not previous_is_complete)
        ):
            merged[-1] = f"{merged[-1]}{part}".strip()
        else:
            merged.append(part)
    return [m for m in merged if m]
