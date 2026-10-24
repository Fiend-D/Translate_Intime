"""Where each local model is downloaded from and stored, for display in settings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.engines.pipeline import kokoro_tts, sherpa_asr, sherpa_tts, whisper_asr
from src.engines.pipeline.llm_mt import default_hymt_dir, hymt_model
from src.engines.pipeline.model_catalog import asr_option_by_id, nllb_option_by_id
from src.engines.pipeline.model_manifests import HYMT, NLLB, QWEN_ASR, SHERPA_ASR, WHISPER
from src.engines.pipeline.nllb_mt import nllb_cache_dir
from src.engines.pipeline.qwen_asr import QWEN_ASR_MODELS
from src.utils.model_fetch import RepoManifest, github_urls, host_of, ordered_urls
from src.utils.resource_paths import model_resource_root


@dataclass(frozen=True)
class ModelSource:
    title: str
    origin: str  # canonical URL (GitHub release / Hugging Face repo)
    hosts: tuple[str, ...]  # download hosts in the order they are tried
    path: Path
    size_mb: int = 0


def _unique_hosts(urls: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for url in urls:
        host = host_of(url)
        if host not in out:
            out.append(host)
    return tuple(out)


def _repo(title: str, manifest: RepoManifest, path: Path) -> ModelSource:
    hosts = _unique_hosts(ordered_urls(manifest.candidates(manifest.files[0])))
    origin = f"https://huggingface.co/{manifest.huggingface}"
    return ModelSource(title, origin, hosts, path, round(manifest.size / 1_000_000))


def _github(title: str, urls: Sequence[str], path: Path, size_mb: int) -> ModelSource:
    origin = next((u for u in urls if host_of(u) == "github.com"), urls[0])
    return ModelSource(title, origin, _unique_hosts(urls), path, size_mb)


def asr_source(model_id: str) -> ModelSource | None:
    """Download source for a local ASR id (Live Captions has none)."""
    model_id = (model_id or "").strip()
    option = asr_option_by_id(model_id)
    title = option.title if option else model_id
    if model_id == "auto":
        # Smart routing downloads both streaming Zipformer models on first use
        en = SHERPA_ASR["sherpa-onnx-streaming-zipformer-en-2023-06-26"]
        zh = SHERPA_ASR["sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"]
        src = _repo(title, zh, sherpa_asr.sherpa_cache_dir("auto").parent)
        return ModelSource(
            title,
            "https://huggingface.co/csukuangfj（英文 + 中英双语两个 Zipformer 模型）",
            src.hosts,
            src.path,
            round((en.size + zh.size) / 1_000_000),
        )
    if model_id in SHERPA_ASR:
        return _repo(title, SHERPA_ASR[model_id], sherpa_asr.sherpa_cache_dir(model_id))
    if model_id in whisper_asr._MODEL_REGISTRY:
        whisper_size = whisper_asr._MODEL_REGISTRY[model_id]["whisper_size"]
        return _repo(title, WHISPER[whisper_size], whisper_asr.whisper_cache_dir(model_id))
    if model_id in QWEN_ASR_MODELS:
        path = model_resource_root() / "asr" / model_id
        return _repo(title, QWEN_ASR[QWEN_ASR_MODELS[model_id]], path)
    return None


def nllb_source(model_id: str) -> ModelSource | None:
    manifest = NLLB.get(model_id)
    if manifest is None:
        return None
    option = nllb_option_by_id(model_id)
    return _repo(option.title if option else model_id, manifest, nllb_cache_dir(model_id))


def hymt_source(model_id: str | None = None) -> ModelSource:
    model = hymt_model(model_id)
    return _repo(f"{model.label} · {model.filename}", HYMT[model.repo_id], default_hymt_dir())


def kokoro_source() -> ModelSource:
    return _github(
        "Kokoro-82M（英文 v1.0 + 中文 v1.1）",
        kokoro_tts._en_model_urls() + kokoro_tts._zh_model_urls(),
        kokoro_tts.kokoro_cache_root(),
        735,
    )


def melo_source() -> ModelSource:
    url = f"{sherpa_tts._RELEASE}/{sherpa_tts.MELO_MODEL}.tar.bz2"
    return _github("MeloTTS 中英", ordered_urls(github_urls(url)), sherpa_tts.melo_model_dir(), 167)


def silero_source() -> ModelSource:
    from src.core.silero_vad import default_model_path, silero_vad_urls

    return _github("Silero VAD", silero_vad_urls(), default_model_path(), 1)


def format_source(source: ModelSource) -> str:
    """Two short lines: where it comes from, where it lives on disk."""
    size = f" · 约 {source.size_mb} MB" if source.size_mb else ""
    return f"下载：{' → '.join(source.hosts)}{size}\n存放：{source.path}"


def source_tooltip(source: ModelSource) -> str:
    return "\n".join(
        [
            source.title,
            f"原始地址：{source.origin}",
            f"下载源：{' → '.join(source.hosts)}（失败或校验不通过时自动换下一个）",
            f"存放目录：{source.path}",
        ]
    )
