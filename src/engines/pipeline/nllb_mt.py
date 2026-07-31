"""NLLB-200 machine translation via CTranslate2 (local offline)."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from src.utils.logger import logger
from src.utils.resource_paths import model_resource_root

DEFAULT_NLLB_MODEL = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
TOKENIZER_ID = "facebook/nllb-200-distilled-600M"
# Development uses project-local resources; packaged builds use a persistent cache.
_CACHE_ROOT = model_resource_root() / "nllb"
_LICENSE_NOTE = "NLLB-200 许可证为 CC-BY-NC（非商业研究用途）。请仅在合规场景下使用。"
_LANG_MAP = {
    "zh": "zho_Hans",
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
}
_LOG_INTERVAL_SEC = 30.0


def nllb_lang_code(lang: str) -> str | None:
    """Map app language codes to NLLB Flores-200 codes."""
    key = (lang or "").strip().lower().replace("_", "-")
    if not key:
        return None
    base = key.split("-", 1)[0]
    return _LANG_MAP.get(base)


def model_slug(model_id: str) -> str:
    """Filesystem-safe directory name for a Hugging Face model id."""
    raw = (model_id or DEFAULT_NLLB_MODEL).strip().rstrip("/")
    slug = raw.split("/")[-1] if "/" in raw else raw
    slug = re.sub(r"[^\w.\-]+", "_", slug)
    return slug or "nllb"


def nllb_cache_dir(model_id: str) -> Path:
    return _CACHE_ROOT / model_slug(model_id)


class NllbCt2Mt:
    """Lazy-download NLLB CTranslate2 translator.

    ``configured`` is True only when model weights are loaded and ready.
    While downloading/loading, ``warming_up`` is True and ``translate`` returns None.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_NLLB_MODEL,
        cache_dir: Path | str | None = None,
        auto_download: bool = True,
        device_preference: str = "auto",
    ) -> None:
        self._model_id = (model_id or DEFAULT_NLLB_MODEL).strip()
        self._cache_dir = Path(cache_dir) if cache_dir else nllb_cache_dir(self._model_id)
        self._auto_download = auto_download
        self._device_preference = (device_preference or "auto").strip().lower()
        self._started = False
        self._ready = False
        self._failed = False
        self._loading = False
        self._license_logged = False
        self._last_err_at = 0.0
        self._lock = threading.Lock()
        self._translator: Any = None
        self._tokenizer: Any = None
        self._device = "cpu"
        self._compute_type = "int8"

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def warming_up(self) -> bool:
        return self._loading and not self._ready and not self._failed

    @property
    def model_id(self) -> str:
        return self._model_id

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
        logger.info("正在下载 NLLB…" if not self._model_files_ready() else "正在加载 NLLB…")
        thread = threading.Thread(
            target=self._load_background,
            name="nllb-mt-loader",
            daemon=True,
        )
        thread.start()

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        text = (text or "").strip()
        if not text or not self._started:
            return None
        src = nllb_lang_code(source_lang)
        tgt = nllb_lang_code(target_lang)
        if src is None or tgt is None:
            return None
        if src == tgt:
            return text
        if not self._ready:
            if not self._failed:
                self.start_loading()
            return None
        try:
            return self._translate_locked(text, src_code=src, tgt_code=tgt)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"NLLB translate failed: {exc}")
            return None

    def translate_sentences(
        self, sentences: list[str], *, source_lang: str, target_lang: str
    ) -> list[str | None]:
        """Translate each sentence independently (better quality than one blob)."""
        return [
            self.translate(s, source_lang=source_lang, target_lang=target_lang) for s in sentences
        ]

    def _translate_locked(self, text: str, *, src_code: str, tgt_code: str) -> str | None:
        with self._lock:
            if self._translator is None or self._tokenizer is None:
                return None
            tokenizer = self._tokenizer
            translator = self._translator
            # transformers NLLB tokenizer uses src_lang for encoding.
            if hasattr(tokenizer, "src_lang"):
                tokenizer.src_lang = src_code
            token_ids = tokenizer.encode(text)
            source_tokens = tokenizer.convert_ids_to_tokens(token_ids)
            # Shorter sentences benefit from a slightly wider beam.
            beam_size = 5 if len(text) < 40 else 4
            results = translator.translate_batch(
                [source_tokens],
                target_prefix=[[tgt_code]],
                beam_size=beam_size,
                max_decoding_length=256,
            )
            if not results or not results[0].hypotheses:
                return None
            hyp = list(results[0].hypotheses[0])
            if hyp and hyp[0] == tgt_code:
                hyp = hyp[1:]
            out_ids = tokenizer.convert_tokens_to_ids(hyp)
            out = tokenizer.decode(out_ids, skip_special_tokens=True)
            out = (out or "").strip()
            return out or None

    def _load_background(self) -> None:
        ok = False
        try:
            ok = self._load()
        except Exception as exc:
            logger.warning(f"NLLB 加载失败: {exc}")
            ok = False
        with self._lock:
            self._ready = ok
            self._failed = not ok
            self._loading = False
        if ok:
            if not self._license_logged:
                self._license_logged = True
                logger.info(_LICENSE_NOTE)
            logger.info(f"NLLB 已就绪: {self._model_id} ({self._device}/{self._compute_type})")
        else:
            logger.warning("NLLB 不可用，将尝试 Argos / MyMemory 回退（若已配置）")

    def _load(self) -> bool:
        try:
            import ctranslate2  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as exc:
            logger.warning(
                f"NLLB 依赖缺失（ctranslate2/transformers）: {exc}；"
                "请 pip install ctranslate2 transformers huggingface_hub sentencepiece"
            )
            return False

        if not self._ensure_model_files():
            return False

        device, compute_type = self._pick_device()
        self._device = device
        self._compute_type = compute_type
        try:
            translator, tokenizer = self._open_translator_and_tokenizer(device, compute_type)
        except Exception as exc:
            logger.warning(f"NLLB Translator/Tokenizer 初始化失败: {exc}")
            msg = str(exc).lower()
            missing_weights = "model.bin" in msg or "unable to open file" in msg
            if not (missing_weights and self._auto_download):
                return False
            logger.info("NLLB 模型不完整（缺少 model.bin），将重新下载")
            if not self._download_model():
                return False
            try:
                translator, tokenizer = self._open_translator_and_tokenizer(device, compute_type)
            except Exception as exc2:
                logger.warning(f"NLLB Translator/Tokenizer 初始化失败: {exc2}")
                return False

        with self._lock:
            self._translator = translator
            self._tokenizer = tokenizer
        return True

    def _open_translator_and_tokenizer(self, device: str, compute_type: str) -> tuple[Any, Any]:
        import warnings

        import ctranslate2
        from transformers import AutoTokenizer

        translator = ctranslate2.Translator(
            str(self._cache_dir),
            device=device,
            compute_type=compute_type,
        )
        # 模型已在本地时, 从本地目录加载 tokenizer, 避免网络请求。
        # NLLB 推理走 CTranslate2，不需要 PyTorch；屏蔽 transformers 的误导告警。
        ready = self._model_files_ready()
        tok_source = str(self._cache_dir) if ready else TOKENIZER_ID
        kwargs: dict[str, Any] = {"local_files_only": ready}
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*PyTorch was not found.*",
                category=UserWarning,
            )
            try:
                # 新版 transformers 对部分 tokenizer.json 正则有误报，需显式修复。
                tokenizer = AutoTokenizer.from_pretrained(
                    tok_source, fix_mistral_regex=True, **kwargs
                )
            except TypeError:
                tokenizer = AutoTokenizer.from_pretrained(tok_source, **kwargs)
        return translator, tokenizer

    def _pick_device(self) -> tuple[str, str]:
        pref = self._device_preference
        try:
            import ctranslate2

            cuda_count = int(ctranslate2.get_cuda_device_count() or 0)
            # 用户强制 CPU
            if pref == "cpu":
                return "cpu", "int8"
            # auto 或 cuda: 无 GPU 时回退 CPU
            if cuda_count <= 0:
                if pref == "cuda":
                    logger.warning("用户选择了 CUDA 但未检测到可用 GPU，回退 CPU")
                return "cpu", "int8"
            cuda_types = set(ctranslate2.get_supported_compute_types("cuda") or [])
            if "int8_float16" in cuda_types:
                return "cuda", "int8_float16"
            if "int8" in cuda_types:
                return "cuda", "int8"
            if "float16" in cuda_types:
                return "cuda", "float16"
            return "cuda", "default"
        except Exception:
            return "cpu", "int8"

    def _model_files_ready(self) -> bool:
        # Require real CTranslate2 weights (>1MB), not just tokenizer/config.
        if not self._cache_dir.is_dir():
            return False
        min_size = 1_000_000
        for path in self._cache_dir.iterdir():
            if not path.is_file():
                continue
            name = path.name
            is_weight = (
                name == "model.bin"
                or name == "model.npz"
                or (name.startswith("model") and name.endswith(".bin"))
            )
            if is_weight and path.stat().st_size > min_size:
                return True
        return False

    def _ensure_model_files(self) -> bool:
        if self._model_files_ready():
            return True
        if self._cache_dir.is_dir() and any(self._cache_dir.iterdir()):
            logger.info("NLLB 模型不完整（缺少 model.bin），将重新下载")
        if not self._auto_download:
            logger.warning(f"NLLB 模型目录为空且禁用下载: {self._cache_dir}")
            return False
        return self._download_model()

    def _download_model(self) -> bool:
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            logger.warning("缺少 huggingface_hub，无法下载 NLLB；请 pip install huggingface_hub")
            return False
        import os

        from src.utils.proxy_env import prepare_model_download_env, without_proxy

        # 提前设置 HF 镜像，避免先走默认源超时再重试；下载后恢复进程环境。
        previous_endpoint = os.environ.get("HF_ENDPOINT")
        if not previous_endpoint:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            logger.info("已预设 HF 镜像: https://hf-mirror.com")

        prepare_model_download_env()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"正在下载 NLLB 模型 {self._model_id} → {self._cache_dir}")

        def _try_download(repo_id: str, cache_dir: Path) -> bool:
            with without_proxy():
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(cache_dir),
                )
            return self._model_files_ready()

        try:
            try:
                if _try_download(self._model_id, self._cache_dir):
                    logger.info(f"NLLB 模型已保存: {self._cache_dir}")
                    return True
            except Exception as exc:
                logger.warning(f"NLLB 模型下载失败: {exc}")
                err = str(exc).lower()
                if "socks" in err or "proxy" in err:
                    logger.warning(
                        "代理相关失败提示: 请改用 Clash HTTP 端口 "
                        "(http://127.0.0.1:7890)，或 pip install PySocks"
                        "；或取消 TRANSLATOR_INTIME_USE_PROXY 以直连下载"
                    )
        finally:
            if previous_endpoint is None:
                os.environ.pop("HF_ENDPOINT", None)
            else:
                os.environ["HF_ENDPOINT"] = previous_endpoint
        # 不再强制降级到 600M；保留用户选择的模型，等下次重试或网络恢复后重新下载。
        return False
