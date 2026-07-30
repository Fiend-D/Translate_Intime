"""本地 ASR via faster-whisper (CTranslate2 加速 Whisper).

faster-whisper 是基于 CTranslate2 的 Whisper 实现, 比原版快 4 倍以上.
- 准确率: Whisper medium 接近 Windows 辅助字幕水平
- 噪声鲁棒: 对 BGM/音效抗干扰, 不会产生 SenseVoice 那样的噪点输出
- 多语言: 中英文准确识别, 不会误识别为韩文等
- int8 量化: CPU 上 5 秒音频约 1-2 秒处理

模型缓存在项目内 resource/asr/whisper/, 复用 huggingface_hub 下载.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.utils.logger import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_ROOT = _PROJECT_ROOT / "resource" / "asr" / "whisper"
_LOG_INTERVAL_SEC = 30.0

# HuggingFace 镜像 (优先 hf-mirror.com)
_HF_ENDPOINTS = (
    "https://hf-mirror.com",
    "https://huggingface.co",
)

# 模型注册表: model_id → whisper 模型大小 + 元信息
# faster-whisper 模型大小: tiny / base / small / medium / large-v3
_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "faster-whisper-medium": {
        "title": "Whisper medium 多语言",
        "whisper_size": "medium",
        "download_mb": 770,
        "ram_mb": 1500,
        "quality": "优秀，接近辅助字幕，噪声鲁棒",
        "note": "★推荐：中英文准确率最高，CPU可跑",
        "recommended": True,
    },
    "faster-whisper-large-v3": {
        "title": "Whisper large-v3 多语言",
        "whisper_size": "large-v3",
        "download_mb": 1550,
        "ram_mb": 3000,
        "quality": "最高准确率",
        "note": "需要 GPU 才能低延迟；CPU 较慢",
        "recommended": False,
    },
}


def whisper_cache_dir(model_id: str) -> Path:
    info = _MODEL_REGISTRY.get(model_id)
    size = info["whisper_size"] if info else "medium"
    return _CACHE_ROOT / size


def list_whisper_model_ids() -> list[str]:
    return list(_MODEL_REGISTRY.keys())


def whisper_model_info(model_id: str) -> dict[str, Any] | None:
    return _MODEL_REGISTRY.get((model_id or "").strip())


def _resolve_device(device_preference: str) -> str:
    """auto → cpu (faster-whisper CUDA 需要 NVIDIA GPU)."""
    pref = (device_preference or "auto").strip().lower()
    if pref == "cuda":
        return "cuda"
    if pref == "cpu":
        return "cpu"
    # auto: 检测 CUDA
    try:
        import torch  # noqa: F401
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    # ctranslate2 也能检测
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _resolve_compute_type(device: str) -> str:
    """CPU → int8, CUDA → int8_float16."""
    if device == "cuda":
        return "int8_float16"
    return "int8"


class FasterWhisperAsr:
    """本地 ASR (faster-whisper) — CTranslate2 加速 Whisper.

    - 异步加载: 首次调用 start() 后台加载模型, warming_up 期间不阻塞
    - 内置 VAD: transcribe 时启用 vad_filter, 自动过滤静音段
    - 噪声鲁棒: Whisper 对 BGM/音效抗干扰, 不产生噪点输出
    - int8 量化: CPU 上 medium 模型 5 秒音频约 1-2 秒处理
    """

    def __init__(
        self,
        *,
        model_id: str = "faster-whisper-medium",
        device_preference: str = "auto",
        hotwords: list[str] | None = None,
    ) -> None:
        self._model_id = (model_id or "faster-whisper-medium").strip().lower()
        info = _MODEL_REGISTRY.get(self._model_id)
        self._whisper_size = info["whisper_size"] if info else "medium"
        self._device_preference = (device_preference or "auto").strip().lower()
        self._started = False
        self._last_err_at = 0.0
        self._lock = threading.Lock()

        # 热词: faster-whisper 通过 initial_prompt 传入, 引导模型识别特定词汇.
        # 不像 sherpa-onnx 那样有 score 权重, 但对专名识别仍有明显提升.
        self._hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]

        self._model: Any = None
        self._loading = False
        self._ready = False
        self._failed = False

    # ---- 公开接口 ----

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
        """异步加载模型."""
        with self._lock:
            if self._ready or self._failed or self._loading:
                return
            self._loading = True

        logger.info(f"正在加载 faster-whisper ({self._model_id})…")
        thread = threading.Thread(
            target=self._load_background,
            name="whisper-asr-loader",
            daemon=True,
        )
        thread.start()

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        if not pcm or not self._started:
            return None
        if not self._ready:
            if not self._failed and not self._loading:
                self.start_loading()
            return None
        if self._model is None:
            return None
        try:
            return self._recognize_internal(pcm, language)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"faster-whisper ASR recognize failed: {exc}")
            return None

    # ---- 内部方法 ----

    def _recognize_internal(self, pcm: bytes, language: str) -> str | None:
        # PCM16 s16le → float32 [-1, 1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return None

        # faster-whisper language 参数: "zh" / "en" / None(自动检测)
        lang = (language or "")[:2].lower()
        lang_param = lang if lang in ("zh", "en", "ja", "ko") else None

        # 热词通过 initial_prompt 传入, 引导模型识别特定词汇.
        # Whisper 会将 prompt 作为前文上下文, 提升其中词汇的识别概率.
        # 拼成自然语句比纯词列表效果更好 (模拟前文对话上下文).
        initial_prompt: str | None = None
        if self._hotwords:
            # 限制 prompt 长度, 避免过长影响识别 (Whisper prompt 上限 244 token)
            words = self._hotwords[:50]
            initial_prompt = "、".join(words) + "。"

        transcribe_kwargs: dict = dict(
            language=lang_param,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=200,
            ),
            # 无语音段返回空, 避免噪点
            condition_on_previous_text=False,
        )
        if initial_prompt:
            transcribe_kwargs["initial_prompt"] = initial_prompt

        segments, _info = self._model.transcribe(samples, **transcribe_kwargs)
        # segments 是生成器, 需要遍历收集
        texts: list[str] = []
        for seg in segments:
            t = (seg.text or "").strip()
            if t:
                texts.append(t)
        if not texts:
            return None
        return " ".join(texts).strip() or None

    def _load_background(self) -> None:
        ok = False
        try:
            ok = self._load_model()
        except Exception as exc:
            logger.warning(f"faster-whisper ASR 加载失败: {exc}")
            ok = False
        with self._lock:
            if ok:
                self._ready = True
            else:
                self._failed = True
            self._loading = False
        if ok:
            logger.info(f"faster-whisper ASR 已就绪 ({self._model_id})")
        else:
            logger.warning(f"faster-whisper ASR ({self._model_id}) 不可用，识别将无法工作")

    def _load_model(self) -> bool:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.warning(
                "faster-whisper 未安装，请执行: pip install faster-whisper"
            )
            return False

        device = _resolve_device(self._device_preference)
        compute_type = _resolve_compute_type(device)
        cache_dir = whisper_cache_dir(self._model_id)

        # 设置 HF 镜像 (优先 hf-mirror.com)，并绕过系统代理直连下载
        from src.utils.proxy_env import prepare_model_download_env, without_proxy

        cache_dir.mkdir(parents=True, exist_ok=True)
        prepare_model_download_env()
        prev_endpoint = os.environ.get("HF_ENDPOINT", "")
        if not prev_endpoint:
            os.environ["HF_ENDPOINT"] = _HF_ENDPOINTS[0]

        logger.info(
            f"faster-whisper 加载: size={self._whisper_size} device={device} "
            f"compute={compute_type} cache={cache_dir}"
        )
        try:
            with without_proxy():
                self._model = WhisperModel(
                    self._whisper_size,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(cache_dir),
                )
        finally:
            # 恢复 HF_ENDPOINT (如果之前没设置)
            if not prev_endpoint:
                os.environ.pop("HF_ENDPOINT", None)

        logger.info(
            f"faster-whisper ASR (offline) device={device} compute={compute_type} "
            f"size={self._whisper_size}"
        )
        return True
