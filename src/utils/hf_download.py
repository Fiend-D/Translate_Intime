"""Hugging Face downloads with an explicit mirror order and progress tracking.

``huggingface_hub`` reads ``HF_ENDPOINT`` only once at import time, so setting the
variable around a call has no effect; the endpoint is passed per call instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.utils import download_progress
from src.utils.logger import logger

DEFAULT_HF_ENDPOINTS = ("https://hf-mirror.com", "https://huggingface.co")

# Known repos download a fixed commit so a pushed/rewritten ``main`` cannot swap the weights.
PINNED_REVISIONS: dict[str, str] = {
    "JustFrederik/nllb-200-distilled-600M-ct2-int8": "302d78f00e6fdb50a1064059df7c392b735e9d05",
    "mijuanlo/nllb-200-distilled-1.3B-int8-ct2": "6eee5eda03ff1441d2a6117d34a02e44504ce321",
    "Systran/faster-whisper-medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "Systran/faster-whisper-large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "Qwen/Qwen3-ASR-0.6B-hf": "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
    "Qwen/Qwen3-ASR-1.7B-hf": "bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
    "tencent/Hy-MT2-1.8B-GGUF": "a0c709d9fac510f2c807aa3af52872340dc37a4a",
}


def revision_for(repo_id: str) -> str | None:
    return PINNED_REVISIONS.get(repo_id)


def hf_endpoints() -> list[str]:
    """User ``HF_ENDPOINT`` first (if set), then the mirror, then the official hub."""
    ordered: list[str] = []
    for endpoint in (os.environ.get("HF_ENDPOINT", ""), *DEFAULT_HF_ENDPOINTS):
        endpoint = endpoint.strip().rstrip("/")
        if endpoint and endpoint not in ordered:
            ordered.append(endpoint)
    return ordered


def _download(
    fetch: Any,
    *,
    repo_id: str,
    local_dir: Path,
    label: str,
    key: str,
    total_mb: int,
    url_suffix: str = "",
    **kwargs: Any,
) -> str:
    from src.utils.proxy_env import prepare_model_download_env, without_proxy

    prepare_model_download_env()
    local_dir.mkdir(parents=True, exist_ok=True)
    download_progress.begin(
        key,
        label,
        dest=local_dir,
        total=total_mb * 1_000_000 if total_mb else None,
        watch_dir=local_dir,
    )
    last_exc: Exception | None = None
    for endpoint in hf_endpoints():
        url = f"{endpoint}/{repo_id}{url_suffix}"
        download_progress.set_source(key, url)
        logger.info(f"正在下载 {label}: {url} → {local_dir}")
        try:
            with without_proxy():
                result = fetch(
                    repo_id=repo_id,
                    local_dir=str(local_dir),
                    endpoint=endpoint,
                    revision=revision_for(repo_id),
                    **kwargs,
                )
        except Exception as exc:
            last_exc = exc
            logger.warning(f"{label} 下载失败（{endpoint}）: {exc}")
            continue
        download_progress.finish(key, ok=True)
        return str(result)
    download_progress.finish(key, ok=False, error=str(last_exc or "所有下载源均失败"))
    raise RuntimeError(f"{label} 下载失败: {last_exc}")


def snapshot(
    repo_id: str,
    local_dir: Path,
    *,
    label: str,
    key: str,
    total_mb: int = 0,
    allow_patterns: list[str] | None = None,
) -> str:
    """Download a whole repo into ``local_dir``; raises after every endpoint fails."""
    from huggingface_hub import snapshot_download

    return _download(
        snapshot_download,
        repo_id=repo_id,
        local_dir=local_dir,
        label=label,
        key=key,
        total_mb=total_mb,
        allow_patterns=allow_patterns,
    )


def single_file(
    repo_id: str,
    filename: str,
    local_dir: Path,
    *,
    label: str,
    key: str,
    total_mb: int = 0,
) -> str:
    """Download one file of a repo into ``local_dir``; raises after every endpoint fails."""
    from huggingface_hub import hf_hub_download

    return _download(
        hf_hub_download,
        repo_id=repo_id,
        local_dir=local_dir,
        label=label,
        key=key,
        total_mb=total_mb,
        url_suffix=f"/resolve/{revision_for(repo_id) or 'main'}/{filename}",
        filename=filename,
    )
