"""Kokoro TTS via kokoro-onnx (local ONNX, PCM16 mono 16 kHz output)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np

from src.engines.pipeline.sentence_split import (
    ensure_terminal_punct,
    is_punct_only,
    split_sentences,
)
from src.utils.audio_utils import float32_to_pcm16, resample
from src.utils.logger import logger

_TARGET_SR = 16000
_CACHE_ROOT = Path.home() / ".cache" / "translator_intime" / "kokoro"
_LOG_INTERVAL_SEC = 30.0
_INTER_SENTENCE_SILENCE_MS = 120
_DOWNLOAD_CHUNK_BYTES = 1024 * 256
_DOWNLOAD_TIMEOUT_SECONDS = 120

_GH_MIRRORS = (
    "https://ghfast.top/https://github.com",
    "https://gh-proxy.com/https://github.com",
    "https://github.com",
)


def _mirror(url: str) -> list[str]:
    """Return mirrored download URLs for GitHub release assets."""
    marker = "https://github.com"
    if not url.startswith(marker):
        return [url]
    suffix = url[len(marker) :]
    out: list[str] = []
    for m in _GH_MIRRORS:
        out.append(m.rstrip("/") + suffix)
    return out


# English / multilingual v1.0
_EN_MODEL_URLS = _mirror(
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
_EN_VOICES_URLS = _mirror(
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)
# Chinese v1.1-zh
_ZH_MODEL_URLS = _mirror(
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/kokoro-v1.1-zh.onnx"
)
_ZH_VOICES_URLS = _mirror(
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/voices-v1.1-zh.bin"
)

DEFAULT_VOICE_EN = "af_bella"
DEFAULT_VOICE_ZH = "zf_xiaoxiao"
_ZH_VOICE_FALLBACKS = (
    "zf_xiaoxiao",
    "zf_001",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoyi",
)


def _silence_pcm(ms: int, *, sample_rate: int = _TARGET_SR) -> bytes:
    samples = max(0, int(sample_rate * ms / 1000.0))
    return b"\x00\x00" * samples


class KokoroOnnxTts:
    """Lazy-download Kokoro ONNX TTS.

    ``configured`` is True when at least the English model is loaded.
    Chinese requires the zh model; if missing, synthesize returns None for zh
    so AutoTts can fall back to edge-tts (never English voice reading Chinese).
    """

    def __init__(
        self,
        *,
        cache_dir: Path | str | None = None,
        voice_en: str = DEFAULT_VOICE_EN,
        voice_zh: str = DEFAULT_VOICE_ZH,
        speed: float = 0.92,
        auto_download: bool = True,
        device_preference: str = "auto",
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_ROOT
        self._voice_en = (voice_en or DEFAULT_VOICE_EN).strip() or DEFAULT_VOICE_EN
        self._voice_zh = (voice_zh or DEFAULT_VOICE_ZH).strip() or DEFAULT_VOICE_ZH
        self._speed = float(max(0.7, min(1.3, speed)))
        self._auto_download = auto_download
        self._device_preference = (device_preference or "auto").strip().lower()
        self._started = False
        self._ready = False
        self._failed = False
        self._loading = False
        self._zh_ready = False
        self._last_err_at = 0.0
        self._zh_missing_logged = False
        self._lock = threading.Lock()
        self._kokoro_en: Any = None
        self._kokoro_zh: Any = None
        self._zh_g2p: Any = None
        self._en_voices: set[str] = set()
        self._zh_voices: set[str] = set()

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def warming_up(self) -> bool:
        return self._loading and not self._ready and not self._failed

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def start(self) -> None:
        self._started = True
        self.start_loading()

    def stop(self) -> None:
        self._started = False

    def start_loading(self) -> None:
        with self._lock:
            if self._ready or self._failed or self._loading:
                return
            self._loading = True
        logger.info("正在下载 Kokoro…")
        thread = threading.Thread(
            target=self._load_background,
            name="kokoro-tts-loader",
            daemon=True,
        )
        thread.start()

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text or is_punct_only(text) or not self._started:
            return None
        if not self._ready:
            if not self._failed:
                self.start_loading()
            return None
        lang = (language or "en")[:2].lower()
        # Never synthesize Chinese with the English model — let AutoTts use edge-tts.
        if lang == "zh" and not self._zh_ready:
            if not self._zh_missing_logged:
                self._zh_missing_logged = True
                logger.warning(
                    "Kokoro 中文模型不可用，跳过 EN 音色朗读中文（将回退 edge-tts）"
                )
            return None
        try:
            sentences = split_sentences(text, min_chars=1)
            if not sentences:
                sentences = [text]
            chunks: list[bytes] = []
            for _i, sentence in enumerate(sentences):
                if is_punct_only(sentence):
                    continue
                unit = ensure_terminal_punct(sentence)
                pcm = self._synthesize_locked(unit, lang=lang)
                if not pcm:
                    continue
                if chunks:
                    chunks.append(_silence_pcm(_INTER_SENTENCE_SILENCE_MS))
                chunks.append(pcm)
            if not chunks:
                return None
            return b"".join(chunks)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"Kokoro synthesize failed: {exc}")
            return None

    def _synthesize_locked(self, text: str, *, lang: str) -> bytes | None:
        with self._lock:
            if lang == "zh" and self._kokoro_zh is not None:
                samples, sr = self._create_zh(text)
            elif lang == "zh":
                return None
            elif self._kokoro_en is not None:
                voice = self._pick_voice(lang)
                samples, sr = self._kokoro_en.create(
                    text, voice=voice, speed=self._speed
                )
            else:
                return None
        return self._to_pcm16(samples, int(sr))

    def _create_zh(self, text: str) -> tuple[Any, int]:
        assert self._kokoro_zh is not None
        voice = self._pick_zh_voice()
        # Prefer misaki G2P when available (recommended for v1.1-zh).
        if self._zh_g2p is not None:
            phonemes, _ = self._zh_g2p(text)
            return self._kokoro_zh.create(
                phonemes, voice=voice, speed=self._speed, is_phonemes=True
            )
        try:
            return self._kokoro_zh.create(
                text, voice=voice, speed=self._speed, lang="cmn"
            )
        except TypeError:
            return self._kokoro_zh.create(text, voice=voice, speed=self._speed)

    def _pick_voice(self, lang: str) -> str:
        if lang == "zh":
            return self._pick_zh_voice()
        if self._voice_en in self._en_voices or not self._en_voices:
            return self._voice_en
        if "af_bella" in self._en_voices:
            return "af_bella"
        if "af_heart" in self._en_voices:
            return "af_heart"
        return next(iter(sorted(self._en_voices)), self._voice_en)

    def _pick_zh_voice(self) -> str:
        voices = self._zh_voices or self._en_voices
        if self._voice_zh in voices:
            return self._voice_zh
        for cand in _ZH_VOICE_FALLBACKS:
            if cand in voices:
                return cand
        zf = sorted(v for v in voices if v.startswith("zf_"))
        if zf:
            return zf[0]
        return self._voice_zh

    @staticmethod
    def _to_pcm16(samples: Any, sr: int) -> bytes | None:
        data = np.asarray(samples, dtype=np.float32)
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        if data.size == 0:
            return None
        data = resample(data, int(sr), _TARGET_SR)
        return float32_to_pcm16(data)

    def _load_background(self) -> None:
        ok = False
        try:
            ok = self._load()
        except Exception as exc:
            logger.warning(f"Kokoro 加载失败: {exc}")
            ok = False
        with self._lock:
            self._ready = ok
            self._failed = not ok
            self._loading = False
        if ok:
            zh = "含中文" if self._zh_ready else "仅英文（中文将回退 edge-tts）"
            logger.info(
                f"Kokoro TTS 已就绪（{zh}）speed={self._speed:.2f}: {self._cache_dir}"
            )
        else:
            logger.warning("Kokoro 不可用，将尝试 edge-tts 回退（若已安装）")

    def _load(self) -> bool:
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            logger.warning(
                "未安装 kokoro-onnx，请执行: pip install kokoro-onnx"
            )
            return False

        # kokoro-onnx 内部用 onnxruntime.InferenceSession 但不暴露 provider 参数,
        # 通过临时 monkey-patch 注入 CUDA provider 支持.
        providers = self._resolve_providers()
        ort_patched = False
        if providers:
            import onnxruntime as ort

            _orig_init = ort.InferenceSession.__init__

            def _patched_init(self_s, *args, **kwargs):
                kwargs.setdefault("providers", providers)
                return _orig_init(self_s, *args, **kwargs)

            ort.InferenceSession.__init__ = _patched_init
            ort_patched = True
            logger.info(f"Kokoro TTS providers={providers}")

        try:
            en_model = self._cache_dir / "kokoro-v1.0.onnx"
            en_voices = self._cache_dir / "voices-v1.0.bin"
            zh_model = self._cache_dir / "kokoro-v1.1-zh.onnx"
            zh_voices = self._cache_dir / "voices-v1.1-zh.bin"

            if not self._ensure_file(en_model, _EN_MODEL_URLS):
                return False
            if not self._ensure_file(en_voices, _EN_VOICES_URLS):
                return False

            # Best-effort Chinese assets (optional).
            zh_ok = self._ensure_file(zh_model, _ZH_MODEL_URLS, required=False)
            zh_ok = zh_ok and self._ensure_file(zh_voices, _ZH_VOICES_URLS, required=False)

            try:
                kokoro_en = Kokoro(str(en_model), str(en_voices))
                en_voice_ids = self._list_voices(kokoro_en)
            except Exception as exc:
                logger.warning(f"Kokoro 英文模型加载失败: {exc}")
                return False

            kokoro_zh = None
            zh_voice_ids: set[str] = set()
            zh_g2p = None
            if zh_ok and zh_model.exists() and zh_voices.exists():
                try:
                    kokoro_zh = Kokoro(str(zh_model), str(zh_voices))
                    zh_voice_ids = self._list_voices(kokoro_zh)
                    try:
                        from misaki import zh as misaki_zh

                        zh_g2p = misaki_zh.ZHG2P()
                    except Exception:
                        zh_g2p = None
                        logger.warning(
                            "未安装 misaki[zh]，中文韵律可能偏硬；"
                            "建议: pip install 'misaki[zh]'"
                        )
                except Exception as exc:
                    logger.warning(f"Kokoro 中文模型加载失败，中文将回退 edge-tts: {exc}")
                    kokoro_zh = None

            with self._lock:
                self._kokoro_en = kokoro_en
                self._kokoro_zh = kokoro_zh
                self._zh_g2p = zh_g2p
                self._en_voices = en_voice_ids
                self._zh_voices = zh_voice_ids
                self._zh_ready = kokoro_zh is not None
            return True
        finally:
            # 恢复原始 InferenceSession, 避免 patch 泄漏到其他模块
            if ort_patched:
                ort.InferenceSession.__init__ = _orig_init

    def _resolve_providers(self) -> list[str]:
        """根据 preference 返回 onnxruntime providers 列表."""
        pref = self._device_preference
        if pref == "cpu":
            return ["CPUExecutionProvider"]
        # auto 或 cuda
        try:
            import onnxruntime as ort

            available = ort.get_available_providers()
            if any("CUDA" in p for p in available):
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        except Exception:
            pass
        if pref == "cuda":
            logger.warning("用户选择了 CUDA 但 onnxruntime 无 CUDA provider，回退 CPU")
        return ["CPUExecutionProvider"]

    @staticmethod
    def _list_voices(kokoro: Any) -> set[str]:
        try:
            voices = getattr(kokoro, "voices", None)
            if voices is None and hasattr(kokoro, "get_voices"):
                voices = kokoro.get_voices()
            if isinstance(voices, dict):
                return set(voices.keys())
            if voices is not None:
                return set(voices)
        except Exception:
            pass
        return set()

    def _ensure_file(
        self, path: Path, urls: str | list[str], *, required: bool = True
    ) -> bool:
        if path.exists() and path.stat().st_size > 0:
            return True
        if not self._auto_download:
            if required:
                logger.warning(f"Kokoro 文件缺失且禁用下载: {path}")
            return False
        url_list = [urls] if isinstance(urls, str) else list(urls)
        from src.utils.proxy_env import prepare_model_download_env, without_proxy

        prepare_model_download_env()
        path.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        with without_proxy():
            for url in url_list:
                try:
                    logger.info(f"正在下载 Kokoro: {url}")
                    tmp = path.with_suffix(path.suffix + ".part")
                    self._download_url(url, tmp)
                    tmp.replace(path)
                    logger.info(f"Kokoro 已保存: {path}")
                    return True
                except Exception as exc:
                    last_exc = exc
                    logger.warning(f"Kokoro 镜像失败（{url}）: {exc}")
        if required:
            logger.warning(f"Kokoro 下载失败: {last_exc}")
            err = str(last_exc).lower() if last_exc else ""
            if "socks" in err or "proxy" in err:
                logger.warning(
                    "代理相关失败提示: 请改用 Clash HTTP 端口 "
                    "(http://127.0.0.1:7890)，或 pip install PySocks"
                    "；或取消 TRANSLATOR_INTIME_USE_PROXY 以直连下载"
                )
            return False
        logger.info(f"Kokoro 可选资源下载跳过/失败: {last_exc}")
        return False

    @staticmethod
    def _download_url(url: str, target: Path) -> None:
        """Download atomically with a timeout and Content-Length validation."""
        target.unlink(missing_ok=True)
        request = Request(url, headers={"User-Agent": "translator-intime/1.0"})
        with urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            expected = int(content_length) if content_length and content_length.isdigit() else None
            written = 0
            with target.open("wb") as output:
                while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                    output.write(chunk)
                    written += len(chunk)
        if expected is not None and written != expected:
            target.unlink(missing_ok=True)
            raise URLError(f"下载长度不匹配: got={written} expected={expected}")
        if written <= 0:
            target.unlink(missing_ok=True)
            raise URLError("下载文件为空")
