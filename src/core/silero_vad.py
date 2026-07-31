"""Silero VAD ONNX runtime wrapper.

Uses the official snakers4/silero-vad stateful ONNX model. The model consumes
512 float32 samples at 16 kHz and keeps recurrent state between calls.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import numpy as np

from src.utils.logger import logger
from src.utils.resource_paths import bundled_path, model_resource_root

SILERO_VAD_URLS = [
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
]
SILERO_VAD_URL = SILERO_VAD_URLS[0]
_BUNDLED_MODEL_PATH = bundled_path("resource", "vad", "silero_vad.onnx")
_PERSISTENT_MODEL_PATH = model_resource_root() / "vad" / "silero_vad.onnx"
DEFAULT_MODEL_PATH = (
    _BUNDLED_MODEL_PATH if _BUNDLED_MODEL_PATH.is_file() else _PERSISTENT_MODEL_PATH
)


class SileroVadEngine:
    """Lazy ONNXRuntime wrapper for Silero VAD."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        sample_rate: int = 16000,
        download_url: str = SILERO_VAD_URL,
    ) -> None:
        self.model_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        self.sample_rate = sample_rate
        self.download_url = download_url
        self._session: Any | None = None
        self._input_names: set[str] = set()
        self._available = False
        self._failed = False
        self._loading = False
        self._lock = threading.Lock()
        self._state: np.ndarray | None = None
        self._h: np.ndarray | None = None
        self._c: np.ndarray | None = None

    def is_available(self) -> bool:
        if self._available:
            return True
        if not self._failed:
            self.start_loading()
        return self._available

    def start_loading(self) -> None:
        with self._lock:
            if self._available or self._failed or self._loading:
                return
            self._loading = True
        thread = threading.Thread(
            target=self._load_background,
            name="silero-vad-loader",
            daemon=True,
        )
        thread.start()

    def reset(self) -> None:
        self._state = None
        self._h = None
        self._c = None

    def prob(self, pcm_float32_512: np.ndarray) -> float:
        if not self.is_available() or self._session is None:
            return 0.0
        x = np.asarray(pcm_float32_512, dtype=np.float32)
        if x.size != 512:
            raise ValueError("Silero VAD expects exactly 512 samples at 16 kHz")
        x = x.reshape(1, 512)
        inputs = self._build_inputs(x)
        outputs = self._session.run(None, inputs)
        self._capture_state(outputs)
        return float(np.asarray(outputs[0]).reshape(-1)[0])

    def _load_background(self) -> None:
        ok = self._load()
        with self._lock:
            self._available = ok
            self._failed = not ok
            self._loading = False

    def _load(self) -> bool:
        if self.sample_rate != 16000:
            logger.warning("Silero VAD only supports 16 kHz input in this pipeline")
            return False
        if not self.model_path.exists() and not self._download_model():
            return False
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
            )
            self._input_names = {i.name for i in self._session.get_inputs()}
            self.reset()
            logger.info(f"Silero VAD 已加载: {self.model_path}")
            return True
        except Exception as exc:
            logger.warning(f"Silero VAD 加载失败，回退 RMS VAD: {exc}")
            self._session = None
            return False

    def _download_model(self) -> bool:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        for url in SILERO_VAD_URLS:
            try:
                logger.info(f"Silero VAD 模型缺失，尝试下载: {url}")
                urlretrieve(url, self.model_path)
                logger.info(f"Silero VAD 模型已保存: {self.model_path}")
                return True
            except Exception as exc:
                logger.warning(f"Silero VAD 模型下载失败（{url}）: {exc}")
        logger.warning("Silero VAD 所有镜像下载均失败，回退 RMS VAD")
        return False

    def _build_inputs(self, x: np.ndarray) -> dict[str, np.ndarray]:
        names = self._input_names
        data: dict[str, np.ndarray] = {}
        data["input" if "input" in names else next(iter(names - {"sr", "state", "h", "c"}))] = x
        if "sr" in names:
            data["sr"] = np.array(self.sample_rate, dtype=np.int64)
        if "state" in names:
            if self._state is None:
                self._state = np.zeros((2, 1, 128), dtype=np.float32)
            data["state"] = self._state
        else:
            if "h" in names:
                if self._h is None:
                    self._h = np.zeros(self._state_shape("h", 64), dtype=np.float32)
                data["h"] = self._h
            if "c" in names:
                if self._c is None:
                    self._c = np.zeros(self._state_shape("c", 64), dtype=np.float32)
                data["c"] = self._c
        return data

    def _state_shape(self, input_name: str, fallback_hidden: int) -> tuple[int, int, int]:
        assert self._session is not None
        for item in self._session.get_inputs():
            if item.name != input_name:
                continue
            shape = item.shape
            if len(shape) == 3:
                layers = int(shape[0]) if isinstance(shape[0], int) else 2
                hidden = int(shape[2]) if isinstance(shape[2], int) else fallback_hidden
                return (layers, 1, hidden)
        return (2, 1, fallback_hidden)

    def _capture_state(self, outputs: list[Any]) -> None:
        if len(outputs) >= 2:
            if "state" in self._input_names:
                self._state = np.asarray(outputs[1], dtype=np.float32)
            elif len(outputs) >= 3:
                self._h = np.asarray(outputs[1], dtype=np.float32)
                self._c = np.asarray(outputs[2], dtype=np.float32)
