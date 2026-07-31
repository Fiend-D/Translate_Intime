"""Resolve bundled resources and persistent local-model cache paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def bundled_path(*parts: str) -> Path:
    """Return a read-only bundled path in PyInstaller, or a project path in development."""
    bundle_root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return bundle_root.joinpath(*parts)


def model_resource_root() -> Path:
    """Return the persistent resource root used for downloaded local models."""
    override = (os.environ.get("SOUNDFERRY_RESOURCE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT / "resource"

    # A resource directory next to the EXE enables a portable offline bundle.
    portable = Path(sys.executable).resolve().parent / "resource"
    if portable.is_dir():
        return portable

    local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / "SoundFerry" / "resource"
