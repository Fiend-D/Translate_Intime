"""Load hotword / glossary files from the repo `hotwords/` directory."""

from __future__ import annotations

from pathlib import Path

_REPO_HOTWORDS = Path(__file__).resolve().parents[2] / "hotwords"


def hotwords_dir() -> Path:
    return _REPO_HOTWORDS


def list_hotword_files() -> list[Path]:
    root = hotwords_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.txt") if p.is_file())


def parse_hotword_file(path: Path | str) -> tuple[list[str], dict[str, str]]:
    """Parse `源=译` lines. Returns (hotwords, glossary).

    Hotwords are source-side terms (keys); glossary maps source → target.
    Lines starting with # and blank lines are ignored.
    """
    hotwords: list[str] = []
    glossary: dict[str, str] = {}
    seen: set[str] = set()
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            src, tgt = line.split("=", 1)
            src, tgt = src.strip(), tgt.strip()
            if not src:
                continue
            glossary[src] = tgt or src
            if src not in seen:
                seen.add(src)
                hotwords.append(src)
        else:
            if line not in seen:
                seen.add(line)
                hotwords.append(line)
    return hotwords, glossary


def merge_corpus(
    *,
    hotwords: list[str] | None = None,
    glossary: dict[str, str] | None = None,
    extra_files: list[Path | str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    words = list(hotwords or [])
    gloss = dict(glossary or {})
    seen = set(words)
    for path in extra_files or []:
        hw, gl = parse_hotword_file(path)
        for w in hw:
            if w not in seen:
                seen.add(w)
                words.append(w)
        gloss.update(gl)
    return words, gloss
