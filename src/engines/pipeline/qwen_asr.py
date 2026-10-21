"""Local Qwen3-ASR (0.6B / 1.7B) through native transformers support.

Requires ``torch`` and a transformers release that ships
``Qwen3ASRForConditionalGeneration``. The ``-hf`` repositories are mandatory:
the original ``Qwen/Qwen3-ASR-*`` snapshots use a different weight layout and
would load with randomly initialised parameters.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.engines.pipeline.model_catalog import asr_option_by_id
from src.utils import hf_download
from src.utils.logger import logger

_LOG_INTERVAL_SEC = 30.0
_MAX_NEW_TOKENS = 256

QWEN_ASR_MODELS: dict[str, str] = {
    "qwen3-asr-0.6b": "Qwen/Qwen3-ASR-0.6B-hf",
    "qwen3-asr-1.7b": "Qwen/Qwen3-ASR-1.7B-hf",
}

_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "yue": "Cantonese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
}


def is_qwen_asr_model(model_id: str) -> bool:
    return (model_id or "").strip().lower() in QWEN_ASR_MODELS


def resolve_torch_device(preference: str) -> str:
    pref = (preference or "auto").strip().lower()
    if pref == "cpu":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except ImportError:
        pass
    if pref == "cuda":
        logger.warning("Qwen3-ASR：选择了 CUDA 但未检测到 GPU，回退 CPU")
    return "cpu"


class QwenLocalAsr:
    """Offline Qwen3-ASR with background download/load and single-flight inference."""

    def __init__(
        self,
        *,
        model_id: str = "qwen3-asr-0.6b",
        device_preference: str = "auto",
        hotwords: list[str] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        key = (model_id or "qwen3-asr-0.6b").strip().lower()
        self._model_id = key if key in QWEN_ASR_MODELS else "qwen3-asr-0.6b"
        self._repo_id = QWEN_ASR_MODELS[self._model_id]
        self._device_preference = device_preference
        self._hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]
        if cache_dir is None:
            from src.utils.resource_paths import model_resource_root

            cache_dir = model_resource_root() / "asr" / self._model_id
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None
        self._device = "cpu"
        self._dtype: Any = None
        self._loading = False
        self._ready = False
        self._failed = False
        self._started = False
        self._last_err_at = 0.0

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def warming_up(self) -> bool:
        return self._loading and not self._ready

    @property
    def model_id(self) -> str:
        return self._model_id

    def start(self) -> None:
        self._started = True
        self.start_loading()

    def stop(self) -> None:
        self._started = False

    def start_loading(self) -> None:
        with self._lock:
            if self._ready or self._loading or self._failed:
                return
            self._loading = True
        logger.info(f"正在加载 Qwen3-ASR ({self._model_id})…")
        threading.Thread(target=self._load_background, name="qwen-asr-load", daemon=True).start()

    def _load_background(self) -> None:
        ok = False
        try:
            ok = self._load()
        except Exception as exc:
            logger.warning(f"Qwen3-ASR 加载失败: {exc}")
        with self._lock:
            self._ready = ok
            self._failed = not ok
            self._loading = False
        if ok:
            logger.info(f"Qwen3-ASR 已就绪 ({self._model_id} · {self._device})")

    def _load(self) -> bool:
        try:
            import torch
            import transformers
        except ImportError:
            logger.warning("Qwen3-ASR 需要 torch 与 transformers：pip install torch transformers")
            return False
        model_cls = getattr(transformers, "Qwen3ASRForConditionalGeneration", None)
        if model_cls is None:
            logger.warning(
                f"transformers {transformers.__version__} 不支持 Qwen3-ASR，"
                "请升级 transformers 或改用 SenseVoice / faster-whisper"
            )
            return False
        if not self._ensure_files():
            return False
        self._device = resolve_torch_device(self._device_preference)
        self._dtype = torch.bfloat16 if self._device.startswith("cuda") else torch.float32
        processor_cls: Any = transformers.AutoProcessor
        processor = processor_cls.from_pretrained(str(self._cache_dir), local_files_only=True)
        model, info = model_cls.from_pretrained(
            str(self._cache_dir),
            local_files_only=True,
            dtype=self._dtype,
            device_map=self._device,
            output_loading_info=True,
        )
        missing = list(info.get("missing_keys", ()))
        if missing:
            logger.warning(
                f"Qwen3-ASR 权重与模型结构不匹配（缺少 {len(missing)} 个参数），"
                f"请删除 {self._cache_dir} 后重新下载 {self._repo_id}"
            )
            return False
        model.eval()
        with self._lock:
            self._model = model
            self._processor = processor
        return True

    def _ensure_files(self) -> bool:
        if (self._cache_dir / "config.json").is_file() and any(
            self._cache_dir.glob("*.safetensors")
        ):
            return True
        try:
            import huggingface_hub  # noqa: F401
        except ImportError:
            logger.warning("缺少 huggingface_hub，无法下载 Qwen3-ASR")
            return False
        option = asr_option_by_id(self._model_id)
        try:
            hf_download.snapshot(
                self._repo_id,
                self._cache_dir,
                label=option.title if option else self._repo_id,
                key=f"asr:{self._model_id}",
                total_mb=option.download_mb if option else 0,
            )
        except Exception as exc:
            logger.warning(f"Qwen3-ASR 下载失败: {exc}")
            return False
        return (self._cache_dir / "config.json").is_file()

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        if not pcm or not self._started:
            return None
        if not self._ready:
            if not self._failed and not self._loading:
                self.start_loading()
            return None
        try:
            with self._infer_lock:
                return self._recognize_locked(pcm, language)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"Qwen3-ASR 识别失败: {exc}")
            return None

    def _recognize_locked(self, pcm: bytes, language: str) -> str | None:
        import torch

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return None
        processor, model = self._processor, self._model
        system = " ".join(self._hotwords[:50])
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "audio", "audio": ""}]},
        ]
        prompt = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        hint = _LANGUAGE_NAMES.get((language or "")[:2].lower())
        if hint:
            prompt += f"language {hint}<asr_text>"
        with torch.inference_mode():
            inputs = processor(text=[prompt], audio=[audio], return_tensors="pt", padding=True)
            inputs = inputs.to(model.device, self._dtype)
            output = model.generate(**inputs, max_new_tokens=_MAX_NEW_TOKENS)
        sequences = output.sequences if hasattr(output, "sequences") else output
        generated = sequences[:, inputs["input_ids"].shape[1] :]
        decoded = processor.decode(generated, skip_special_tokens=True)
        raw = decoded[0] if isinstance(decoded, list) and decoded else decoded
        parsed = processor.parse_output(str(raw or ""))
        text = parsed.get("transcription") if isinstance(parsed, dict) else raw
        return str(text or "").strip() or None
