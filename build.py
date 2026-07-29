#!/usr/bin/env python3
"""PyInstaller 打包脚本 - 将 声渡 SoundFerry 打包为单文件 EXE。

用法:
    python build.py            # 打包到 dist/
    python build.py --clean    # 清理后重新打包
    python build.py --debug    # 打包调试版（含控制台窗口）

前置条件:
    pip install pyinstaller
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "SoundFerry.spec"


def run(cmd: list[str]) -> None:
    print(f"> {' '.join(cmd)}")
    subprocess.check_call(cmd)


def clean() -> None:
    for d in (DIST, BUILD):
        if d.exists():
            print(f"清理 {d}")
            shutil.rmtree(d)
    if SPEC.exists():
        SPEC.unlink()


def build_exe(debug: bool = False) -> None:
    hidden_imports = [
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "pyaudio",
        "sounddevice",
        "soundfile",
        "numpy",
        "scipy",
        "scipy.signal",
        "pydantic",
        "loguru",
        "httpx",
        "aiohttp",
        "protobuf",
        "keyring",
        "pynput",
        "pynput.keyboard",
        "pynput.keyboard._win32",
        "pynput.mouse",
        "pynput.mouse._win32",
        "yaml",
        "soundcard",
        "dotenv",
        "websockets",
        "edge_tts",
        "comtypes",
        "pycaw",
        "pycaw.pycaw",
        "audioop",
        # 项目内部模块
        "src.core.speech_gate",
        "src.core.usage_tracker",
        "src.core.audio_capture",
        "src.core.audio_player",
        "src.core.exceptions",
        "src.core.pipeline",
        "src.core.volc_engine",
        "src.core.dota_coach",
        "src.core.music_share",
        "src.core.typed_translate",
        "src.audio.stream",
        "src.audio.device_guard",
        "src.audio.session_ducker",
        "src.audio.wasapi_process_loopback",
        "src.audio.virtual_device",
        "src.gui.main_window",
        "src.gui.subtitle_overlay",
        "src.gui.subtitle_buffer",
        "src.gui.styles",
        "src.gui.hotkey_dialog",
        "src.gui.hotkey_edit",
        "src.gui.device_labels",
        "src.gui.corpus_dialog",
        "src.gui.music_sidebar",
        "src.gui.toast",
        "src.gui.typed_dialog",
        "src.utils.logger",
        "src.utils.config_manager",
        "src.utils.hotkeys",
        "src.utils.audio_utils",
        "src.utils.hotword_files",
        # protobuf 生成代码
        "python_protogen",
        "python_protogen.common.events_pb2",
        "python_protogen.common.events_pb2_grpc",
        "python_protogen.common.rpcmeta_pb2",
        "python_protogen.common.rpcmeta_pb2_grpc",
        "python_protogen.products.understanding.ast.ast_service_pb2",
        "python_protogen.products.understanding.ast.ast_service_pb2_grpc",
        "python_protogen.products.understanding.base.au_base_pb2",
        "python_protogen.products.understanding.base.au_base_pb2_grpc",
    ]

    datas = [
        # (源路径, 目标目录)
        ("python_protogen", "python_protogen"),
        ("config", "config"),
        ("assets", "assets"),
        ("hotwords", "hotwords"),
    ]

    # 检查 specs 目录是否存在
    specs_dir = ROOT / "specs"
    if specs_dir.exists():
        datas.append(("specs", "specs"))

    # EXE 文件图标（资源管理器显示）
    icon_path = ROOT / "assets" / "app_icon.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "SoundFerry",
        # 单文件模式
        "--onefile",
        # 入口
        str(ROOT / "run.py"),
    ]

    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])

    if not debug:
        cmd.append("--windowed")  # 无控制台窗口
    else:
        cmd.append("--console")   # 保留控制台用于调试

    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])

    for src, dst in datas:
        src_path = ROOT / src
        if src_path.exists():
            cmd.extend(["--add-data", f"{src};{dst}"])

    run(cmd)
    print(f"\n打包完成: {DIST / 'SoundFerry.exe'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 声渡 SoundFerry 为 EXE")
    parser.add_argument("--clean", action="store_true", help="清理旧构建后重新打包")
    parser.add_argument("--debug", action="store_true", help="打包调试版（保留控制台窗口）")
    args = parser.parse_args()

    if args.clean:
        clean()

    # 确保 PyInstaller 已安装
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装，正在安装...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    build_exe(debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
