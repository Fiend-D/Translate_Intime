"""Resolve bundled resources and persistent local-model cache paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_user_model_dir: Path | None = None


def bundled_path(*parts: str) -> Path:
    """Return a read-only bundled path in PyInstaller, or a project path in development."""
    bundle_root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return bundle_root.joinpath(*parts)


def set_model_dir(value: str | Path | None) -> None:
    """Use ``value`` (the ``model_dir`` setting) as the model root; empty restores the default."""
    global _user_model_dir
    text = str(value or "").strip()
    _user_model_dir = Path(text).expanduser().resolve() if text else None


def model_resource_root() -> Path:
    """Return the persistent resource root used for downloaded local models."""
    override = (os.environ.get("SOUNDFERRY_RESOURCE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _user_model_dir is not None:
        return _user_model_dir
    return default_model_resource_root()


def default_model_resource_root() -> Path:
    """The model root used when no ``model_dir`` is configured."""
    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT / "resource"

    # A resource directory next to the EXE enables a portable offline bundle.
    portable = Path(sys.executable).resolve().parent / "resource"
    if portable.is_dir():
        return portable

    return _user_data_root() / "resource"


def _user_data_root() -> Path:
    local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / "SoundFerry"


def resolve_log_dir(value: str | Path) -> Path:
    """Anchor a relative ``log_dir`` setting so logs don't follow the launch directory."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    root = _user_data_root() if getattr(sys, "frozen", False) else PROJECT_ROOT
    return root / path
