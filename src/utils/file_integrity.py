"""Small helpers for validating downloaded binary assets."""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Collection
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _stamp(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def cached_sha256(path: Path) -> str:
    """SHA-256 of ``path``; reuses the sidecar digest while size and mtime are unchanged."""
    sidecar = _sidecar(path)
    stamp = _stamp(path)
    with contextlib.suppress(OSError, ValueError):
        cached_stamp, digest = sidecar.read_text(encoding="ascii").split()
        if cached_stamp == stamp:
            return digest
    digest = sha256_file(path)
    with contextlib.suppress(OSError):
        sidecar.write_text(f"{stamp} {digest}\n", encoding="ascii")
    return digest


def remember_sha256(path: Path, digest: str) -> None:
    """Record an already-verified digest so the next check skips re-hashing ``path``."""
    with contextlib.suppress(OSError):
        _sidecar(path).write_text(f"{_stamp(path)} {digest}\n", encoding="ascii")


def matches_sha256(path: Path, expected: str | Collection[str], *, cache: bool = True) -> bool:
    if not path.is_file():
        return False
    allowed = {expected} if isinstance(expected, str) else set(expected)
    digest = (cached_sha256(path) if cache else sha256_file(path)).casefold()
    return any(digest == value.casefold() for value in allowed)
