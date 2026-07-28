"""Profile CPU/RAM usage during an active translation session."""

import time

import psutil


def profile_for(duration_seconds: int = 180, interval: float = 1.0) -> None:
    """Print peak and average CPU/RAM over a profiling window."""
    process = psutil.Process()
    samples_cpu: list[float] = []
    samples_ram_mb: list[float] = []

    print(f"Profiling for {duration_seconds}s...")
    start = time.time()
    while time.time() - start < duration_seconds:
        samples_cpu.append(process.cpu_percent(interval=interval))
        samples_ram_mb.append(process.memory_info().rss / (1024 * 1024))

    if not samples_cpu:
        return

    avg_cpu = sum(samples_cpu) / len(samples_cpu)
    peak_cpu = max(samples_cpu)
    avg_ram = sum(samples_ram_mb) / len(samples_ram_mb)
    peak_ram = max(samples_ram_mb)

    print(f"CPU avg/peak: {avg_cpu:.1f}% / {peak_cpu:.1f}%")
    print(f"RAM avg/peak: {avg_ram:.1f} MB / {peak_ram:.1f} MB")

    if peak_ram > 2048:
        print("WARNING: Peak RAM exceeds 2 GB constitutional limit")
    if avg_cpu > 30:
        print("WARNING: Average CPU exceeds 30% constitutional limit")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    profile_for(args.duration, args.interval)
