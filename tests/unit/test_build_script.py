"""Tests for the one-click PyInstaller build command."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import build


def test_build_command_uses_isolated_paths_and_bundles_vad(monkeypatch) -> None:
    monkeypatch.setattr(build, "module_available", lambda _name: False)

    command = build.build_command(debug=False, clean=True)
    rendered = " ".join(command)

    assert "--onefile" in command
    assert "--windowed" in command
    assert "--clean" in command
    assert str(build.ROOT / "resource" / "vad" / "silero_vad.onnx") in rendered
    assert str(build.SPEC_DIR) in command
    assert str(build.ENTRYPOINT) == command[-1]


def test_debug_build_keeps_console(monkeypatch) -> None:
    monkeypatch.setattr(build, "module_available", lambda _name: False)

    command = build.build_command(debug=True, clean=False)

    assert "--console" in command
    assert "--windowed" not in command
    assert "--clean" not in command


def test_write_checksum(tmp_path) -> None:
    executable = tmp_path / "SoundFerry.exe"
    executable.write_bytes(b"test executable")

    checksum = build.write_checksum(executable)

    expected = hashlib.sha256(b"test executable").hexdigest()
    assert checksum.read_text(encoding="ascii") == f"{expected}  SoundFerry.exe\n"


def test_install_dependencies_uses_frozen_uv_lock(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    lockfile = tmp_path / "uv.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(build, "run", lambda command: commands.append(command))

    build.install_dependencies()

    assert commands == [["uv", "sync", "--frozen", "--group", "dev", "--active"]]


def test_install_dependencies_requires_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build, "ROOT", Path(tmp_path))

    with pytest.raises(RuntimeError, match="uv.lock"):
        build.install_dependencies()
