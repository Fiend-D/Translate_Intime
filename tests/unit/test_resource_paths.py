"""Resource path behavior for source and PyInstaller builds."""

from __future__ import annotations

import sys

from src.utils import resource_paths


def test_development_models_stay_in_project(monkeypatch) -> None:
    monkeypatch.delenv("SOUNDFERRY_RESOURCE_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert resource_paths.model_resource_root() == resource_paths.PROJECT_ROOT / "resource"


def test_frozen_models_use_local_app_data(tmp_path, monkeypatch) -> None:
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.delenv("SOUNDFERRY_RESOURCE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "SoundFerry.exe"))

    assert resource_paths.model_resource_root() == tmp_path / "local" / "SoundFerry" / "resource"


def test_frozen_portable_resource_directory_takes_precedence(tmp_path, monkeypatch) -> None:
    exe_dir = tmp_path / "portable"
    resource_dir = exe_dir / "resource"
    resource_dir.mkdir(parents=True)
    monkeypatch.delenv("SOUNDFERRY_RESOURCE_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "SoundFerry.exe"))

    assert resource_paths.model_resource_root() == resource_dir


def test_bundled_path_uses_meipass(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resource_paths.bundled_path("assets", "app_icon.ico") == (
        tmp_path / "assets" / "app_icon.ico"
    )
