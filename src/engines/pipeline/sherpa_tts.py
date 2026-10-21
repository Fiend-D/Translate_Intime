"""Local MeloTTS (zh + en code-switching) via sherpa-onnx OfflineTts."""

from __future__ import annotations

import shutil
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from src.engines.pipeline.sentence_split import is_punct_only
from src.utils import download_progress
from src.utils.audio_utils import float32_to_pcm16, resample
from src.utils.file_integrity import matches_sha256
from src.utils.http_download import DownloadError
from src.utils.http_download import fetch_resumable as _fetch_resumable
from src.utils.logger import logger
from src.utils.proxy_env import use_proxy_for_downloads
from src.utils.resource_paths import model_resource_root

_TARGET_SR = 16000
_LOG_INTERVAL_SEC = 30.0
MELO_MODEL = "vits-melo-tts-zh_en"
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
_MIRRORS = ("https://gh-proxy.com/", "", "https://ghfast.top/")
_RULE_FSTS = ("phone.fst", "date.fst", "number.fst")
_ARCHIVE_SHA256 = {
    MELO_MODEL: "e58351ed7149f290a54534538badd4077cdbe6fddc964b24d0bee870415d1514",
}


def tts_cache_root() -> Path:
    return model_resource_root() / "tts"


def melo_model_dir(root: Path | None = None) -> Path:
    return (root or tts_cache_root()) / MELO_MODEL


# The upstream archive ships model.int8.onnx as a ~130-byte Git LFS pointer.
_MIN_MODEL_BYTES = 1_000_000


def _melo_model_file(model_dir: Path) -> Path | None:
    """Real ONNX weights, preferring int8 when it is not a placeholder."""
    candidates = sorted(model_dir.glob("*.onnx"), key=lambda p: ("int8" not in p.name, p.name))
    for path in candidates:
        try:
            if path.stat().st_size >= _MIN_MODEL_BYTES:
                return path
        except OSError:
            continue
    return None


def melo_model_ready(model_dir: Path | None = None) -> bool:
    model_dir = model_dir or melo_model_dir()
    return _melo_model_file(model_dir) is not None and (model_dir / "tokens.txt").is_file()


def _download_archive(name: str, root: Path) -> bool:
    """Fetch and unpack ``name``.tar.bz2, trying each GitHub mirror in turn."""
    archive = root / f"{name}.tar.bz2.part"
    root.mkdir(parents=True, exist_ok=True)
    key = f"tts:{name}"
    download_progress.begin(key, f"MeloTTS · {name}", dest=root / name)
    for mirror in _MIRRORS:
        url = f"{mirror}{_RELEASE}/{name}.tar.bz2"
        keep_partial = False
        try:
            logger.info(f"下载 TTS 模型: {url}")
            download_progress.set_source(key, url)
            _fetch_resumable(url, archive, progress_key=key, trust_env=use_proxy_for_downloads())
            expected = _ARCHIVE_SHA256.get(name)
            if expected and not matches_sha256(archive, expected, cache=False):
                raise ValueError("SHA-256 校验失败（镜像返回的文件不可信）")
            # Mirrors sometimes answer 200 with an HTML/text error page.
            with tarfile.open(archive, "r:bz2") as tar:
                tar.extractall(root, filter="data")
            if melo_model_ready(root / name):
                archive.unlink(missing_ok=True)
                download_progress.finish(key, ok=True)
                return True
            logger.warning(f"TTS 模型包内容不完整: {url}")
        except (httpx.TransportError, DownloadError) as exc:
            # Same release asset on every mirror, so the next one can continue the bytes
            keep_partial = True
            logger.warning(f"TTS 模型下载失败 ({url}): {exc}")
        except Exception as exc:
            logger.warning(f"TTS 模型下载失败 ({url}): {exc}")
        if not keep_partial:
            archive.unlink(missing_ok=True)
        shutil.rmtree(root / name, ignore_errors=True)
    archive.unlink(missing_ok=True)
    download_progress.finish(key, ok=False, error="所有镜像均失败")
    return False


class MeloSherpaTts:
    """sherpa-onnx VITS MeloTTS: one speaker, reads mixed Chinese/English sentences."""

    def __init__(
        self,
        *,
        speed: float = 1.0,
        num_threads: int = 2,
        model_dir: Path | None = None,
    ) -> None:
        self._speed = float(speed or 1.0)
        self._num_threads = max(1, int(num_threads))
        self._model_dir = model_dir or melo_model_dir()
        self._tts: Any | None = None
        self._started = False
        self._loading = False
        self._failed = False
        self._lock = threading.Lock()
        self._last_err_at = 0.0

    @property
    def configured(self) -> bool:
        return self._tts is not None

    @property
    def warming_up(self) -> bool:
        return self._loading

    def start(self) -> None:
        self._started = True
        with self._lock:
            if self._tts is not None or self._loading or self._failed:
                return
            self._loading = True
        threading.Thread(target=self._load_background, name="melo-tts-load", daemon=True).start()

    def stop(self) -> None:
        self._started = False

    def _load_background(self) -> None:
        try:
            self._tts = self._load()
            self._failed = self._tts is None
        finally:
            self._loading = False

    def _load(self) -> Any | None:
        try:
            import sherpa_onnx
        except ImportError:
            logger.warning("未安装 sherpa-onnx，请执行: pip install sherpa-onnx")
            return None
        model_dir = self._model_dir
        if not melo_model_ready(model_dir) and not _download_archive(
            model_dir.name, model_dir.parent
        ):
            return None
        model = _melo_model_file(model_dir)
        if model is None:
            return None
        dict_dir = model_dir / "dict"
        rule_fsts = ",".join(str(model_dir / f) for f in _RULE_FSTS if (model_dir / f).is_file())
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(model),
                    lexicon=str(model_dir / "lexicon.txt"),
                    tokens=str(model_dir / "tokens.txt"),
                    dict_dir=str(dict_dir) if dict_dir.is_dir() else "",
                ),
                num_threads=self._num_threads,
                provider="cpu",
            ),
            rule_fsts=rule_fsts,
            max_num_sentences=1,
        )
        try:
            tts = sherpa_onnx.OfflineTts(config)
        except Exception as exc:
            logger.warning(f"MeloTTS 加载失败: {exc}")
            return None
        logger.info(f"MeloTTS 已就绪: {model_dir}")
        return tts

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        del language
        text = (text or "").strip()
        tts = self._tts
        if not text or is_punct_only(text) or not self._started or tts is None:
            return None
        try:
            audio = tts.generate(text, sid=0, speed=self._speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            if not samples.size:
                return None
            return float32_to_pcm16(resample(samples, int(audio.sample_rate), _TARGET_SR))
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"MeloTTS 合成失败: {exc}")
            return None


__all__ = ["MELO_MODEL", "MeloSherpaTts", "melo_model_dir", "melo_model_ready", "tts_cache_root"]
