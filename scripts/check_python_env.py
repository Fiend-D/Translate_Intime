#!/usr/bin/env python3
"""检查当前 Python 环境和关键依赖是否一致。"""
from __future__ import annotations

import importlib
import site
import subprocess
import sys


def main() -> None:
    print("python:", sys.executable)
    print("version:", sys.version.replace("\n", " "))
    print("prefix:", sys.prefix)
    print("base_prefix:", sys.base_prefix)
    print("site-packages:")
    for path in site.getsitepackages():
        print("  ", path)

    print("\npip:")
    subprocess.run([sys.executable, "-m", "pip", "-V"], check=False)

    for name in ("funasr", "faster_whisper", "aiohttp", "soundfile"):
        print(f"\nimport {name}:")
        try:
            module = importlib.import_module(name)
            version = getattr(module, "__version__", "unknown")
            print("  ok", version, getattr(module, "__file__", ""))
        except Exception as exc:
            print("  failed:", repr(exc))


if __name__ == "__main__":
    main()
