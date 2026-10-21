"""Process-wide registry of model downloads, polled by the GUI for progress display.

Download threads report bytes; the Qt side reads :func:`snapshot` from a timer, so
no cross-thread signals are needed. Downloads driven by third-party libraries
(huggingface_hub, faster-whisper) register a ``watch_dir`` instead and progress is
measured from the bytes that have landed on disk.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

DownloadState = Literal["running", "done", "failed"]

# Finished entries stay visible briefly so the user sees "完成" / "失败".
_LINGER_SECONDS = 8.0
_SPEED_WINDOW_SECONDS = 3.0


@dataclass(frozen=True)
class DownloadTask:
    key: str
    label: str
    url: str
    dest: str
    done: int = 0
    total: int | None = None
    speed: float = 0.0
    state: DownloadState = "running"
    error: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0

    @property
    def fraction(self) -> float | None:
        if not self.total or self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))


@dataclass
class _Entry:
    task: DownloadTask
    watch_dir: Path | None = None
    watch_base: int = 0
    samples: list[tuple[float, int]] | None = None


_lock = threading.Lock()
_entries: dict[str, _Entry] = {}
_WATCH_INTERVAL_SECONDS = 1.0
_watcher: threading.Thread | None = None


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def begin(
    key: str,
    label: str,
    *,
    url: str = "",
    dest: str | Path = "",
    total: int | None = None,
    watch_dir: Path | None = None,
) -> None:
    """Start (or restart) tracking ``key``; ``watch_dir`` measures progress from disk."""
    now = time.monotonic()
    base = _dir_size(watch_dir) if watch_dir is not None else 0
    with _lock:
        _entries[key] = _Entry(
            task=DownloadTask(
                key=key,
                label=label,
                url=url,
                dest=str(dest),
                total=total,
                started_at=now,
                updated_at=now,
            ),
            watch_dir=watch_dir,
            watch_base=base,
            samples=[(now, 0)],
        )
    if watch_dir is not None:
        _ensure_watcher()


def _ensure_watcher() -> None:
    """Disk sizes are sampled off the GUI thread; the thread exits when nothing is watched."""
    global _watcher
    with _lock:
        if _watcher is not None and _watcher.is_alive():
            return
        _watcher = threading.Thread(target=_watch_loop, name="download-progress", daemon=True)
        _watcher.start()


def _watch_loop() -> None:
    while True:
        with _lock:
            watched = [
                (key, entry.watch_dir, entry.watch_base)
                for key, entry in _entries.items()
                if entry.watch_dir is not None and entry.task.state == "running"
            ]
        if not watched:
            return
        sizes = {key: max(0, _dir_size(path) - base) for key, path, base in watched}
        now = time.monotonic()
        with _lock:
            for key, size in sizes.items():
                entry = _entries.get(key)
                if entry is not None and entry.task.state == "running":
                    _record(entry, now, size, None)
        time.sleep(_WATCH_INTERVAL_SECONDS)


def set_source(key: str, url: str, *, total: int | None = None) -> None:
    """Record a mirror switch; byte count restarts unless the download resumes."""
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return
        entry.task = replace(entry.task, url=url, total=total or entry.task.total)


def update(key: str, done: int, total: int | None = None) -> None:
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return
        _record(entry, now, done, total)


def _record(entry: _Entry, now: float, done: int, total: int | None) -> None:
    samples = entry.samples if entry.samples is not None else []
    # A resumed download starts at its offset; those bytes were not fetched just now.
    first_update = len(samples) == 1 and samples[0][1] == 0
    if first_update or (samples and done < samples[-1][1]):
        samples = []
    samples.append((now, done))
    while len(samples) > 2 and now - samples[0][0] > _SPEED_WINDOW_SECONDS:
        samples.pop(0)
    entry.samples = samples
    first_t, first_done = samples[0]
    elapsed = now - first_t
    speed = (done - first_done) / elapsed if elapsed > 0.2 and done >= first_done else 0.0
    entry.task = replace(
        entry.task,
        done=done,
        total=total if total is not None else entry.task.total,
        speed=max(0.0, speed),
        updated_at=now,
    )


def finish(key: str, *, ok: bool, error: str = "") -> None:
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return
        task = entry.task
        done = task.total if ok and task.total else task.done
        entry.task = replace(
            task,
            done=done or 0,
            state="done" if ok else "failed",
            error=error[:200],
            speed=0.0,
            updated_at=now,
        )
        entry.watch_dir = None


@contextmanager
def tracked(
    key: str,
    label: str,
    *,
    url: str = "",
    dest: str | Path = "",
    total: int | None = None,
    watch_dir: Path | None = None,
) -> Iterator[None]:
    """Track a library-driven download; marks failure when the block raises."""
    begin(key, label, url=url, dest=dest, total=total, watch_dir=watch_dir)
    try:
        yield
    except BaseException as exc:
        finish(key, ok=False, error=str(exc))
        raise
    finish(key, ok=True)


def snapshot() -> list[DownloadTask]:
    """Current tasks (running first). Cheap: safe to call from a GUI timer."""
    now = time.monotonic()
    with _lock:
        for key in [
            k
            for k, e in _entries.items()
            if e.task.state != "running" and now - e.task.updated_at > _LINGER_SECONDS
        ]:
            del _entries[key]
        tasks = [entry.task for entry in _entries.values()]
    return sorted(tasks, key=lambda t: (t.state != "running", t.started_at))


def clear() -> None:
    with _lock:
        _entries.clear()


def _mb(n: float) -> str:
    return f"{n / 1_000_000:.0f}" if n >= 10_000_000 else f"{n / 1_000_000:.1f}"


def format_task(task: DownloadTask) -> str:
    """One-line Chinese summary, e.g. ``Kokoro 中文模型 42%（130/310 MB，1.2 MB/s）``."""
    if task.state == "done":
        return f"{task.label} 下载完成"
    if task.state == "failed":
        return f"{task.label} 下载失败：{task.error or '网络错误'}"
    frac = task.fraction
    size = f"{_mb(task.done)}/{_mb(task.total or 0)} MB" if task.total else f"{_mb(task.done)} MB"
    speed = f"，{task.speed / 1_000_000:.1f} MB/s" if task.speed > 0 else ""
    pct = f" {frac * 100:.0f}%" if frac is not None else ""
    return f"{task.label}{pct}（{size}{speed}）"
