#!/usr/bin/env python3
"""检查 PulseAudio/PipeWire 音频路由和 monitor 源电平。"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import time

import numpy as np


def run_pactl(*args: str) -> str:
    result = subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or result.stderr.strip()


def print_section(title: str, body: str) -> None:
    print(f"\n== {title} ==")
    print(body or "(空)")


def measure_source(source: str, seconds: float, rate: int, channels: int) -> None:
    if shutil.which("parec") is None:
        raise SystemExit("未找到 parec，请先安装: sudo apt install pulseaudio-utils")

    cmd = [
        "parec",
        f"--device={source}",
        "--format=s16le",
        f"--rate={rate}",
        f"--channels={channels}",
        "--raw",
    ]
    print(f"\n== 测量 {source} ==")
    print("请保持视频播放，下面每 0.5 秒输出一次 RMS。")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None

    chunk_frames = int(rate * 0.5)
    chunk_bytes = chunk_frames * channels * 2
    end_at = time.time() + seconds

    try:
        while time.time() < end_at:
            raw = proc.stdout.read(chunk_bytes)
            if not raw:
                err = proc.stderr.read().decode(errors="ignore").strip() if proc.stderr else ""
                print(f"无数据: {err}")
                break

            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = math.sqrt(float(np.mean(data * data))) if data.size else 0.0
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            print(f"rms={rms:.5f} peak={peak:.5f}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="translator_virtual_sink.monitor",
        help="要测试的 PulseAudio/PipeWire source",
    )
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    args = parser.parse_args()

    print_section("Sinks 输出设备", run_pactl("list", "short", "sinks"))
    print_section("Sources 输入/Monitor", run_pactl("list", "short", "sources"))
    print_section("Sink Inputs 正在播放的应用", run_pactl("list", "short", "sink-inputs"))
    measure_source(args.source, args.seconds, args.rate, args.channels)


if __name__ == "__main__":
    main()
