#!/usr/bin/env python3
"""便捷启动脚本：优先使用项目 .venv 解释器。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENV_PY = _ROOT / ".venv" / "bin" / "python"
if os.name == "nt":
    _VENV_PY = _ROOT / ".venv" / "Scripts" / "python.exe"


def _reexec_with_venv() -> None:
    """If .venv exists and we are not already in it, re-launch with that Python."""
    if not _VENV_PY.is_file():
        return
    try:
        current = Path(sys.executable).resolve()
        target = _VENV_PY.resolve()
    except OSError:
        return
    if current == target:
        return
    # Avoid infinite loop
    if os.environ.get("TRANSLATOR_INTIME_BOOTSTRAPPED") == "1":
        return
    env = os.environ.copy()
    env["TRANSLATOR_INTIME_BOOTSTRAPPED"] = "1"
    os.execve(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]], env)


_reexec_with_venv()

from src.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
