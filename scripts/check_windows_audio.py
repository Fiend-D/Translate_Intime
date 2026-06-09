#!/usr/bin/env python3
"""检查 Windows WASAPI loopback 设备。"""
from __future__ import annotations

import platform


def main() -> None:
    if platform.system() != "Windows":
        print("当前不是 Windows，仅 Windows 需要运行此脚本。")
        return

    try:
        import soundcard as sc
    except Exception as exc:
        print(f"soundcard 导入失败: {exc}")
        print("请运行: python -m pip install soundcard")
        return

    default = sc.default_speaker()
    print(f"默认输出: {default.name if default else '(无)'}")
    print("\n可用于游戏声音捕获的 WASAPI loopback:")
    for speaker in sc.all_speakers():
        print(f"  [Loopback] {speaker.name}")
        print(f"    id=wasapi_loopback:{speaker.name}")


if __name__ == "__main__":
    main()
