"""设备检测与实时资源监控 (CPU/RAM/GPU).

提供:
- detect_cuda_available(): 检测 CUDA GPU 是否可用
- get_device_options(): 返回可选设备列表 (auto/cpu/cuda)
- resolve_device(preference): 根据 preference 解析实际设备
- ResourceMonitor: 实时采集 CPU/RAM/GPU 占用
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

_CACHE_TTL_SEC = 5.0
_cuda_cache: tuple[bool, float] | None = None


def detect_cuda_available() -> bool:
    """检测 CUDA GPU 是否可用 (结果缓存 5 秒)."""
    global _cuda_cache
    now = time.monotonic()
    if _cuda_cache is not None:
        ok, ts = _cuda_cache
        if now - ts < _CACHE_TTL_SEC:
            return ok

    ok = False
    # 1. ctranslate2 (NLLB)
    try:
        import ctranslate2

        if int(ctranslate2.get_cuda_device_count() or 0) > 0:
            ok = True
    except Exception:
        pass

    # 2. onnxruntime CUDA EP (sherpa-onnx / kokoro-onnx)
    if not ok:
        try:
            import onnxruntime as ort

            for p in ort.get_available_providers():
                if "CUDA" in p or "TensorRT" in p:
                    ok = True
                    break
        except Exception:
            pass

    # 3. pynvml 直接检测 NVIDIA 驱动
    if not ok:
        try:
            import pynvml

            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            pynvml.nvmlShutdown()
            if count > 0:
                ok = True
        except Exception:
            pass

    _cuda_cache = (ok, now)
    return ok


def get_device_options() -> list[tuple[str, str]]:
    """返回 (label, value) 列表, 只包含可用选项."""
    opts: list[tuple[str, str]] = [("自动", "auto"), ("CPU", "cpu")]
    if detect_cuda_available():
        opts.append(("GPU (CUDA)", "cuda"))
    return opts


def resolve_device(preference: str) -> str:
    """根据 preference 返回实际设备 ('cpu' 或 'cuda')."""
    pref = (preference or "auto").strip().lower()
    if pref == "cuda":
        return "cuda" if detect_cuda_available() else "cpu"
    if pref == "cpu":
        return "cpu"
    # auto
    return "cuda" if detect_cuda_available() else "cpu"


@dataclass
class ResourceSnapshot:
    """某一时刻的资源占用快照."""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_percent: float | None = None  # None = 无 GPU 或不可用
    gpu_vram_percent: float | None = None
    gpu_name: str | None = None


class ResourceMonitor:
    """定时采集 CPU/RAM/GPU 占用, 线程安全."""

    def __init__(self, interval_ms: int = 1500) -> None:
        self._interval = max(500, int(interval_ms)) / 1000.0
        self._snapshot: ResourceSnapshot = ResourceSnapshot()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._psutil_ok = False
        self._pynvml_ok = False
        self._gpu_handle: Any = None
        self._gpu_name: str | None = None

        try:
            import psutil  # noqa: F401

            self._psutil_ok = True
        except ImportError:
            pass

        try:
            import pynvml

            pynvml.nvmlInit()
            self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._gpu_name = pynvml.nvmlDeviceGetName(self._gpu_handle)
            if isinstance(self._gpu_name, bytes):
                self._gpu_name = self._gpu_name.decode("utf-8", errors="replace")
            self._pynvml_ok = True
        except Exception:
            self._pynvml_ok = False

    @property
    def snapshot(self) -> ResourceSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def has_gpu(self) -> bool:
        return self._pynvml_ok

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        # psutil cpu_percent 第一次调用返回 0, 提前预热
        if self._psutil_ok:
            try:
                import psutil

                psutil.cpu_percent(interval=None)
            except Exception:
                pass
        while self._running:
            self._collect()
            time.sleep(self._interval)

    def _collect(self) -> None:
        snap = ResourceSnapshot()
        if self._psutil_ok:
            try:
                import psutil

                snap.cpu_percent = float(psutil.cpu_percent(interval=None))
                vm = psutil.virtual_memory()
                snap.ram_percent = float(vm.percent)
                snap.ram_total_gb = vm.total / (1024**3)
                snap.ram_used_gb = vm.used / (1024**3)
            except Exception:
                pass

        if self._pynvml_ok and self._gpu_handle is not None:
            try:
                import pynvml

                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                snap.gpu_percent = float(util.gpu)
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                if mem.total > 0:
                    snap.gpu_vram_percent = float(mem.used) / float(mem.total) * 100.0
                snap.gpu_name = self._gpu_name
            except Exception:
                snap.gpu_percent = None
                snap.gpu_vram_percent = None

        with self._lock:
            self._snapshot = snap

    def format_text(self) -> str:
        """格式化为一行文本, 用于 UI 标签."""
        snap = self.snapshot
        parts: list[str] = []
        parts.append(f"CPU {snap.cpu_percent:.0f}%")
        if snap.ram_total_gb > 0:
            parts.append(f"RAM {snap.ram_used_gb:.1f}/{snap.ram_total_gb:.1f}GB")
        if snap.gpu_percent is not None:
            gpu_label = f"GPU {snap.gpu_percent:.0f}%"
            if snap.gpu_vram_percent is not None:
                gpu_label += f" (VRAM {snap.gpu_vram_percent:.0f}%)"
            parts.append(gpu_label)
        return " | ".join(parts)
