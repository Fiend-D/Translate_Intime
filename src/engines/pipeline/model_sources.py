"""Where each local model is downloaded from and stored, for display in settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engines.pipeline import kokoro_tts, sherpa_asr, sherpa_tts, whisper_asr
from src.engines.pipeline.llm_mt import HYMT_DEFAULT_FILE, HYMT_DEFAULT_REPO, default_hymt_dir
from src.engines.pipeline.model_catalog import asr_option_by_id, nllb_option_by_id
from src.engines.pipeline.nllb_mt import nllb_cache_dir
from src.engines.pipeline.qwen_asr import QWEN_ASR_MODELS
from src.utils.resource_paths import model_resource_root

_HF_MIRRORS = ("hf-mirror.com",)
_GH_MIRRORS = ("gh-proxy.com", "ghfast.top")


@dataclass(frozen=True)
class ModelSource:
    title: str
    origin: str  # canonical URL (GitHub release / Hugging Face repo)
    mirrors: tuple[str, ...]  # mirror hosts tried alongside the origin
    path: Path
    size_mb: int = 0


def _hf(title: str, repo: str, path: Path, size_mb: int) -> ModelSource:
    return ModelSource(title, f"https://huggingface.co/{repo}", _HF_MIRRORS, path, size_mb)


def asr_source(model_id: str) -> ModelSource | None:
    """Download source for a local ASR id (Live Captions has none)."""
    model_id = (model_id or "").strip()
    option = asr_option_by_id(model_id)
    size = option.download_mb if option else 0
    title = option.title if option else model_id
    registry = {**sherpa_asr._MODEL_REGISTRY, **sherpa_asr._OFFLINE_MODEL_REGISTRY}
    if model_id == "auto":
        # Smart routing downloads both streaming Zipformer models on first use
        return ModelSource(
            title,
            "https://huggingface.co/csukuangfj（英文 + 中英双语两个 Zipformer 模型）",
            _HF_MIRRORS,
            sherpa_asr.sherpa_cache_dir("auto").parent,
            size,
        )
    if model_id in registry:
        repo = registry[model_id]["repo"]
        return _hf(title, repo, sherpa_asr.sherpa_cache_dir(model_id), size)
    if model_id in whisper_asr._MODEL_REGISTRY:
        whisper_size = whisper_asr._MODEL_REGISTRY[model_id]["whisper_size"]
        repo = f"Systran/faster-whisper-{whisper_size}"
        return _hf(title, repo, whisper_asr.whisper_cache_dir(model_id), size)
    if model_id in QWEN_ASR_MODELS:
        path = model_resource_root() / "asr" / model_id
        return _hf(title, QWEN_ASR_MODELS[model_id], path, size)
    return None


def nllb_source(model_id: str) -> ModelSource:
    option = nllb_option_by_id(model_id)
    title = option.title if option else model_id
    return _hf(title, model_id, nllb_cache_dir(model_id), option.download_mb if option else 0)


def hymt_source() -> ModelSource:
    return _hf(f"Hy-MT2 · {HYMT_DEFAULT_FILE}", HYMT_DEFAULT_REPO, default_hymt_dir(), 1133)


def kokoro_source() -> ModelSource:
    return ModelSource(
        "Kokoro-82M（英文 v1.0 + 中文 v1.1）",
        "https://github.com/thewh1teagle/kokoro-onnx/releases",
        _GH_MIRRORS,
        kokoro_tts._CACHE_ROOT,
        735,
    )


def melo_source() -> ModelSource:
    return ModelSource(
        "MeloTTS 中英",
        f"{sherpa_tts._RELEASE}/{sherpa_tts.MELO_MODEL}.tar.bz2",
        _GH_MIRRORS,
        sherpa_tts.melo_model_dir(),
        167,
    )


def silero_source() -> ModelSource:
    from src.core.silero_vad import DEFAULT_MODEL_PATH, SILERO_VAD_URL

    return ModelSource("Silero VAD", SILERO_VAD_URL, _GH_MIRRORS, DEFAULT_MODEL_PATH, 1)


def format_source(source: ModelSource) -> str:
    """Two short lines: where it comes from, where it lives on disk."""
    origin = source.origin.removeprefix("https://")
    mirrors = f"（镜像 {'、'.join(source.mirrors)}）" if source.mirrors else ""
    size = f" · 约 {source.size_mb} MB" if source.size_mb else ""
    return f"下载：{origin}{mirrors}{size}\n存放：{source.path}"


def source_tooltip(source: ModelSource) -> str:
    lines = [source.title, f"原始地址：{source.origin}"]
    if source.mirrors:
        lines.append(f"国内镜像：{'、'.join(source.mirrors)}（自动依次尝试）")
    lines.append(f"存放目录：{source.path}")
    return "\n".join(lines)
