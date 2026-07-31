#!/usr/bin/env python3
"""Build the Windows SoundFerry executable with PyInstaller.

Typical usage:
    python build.py --clean
    python build.py --clean --debug
    python build.py --clean --install-deps
    python build.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build" / "pyinstaller"
SPEC_DIR = BUILD_DIR / "spec"
WORK_DIR = BUILD_DIR / "work"
ENTRYPOINT = ROOT / "run.py"
APP_NAME = "SoundFerry"

DATA_DIRS = ("assets", "config", "hotwords")
COLLECT_ALL_PACKAGES = (
    "espeakng_loader",
    "faster_whisper",
    "kokoro_onnx",
    "sherpa_onnx",
)
COLLECT_BINARY_PACKAGES = ("ctranslate2",)
COPY_METADATA_PACKAGES = ("faster-whisper", "kokoro-onnx")
HIDDEN_IMPORTS = (
    "audioop",
    "dashscope.audio.asr",
    "google.protobuf",
    "keyring.backends.Windows",
    "pyaudio",
    "pycaw.pycaw",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "scipy.signal",
    "sentencepiece",
    "sounddevice",
    "soundfile",
    "tokenizers",
    "transformers.models.nllb.tokenization_nllb",
    "uiautomation",
    "yaml",
)
EXCLUDED_MODULES = ("jax", "matplotlib", "tensorflow", "torch")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(">", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _venv_python() -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return ROOT / ".venv" / scripts / name


def reexec_with_project_venv() -> None:
    """Always build with the project environment when it exists."""
    target = _venv_python()
    if not target.is_file():
        return
    try:
        already_using_venv = Path(sys.executable).resolve() == target.resolve()
    except OSError:
        return
    if already_using_venv or os.environ.get("SOUNDFERRY_BUILD_BOOTSTRAPPED") == "1":
        return
    env = os.environ.copy()
    env["SOUNDFERRY_BUILD_BOOTSTRAPPED"] = "1"
    os.execve(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def install_dependencies() -> None:
    """Install the exact locked runtime and build dependencies."""
    lockfile = ROOT / "uv.lock"
    if not lockfile.is_file():
        raise RuntimeError("Missing uv.lock; run `uv lock` and commit it before building")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required for reproducible builds. Install it from https://docs.astral.sh/uv/"
        )
    run([uv, "sync", "--frozen", "--group", "dev", "--active"])


def validate_environment(*, allow_missing_pyinstaller: bool = False) -> None:
    if os.name != "nt":
        raise RuntimeError("EXE builds must run on Windows")
    if not ENTRYPOINT.is_file():
        raise RuntimeError(f"Missing entry point: {ENTRYPOINT}")
    if not allow_missing_pyinstaller and not module_available("PyInstaller"):
        raise RuntimeError(
            "PyInstaller is not installed. Run build_exe.bat or "
            "python build.py --install-deps --clean"
        )


def clean_outputs() -> None:
    for path in (DIST_DIR, BUILD_DIR):
        if path.exists():
            print(f"Removing {path}")
            shutil.rmtree(path)


def _add_data(command: list[str], source: Path, destination: str) -> None:
    if source.exists():
        command.extend(["--add-data", f"{source}{os.pathsep}{destination}"])


def build_command(*, debug: bool, clean: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        APP_NAME,
        "--onefile",
        "--console" if debug else "--windowed",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--paths",
        str(ROOT),
    ]
    if clean:
        command.append("--clean")

    icon = ROOT / "assets" / "app_icon.ico"
    if icon.is_file():
        command.extend(["--icon", str(icon)])

    for name in DATA_DIRS:
        _add_data(command, ROOT / name, name)

    # Bundle only the small VAD model. ASR/MT model caches can exceed 3 GB and
    # remain persistent outside the one-file extraction directory at runtime.
    vad_model = ROOT / "resource" / "vad" / "silero_vad.onnx"
    _add_data(command, vad_model, "resource/vad")

    command.extend(["--collect-submodules", "src"])
    command.extend(["--collect-submodules", "python_protogen"])
    for package in COLLECT_ALL_PACKAGES:
        if module_available(package):
            command.extend(["--collect-all", package])
    for package in COLLECT_BINARY_PACKAGES:
        if module_available(package):
            command.extend(["--collect-binaries", package])
    for distribution in COPY_METADATA_PACKAGES:
        command.extend(["--copy-metadata", distribution])
    for module in HIDDEN_IMPORTS:
        if module_available(module.split(".", 1)[0]):
            command.extend(["--hidden-import", module])
    for module in EXCLUDED_MODULES:
        command.extend(["--exclude-module", module])

    command.append(str(ENTRYPOINT))
    return command


def write_checksum(executable: Path) -> Path:
    digest = hashlib.sha256()
    with executable.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = executable.with_suffix(executable.suffix + ".sha256")
    checksum.write_text(f"{digest.hexdigest()}  {executable.name}\n", encoding="ascii")
    return checksum


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SoundFerry.exe with PyInstaller")
    parser.add_argument("--clean", action="store_true", help="remove old build outputs first")
    parser.add_argument("--debug", action="store_true", help="keep a console window for logs")
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="sync the exact versions from uv.lock before building",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the PyInstaller command without changing files",
    )
    return parser.parse_args()


def main() -> int:
    reexec_with_project_venv()
    args = parse_args()
    if args.install_deps and not args.dry_run:
        install_dependencies()
    validate_environment(allow_missing_pyinstaller=args.dry_run)

    command = build_command(debug=args.debug, clean=args.clean)
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return 0

    if args.clean:
        clean_outputs()
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    run(command)

    executable = DIST_DIR / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller completed but did not create {executable}")
    checksum = write_checksum(executable)
    size_mb = executable.stat().st_size / (1024 * 1024)
    print(f"\nBuild complete: {executable} ({size_mb:.1f} MB)")
    print(f"SHA-256:       {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
