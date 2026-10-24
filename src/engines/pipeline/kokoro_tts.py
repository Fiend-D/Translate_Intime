"""Kokoro TTS via kokoro-onnx (local ONNX, PCM16 mono 16 kHz output)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError

import numpy as np

from src.engines.pipeline.sentence_split import (
    ensure_terminal_punct,
    is_punct_only,
    split_sentences,
)
from src.models.config import KOKORO_LEGACY_ZH_VOICES
from src.utils import download_progress
from src.utils.audio_utils import float32_to_pcm16, resample
from src.utils.file_integrity import matches_sha256
from src.utils.http_download import fetch_resumable
from src.utils.logger import logger
from src.utils.model_fetch import github_urls, huggingface_urls, modelscope_url, ordered_urls
from src.utils.resource_paths import default_model_resource_root, model_resource_root

_TARGET_SR = 16000
_LEGACY_CACHE_ROOT = Path.home() / ".cache" / "translator_intime" / "kokoro"


def kokoro_cache_root() -> Path:
    """``resource/tts/kokoro`` like other models; keep using a completed legacy cache."""
    root = model_resource_root()
    legacy_ok = root == default_model_resource_root()
    if legacy_ok and (_LEGACY_CACHE_ROOT / "kokoro-v1.0.onnx").is_file():
        return _LEGACY_CACHE_ROOT
    return root / "tts" / "kokoro"


_LOG_INTERVAL_SEC = 30.0
_INTER_SENTENCE_SILENCE_MS = 120
_DOWNLOAD_CHUNK_BYTES = 1024 * 256
_DOWNLOAD_TIMEOUT_SECONDS = 120
_FILE_SHA256: dict[str, str | tuple[str, ...]] = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    # Re-exported upstream on 2026-08-18 (float speed input, needs kokoro-onnx>=0.6.1).
    "kokoro-v1.1-zh.onnx": (
        "859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326",
        "eefec708cbc7aba8e8129b5c2f7cb92e1fe7d281af1e1dd451592d9ff0714a0d",
    ),
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    "voices-v1.1-zh.bin": "14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd",
    "config-v1.1-zh.json": "bc333efa5ce4ceff433c8c8e5d027a1eca0166001e4e4a62bea2d26ff7a46890",
}

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download"
# The zh model's zhuyin vocab; kokoro-onnx's built-in v1.0 vocab drops nearly every phoneme.
_ZH_VOCAB_REPO = "hexgrad/Kokoro-82M-v1.1-zh"
_ZH_VOCAB_REVISION = "01e7505bd6a7a2ac4975463114c3a7650a9f7218"


def _release_urls(asset: str, *modelscope: tuple[str, str]) -> list[str]:
    """A GitHub release asset plus byte-identical ModelScope copies (repo, path)."""
    return ordered_urls(
        [
            *(modelscope_url(repo, path) for repo, path in modelscope),
            *github_urls(f"{_RELEASE}/{asset}"),
        ]
    )


def _en_model_urls() -> list[str]:
    return _release_urls(
        "model-files-v1.0/kokoro-v1.0.onnx", ("aistoy/kokoro-onnx-v_1_0", "kokoro-v1.0.onnx")
    )


def _en_voices_urls() -> list[str]:
    return _release_urls(
        "model-files-v1.0/voices-v1.0.bin", ("aistoy/kokoro-onnx-v_1_0", "voices-v1.0.bin")
    )


def _zh_model_urls() -> list[str]:
    return _release_urls("model-files-v1.1/kokoro-v1.1-zh.onnx")


def _zh_voices_urls() -> list[str]:
    return _release_urls("model-files-v1.1/voices-v1.1-zh.bin")


def _zh_vocab_urls() -> list[str]:
    return ordered_urls(
        [
            modelscope_url(_ZH_VOCAB_REPO, "config.json"),
            modelscope_url("AI-ModelScope/Kokoro-82M-v1.1-zh", "config.json"),
            *huggingface_urls(_ZH_VOCAB_REPO, "config.json", _ZH_VOCAB_REVISION),
        ]
    )


DEFAULT_VOICE_EN = "af_bella"
DEFAULT_VOICE_ZH = "zf_001"


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
        self._cache_dir = Path(cache_dir) if cache_dir else kokoro_cache_root()
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
        logger.info("正在准备 Kokoro（缺文件时自动下载）…")
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
        # Never synthesize Chinese with the English model — let AutoTts use MeloTTS.
        if lang == "zh" and not self._zh_ready:
            if not self._zh_missing_logged:
                self._zh_missing_logged = True
                logger.warning("Kokoro 中文不可用，跳过 EN 音色朗读中文（改用 MeloTTS）")
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
                samples, sr = self._kokoro_en.create(text, voice=voice, speed=self._speed)
            else:
                return None
        return self._to_pcm16(samples, int(sr))

    def _create_zh(self, text: str) -> tuple[Any, int]:
        assert self._kokoro_zh is not None
        assert self._zh_g2p is not None
        voice = self._pick_zh_voice()
        phonemes, _ = self._zh_g2p(text)
        return cast(
            tuple[Any, int],
            self._kokoro_zh.create(phonemes, voice=voice, speed=self._speed, is_phonemes=True),
        )

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
        voices = self._zh_voices
        wanted = KOKORO_LEGACY_ZH_VOICES.get(self._voice_zh, self._voice_zh)
        if not voices or wanted in voices:
            return wanted
        # Keep the requested gender rather than collapsing every voice to one speaker.
        prefix = "zm_" if wanted.startswith("zm_") else "zf_"
        same_gender = sorted(v for v in voices if v.startswith(prefix))
        if same_gender:
            return same_gender[0]
        return DEFAULT_VOICE_ZH if DEFAULT_VOICE_ZH in voices else sorted(voices)[0]

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
            logger.info(f"Kokoro TTS 已就绪（{zh}）speed={self._speed:.2f}: {self._cache_dir}")
        else:
            logger.warning("Kokoro 不可用，将尝试 edge-tts 回退（若已安装）")

    def _load(self) -> bool:
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            logger.warning("未安装 kokoro-onnx，请执行: pip install kokoro-onnx")
            return False

        # kokoro-onnx 内部用 onnxruntime.InferenceSession 但不暴露 provider 参数,
        # 通过临时 monkey-patch 注入 CUDA provider 支持.
        providers = self._resolve_providers()
        ort_patched = False
        if providers:
            import onnxruntime as ort

            _orig_init = ort.InferenceSession.__init__

            def _patched_init(self_s: Any, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("providers", providers)
                _orig_init(self_s, *args, **kwargs)

            ort.InferenceSession.__init__ = _patched_init
            ort_patched = True
            logger.info(f"Kokoro TTS providers={providers}")

        try:
            en_model = self._cache_dir / "kokoro-v1.0.onnx"
            en_voices = self._cache_dir / "voices-v1.0.bin"
            zh_model = self._cache_dir / "kokoro-v1.1-zh.onnx"
            zh_voices = self._cache_dir / "voices-v1.1-zh.bin"
            zh_vocab = self._cache_dir / "config-v1.1-zh.json"

            if not self._ensure_file(en_model, _en_model_urls()):
                return False
            if not self._ensure_file(en_voices, _en_voices_urls()):
                return False

            # Best-effort Chinese assets (optional).
            zh_ok = self._ensure_file(zh_model, _zh_model_urls(), required=False)
            zh_ok = zh_ok and self._ensure_file(zh_voices, _zh_voices_urls(), required=False)
            zh_ok = zh_ok and self._ensure_file(zh_vocab, _zh_vocab_urls(), required=False)

            try:
                kokoro_en = Kokoro(str(en_model), str(en_voices))
                en_voice_ids = self._list_voices(kokoro_en)
            except Exception as exc:
                logger.warning(f"Kokoro 英文模型加载失败: {exc}")
                return False

            kokoro_zh = None
            zh_voice_ids: set[str] = set()
            zh_g2p = None
            if zh_ok:
                try:
                    from misaki import zh as misaki_zh

                    # v1.1-zh was trained on misaki's zhuyin+tone set; espeak "cmn"
                    # phonemes make it drawl with a foreign accent.
                    zh_g2p = misaki_zh.ZHG2P(version="1.1")
                    kokoro_zh = Kokoro(str(zh_model), str(zh_voices), vocab_config=str(zh_vocab))
                    zh_voice_ids = self._list_voices(kokoro_zh)
                except ImportError:
                    zh_g2p = None
                    logger.warning(
                        "未安装 misaki[zh]，Kokoro 中文不可用，中文改用 MeloTTS；"
                        "安装: pip install 'misaki[zh]'"
                    )
                except Exception as exc:
                    logger.warning(f"Kokoro 中文模型加载失败，中文改用 MeloTTS: {exc}")
                    kokoro_zh = None
                    zh_g2p = None

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

    def _ensure_file(self, path: Path, urls: str | list[str], *, required: bool = True) -> bool:
        expected_sha256 = _FILE_SHA256.get(path.name, ())
        if expected_sha256 and matches_sha256(path, expected_sha256):
            return True
        if not self._auto_download:
            if required or path.exists():
                state = "校验不通过" if path.exists() else "缺失"
                logger.warning(f"Kokoro 文件{state}且禁用下载: {path}")
            return False
        path.unlink(missing_ok=True)
        url_list = [urls] if isinstance(urls, str) else list(urls)
        from src.utils.proxy_env import prepare_model_download_env, use_proxy_for_downloads

        prepare_model_download_env()
        path.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        key = f"tts:kokoro:{path.name}"
        download_progress.begin(key, f"Kokoro · {path.name}", dest=path)
        # Every mirror serves the same asset, so a partial file carries over between them.
        tmp = path.with_suffix(path.suffix + ".part")
        for url in url_list:
            try:
                logger.info(f"正在下载 Kokoro: {url}")
                download_progress.set_source(key, url)
                fetch_resumable(
                    url,
                    tmp,
                    attempts=6,
                    progress_key=key,
                    trust_env=use_proxy_for_downloads(),
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Kokoro 镜像失败（{url}）: {exc}")
                continue
            if tmp.stat().st_size <= 0 or (
                expected_sha256 and not matches_sha256(tmp, expected_sha256, cache=False)
            ):
                tmp.unlink(missing_ok=True)
                last_exc = URLError(f"SHA-256 mismatch for {path.name}")
                logger.warning(f"Kokoro 校验失败（{url}），换下一个下载源")
                continue
            tmp.replace(path)
            logger.info(f"Kokoro 已保存: {path}")
            download_progress.finish(key, ok=True)
            return True
        download_progress.finish(key, ok=False, error=str(last_exc or ""))
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
