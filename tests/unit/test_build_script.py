"""Tests for the one-click PyInstaller build command."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import build


def test_build_command_uses_isolated_paths_and_bundles_vad(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build, "module_available", lambda _name: False)
    vad_model = tmp_path / "resource" / "vad" / "silero_vad.onnx"
    vad_model.parent.mkdir(parents=True)
    vad_model.write_bytes(b"onnx")
    monkeypatch.setattr(build, "ROOT", tmp_path)

    command = build.build_command(debug=False, clean=True)
    rendered = " ".join(command)

    assert "--onefile" in command
    assert "--windowed" in command
    assert "--clean" in command
    assert str(vad_model) in rendered
    assert str(build.SPEC_DIR) in command
    assert str(build.ENTRYPOINT) == command[-1]


def test_build_command_skips_missing_vad_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build, "module_available", lambda _name: False)
    monkeypatch.setattr(build, "ROOT", tmp_path)

    command = build.build_command(debug=False, clean=False)

    assert "silero_vad.onnx" not in " ".join(command)


def test_metadata_is_copied_only_for_installed_packages(monkeypatch) -> None:
    monkeypatch.setattr(build, "module_available", lambda _name: False)
    monkeypatch.setattr(build, "distribution_available", lambda name: name == "kokoro-onnx")

    command = build.build_command(debug=False, clean=False)

    copied = [command[i + 1] for i, arg in enumerate(command) if arg == "--copy-metadata"]
    assert copied == ["kokoro-onnx"]


def test_windows_bootstrap_propagates_build_exit_code(monkeypatch, tmp_path) -> None:
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(build, "_venv_python", lambda: python)
    monkeypatch.setattr(build, "_is_windows", lambda: True)
    monkeypatch.delenv("SOUNDFERRY_BUILD_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(
        build.subprocess, "run", lambda *_a, **_k: build.subprocess.CompletedProcess([], 3)
    )

    with pytest.raises(SystemExit) as exc:
        build.reexec_with_project_venv()
    assert exc.value.code == 3


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


def test_install_dependencies_requires_up_to_date_uv_lock(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    lockfile = tmp_path / "uv.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(build, "run", lambda command: commands.append(command))

    build.install_dependencies()

    assert commands == [["uv", "sync", "--locked", "--group", "dev", "--active"]]


def test_install_dependencies_requires_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build, "ROOT", Path(tmp_path))

    with pytest.raises(RuntimeError, match="uv.lock"):
        build.install_dependencies()


@pytest.mark.parametrize(
    ("windows", "name", "present", "absent"),
    [
        (True, "SoundFerry.exe", "pynput.keyboard._win32", "pynput.keyboard._xorg"),
        (False, "SoundFerry", "pynput.keyboard._xorg", "pynput.keyboard._win32"),
    ],
)
def test_platform_specific_output_and_hidden_imports(
    monkeypatch, windows: bool, name: str, present: str, absent: str
) -> None:
    monkeypatch.setattr(build, "_is_windows", lambda: windows)
    monkeypatch.setattr(build, "module_available", lambda _name: True)
    monkeypatch.setattr(build, "distribution_available", lambda _name: False)

    command = build.build_command(debug=False, clean=False)
    hidden = [command[i + 1] for i, arg in enumerate(command) if arg == "--hidden-import"]

    assert build.executable_path().name == name
    assert present in hidden and absent not in hidden
    assert ("--icon" in command) is (windows and (build.ROOT / "assets/app_icon.ico").is_file())


def test_windows_build_bundles_vc_runtime_next_to_the_exe(monkeypatch, tmp_path) -> None:
    dll = tmp_path / "msvcp140.dll"
    dll.write_bytes(b"dll")
    monkeypatch.setattr(build, "_is_windows", lambda: True)
    monkeypatch.setattr(build, "module_available", lambda _name: False)
    monkeypatch.setattr(build, "msvc_runtime_dlls", lambda: [dll])

    command = build.build_command(debug=False, clean=False)

    assert f"{dll}{build.os.pathsep}." in command
