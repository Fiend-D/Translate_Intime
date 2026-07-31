"""Small helpers for validating downloaded binary assets."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches_sha256(path: Path, expected: str) -> bool:
    return path.is_file() and sha256_file(path).casefold() == expected.casefold()
