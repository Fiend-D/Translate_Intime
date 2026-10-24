"""LLM-based MT for the economy pipeline.

- ``ChatCompletionMt``: any OpenAI-compatible ``/chat/completions`` endpoint
  (DeepSeek, DashScope compatible-mode, Ollama, LM Studio, llama-server, vLLM …),
  with a short rolling context and glossary hints.
- ``HyMtLocal``: Tencent Hy-MT2 1.8B GGUF in-process via ``llama-cpp-python``.
- ``CachedMt`` / ``FallbackMt``: LRU cache and primary → fallback chaining.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.engines.pipeline.model_manifests import HYMT
from src.engines.pipeline.mt import MtBackend
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.logger import logger

_LOG_INTERVAL_SEC = 30.0

LANGUAGE_NAMES: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "ar": "Arabic",
    "tr": "Turkish",
}

_SYSTEM_PROMPT = (
    "You are a real-time interpreter for live voice chat and game commentary. "
    "Translate the user's {src} speech into natural, concise spoken {tgt}. "
    "The input comes from speech recognition and may contain recognition errors; "
    "infer the intended meaning. Keep names, numbers and game terms accurate. "
    "Output only the translation, without quotes, notes or explanations."
)

HYMT_DEFAULT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
HYMT_DEFAULT_FILE = "Hy-MT2-1.8B-Q4_K_M.gguf"
HYMT_DOWNLOAD_MB = 1133


@dataclass(frozen=True)
class HyMtModel:
    id: str
    repo_id: str
    filename: str
    size_mb: int
    label: str
    note: str


HYMT_MODELS: tuple[HyMtModel, ...] = (
    HyMtModel(
        "hy-mt2-1.8b",
        HYMT_DEFAULT_REPO,
        HYMT_DEFAULT_FILE,
        HYMT_DOWNLOAD_MB,
        "Hy-MT2 1.8B（推荐）",
        "约 1.1 GB；CPU 上每句约 1 秒，质量远好于 NLLB",
    ),
    HyMtModel(
        "hy-mt2-7b",
        "tencent/Hy-MT2-7B-GGUF",
        "Hy-MT2-7B-Q4_K_M.gguf",
        4625,
        "Hy-MT2 7B（质量最好）",
        "约 4.6 GB；需 8 GB 以上空闲内存，纯 CPU 每句 2–9 秒，建议有独显时用",
    ),
)


def hymt_model(model_id: str | None) -> HyMtModel:
    return next((m for m in HYMT_MODELS if m.id == model_id), HYMT_MODELS[0])


def _lang(code: str) -> str:
    key = (code or "").split("-")[0].lower()
    return LANGUAGE_NAMES.get(key, code or "the target language")


def _same_language(source_lang: str, target_lang: str) -> bool:
    return (source_lang or "")[:2].lower() == (target_lang or "")[:2].lower()


def clean_llm_output(text: str) -> str:
    """Strip reasoning blocks, wrapping quotes and 'Translation:' prefixes."""
    out = (text or "").strip()
    if "</think>" in out:
        out = out.rsplit("</think>", 1)[1].strip()
    for prefix in ("Translation:", "translation:", "译文：", "译文:", "翻译：", "翻译:"):
        if out.startswith(prefix):
            out = out[len(prefix) :].strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'“”「」":
        out = out[1:-1].strip()
    if len(out) >= 2 and out[0] in "“「" and out[-1] in "”」":
        out = out[1:-1].strip()
    return out


class CachedMt:
    """LRU cache in front of any MT backend (repeated phrases, re-split sentences)."""

    def __init__(self, inner: MtBackend, *, max_entries: int = 512) -> None:
        self._inner = inner
        self._max = max(1, int(max_entries))
        self._cache: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def inner(self) -> MtBackend:
        return self._inner

    @property
    def configured(self) -> bool:
        return bool(self._inner.configured)

    @property
    def warming_up(self) -> bool:
        return bool(getattr(self._inner, "warming_up", False))

    def start(self) -> None:
        self._inner.start()

    def stop(self) -> None:
        self._inner.stop()

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        normalized = " ".join((text or "").split())
        if not normalized:
            return None
        key = ((source_lang or "")[:2].lower(), (target_lang or "")[:2].lower(), normalized)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        out = self._inner.translate(text, source_lang=source_lang, target_lang=target_lang)
        if out:
            with self._lock:
                self._cache[key] = out
                self._cache.move_to_end(key)
                while len(self._cache) > self._max:
                    self._cache.popitem(last=False)
        return out


class FallbackMt:
    """Try ``primary`` first; use ``fallback`` when it returns nothing."""

    def __init__(self, primary: MtBackend, fallback: MtBackend, *, name: str = "") -> None:
        self._primary = primary
        self._fallback = fallback
        self._name = name or type(primary).__name__
        self._fallback_logged_at = 0.0
        self._fallback_started = False
        self._breaker = CircuitBreaker()

    @property
    def configured(self) -> bool:
        return bool(self._primary.configured or self._fallback.configured)

    def _primary_warming(self) -> bool:
        return bool(getattr(self._primary, "warming_up", False)) and not self._primary.configured

    @property
    def warming_up(self) -> bool:
        """Hold text while the primary loads, and while a fallback in use is loading."""
        if self._primary_warming():
            return True
        return self._fallback_started and bool(getattr(self._fallback, "warming_up", False))

    def start(self) -> None:
        self._primary.start()

    def stop(self) -> None:
        self._primary.stop()
        if self._fallback_started:
            self._fallback.stop()
            self._fallback_started = False

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        if self._primary_warming():
            # A model still downloading is not failing; don't hand its text elsewhere.
            return None
        if self._primary.configured and self._breaker.allow():
            out = self._primary.translate(text, source_lang=source_lang, target_lang=target_lang)
            if self._breaker.record(bool(out)):
                logger.warning(
                    f"{self._name} 连续失败，{self._breaker.cooldown_s:.0f} 秒内直接使用备用翻译"
                )
            if out:
                return out
        if not self._fallback_started:
            # Started lazily so a working primary never loads the fallback model.
            self._fallback_started = True
            self._fallback.start()
        now = time.time()
        if now - self._fallback_logged_at >= _LOG_INTERVAL_SEC:
            self._fallback_logged_at = now
            logger.warning(f"{self._name} 无结果，本句改用备用翻译")
        return self._fallback.translate(text, source_lang=source_lang, target_lang=target_lang)


class ChatCompletionMt:
    """OpenAI-compatible chat-completions translator with rolling dialogue context."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        glossary: dict[str, str] | None = None,
        context_turns: int = 3,
        timeout: float = 8.0,
        temperature: float = 0.2,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._model = (model or "").strip()
        self._api_key = (api_key or "").strip()
        self._glossary = {k: v for k, v in (glossary or {}).items() if k and v}
        self._context: dict[tuple[str, str], deque[tuple[str, str]]] = {}
        self._context_turns = max(0, int(context_turns))
        self._timeout = float(timeout)
        self._temperature = float(temperature)
        self._client = client
        self._owns_client = client is None
        self._started = False
        self._last_err_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._model)

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, trust_env=True)

    def stop(self) -> None:
        self._started = False
        with self._lock:
            self._context.clear()
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _messages(self, text: str, source_lang: str, target_lang: str) -> list[dict[str, str]]:
        system = _SYSTEM_PROMPT.format(src=_lang(source_lang), tgt=_lang(target_lang))
        relevant = {k: v for k, v in self._glossary.items() if k in text}
        if relevant:
            terms = "; ".join(f"{k} → {v}" for k, v in relevant.items())
            system += f" Use these term translations: {terms}."
        messages = [{"role": "system", "content": system}]
        key = ((source_lang or "")[:2].lower(), (target_lang or "")[:2].lower())
        with self._lock:
            history = list(self._context.get(key, ()))
        for src_text, tgt_text in history:
            messages.append({"role": "user", "content": src_text})
            messages.append({"role": "assistant", "content": tgt_text})
        messages.append({"role": "user", "content": text})
        return messages

    def _remember(self, text: str, translated: str, source_lang: str, target_lang: str) -> None:
        if self._context_turns <= 0:
            return
        key = ((source_lang or "")[:2].lower(), (target_lang or "")[:2].lower())
        with self._lock:
            history = self._context.setdefault(key, deque(maxlen=self._context_turns))
            history.append((text, translated))

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        text = (text or "").strip()
        if not text or not self._started or not self.configured or self._client is None:
            return None
        if _same_language(source_lang, target_lang):
            return text
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages(text, source_lang, target_lang),
            "temperature": self._temperature,
            "max_tokens": 256,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = self._client.post(
                f"{self._base_url}/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"LLM 翻译失败 ({self._model}): {exc}")
            return None
        out = clean_llm_output(str(content or ""))
        if not out:
            return None
        self._remember(text, out, source_lang, target_lang)
        return out


def hymt_prompt(text: str, target_lang: str) -> str:
    """Official Hy-MT2 single-turn instruction."""
    if (target_lang or "")[:2].lower() == "zh":
        return f"将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n{text}"
    return (
        f"Translate the following text into {_lang(target_lang)}. Note that you should "
        f"only output the translated result without any additional explanation:\n{text}"
    )


def default_hymt_dir() -> Path:
    from src.utils.resource_paths import model_resource_root

    return model_resource_root() / "mt" / "hy-mt2"


class HyMtLocal:
    """Hy-MT2 GGUF translated in-process with llama-cpp-python (GPU offload when present)."""

    def __init__(
        self,
        *,
        repo_id: str = HYMT_DEFAULT_REPO,
        filename: str = HYMT_DEFAULT_FILE,
        download_mb: int = HYMT_DOWNLOAD_MB,
        model_dir: Path | None = None,
        device_preference: str = "auto",
        n_ctx: int = 2048,
        auto_download: bool = True,
    ) -> None:
        self._repo_id = repo_id
        self._filename = filename
        self._download_mb = int(download_mb)
        self._model_dir = model_dir or default_hymt_dir()
        self._device_preference = (device_preference or "auto").lower()
        self._n_ctx = int(n_ctx)
        self._auto_download = auto_download
        self._llm: Any = None
        self._lock = threading.Lock()
        self._loading = False
        self._ready = False
        self._failed = False
        self._started = False
        self._last_err_at = 0.0

    @property
    def model_path(self) -> Path:
        return self._model_dir / self._filename

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def warming_up(self) -> bool:
        return self._loading

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
        threading.Thread(target=self._load_background, name="hymt-load", daemon=True).start()

    def _load_background(self) -> None:
        ok = False
        try:
            ok = self._load()
        except Exception as exc:
            logger.warning(f"Hy-MT2 加载失败: {exc}")
        with self._lock:
            self._ready = ok
            self._failed = not ok
            self._loading = False
        if ok:
            logger.info(f"Hy-MT2 已就绪: {self.model_path.name}")

    def _load(self) -> bool:
        try:
            from llama_cpp import Llama
        except ImportError:
            logger.warning("Hy-MT2 需要 llama-cpp-python：pip install llama-cpp-python")
            return False
        if not self.model_path.is_file() and not self._download():
            return False
        gpu_layers = 0 if self._device_preference == "cpu" else -1
        llm = Llama(
            model_path=str(self.model_path),
            n_ctx=self._n_ctx,
            n_gpu_layers=gpu_layers,
            n_threads=max(1, (os.cpu_count() or 4) // 2),
            verbose=False,
        )
        with self._lock:
            self._llm = llm
        return True

    def _download(self) -> bool:
        if not self._auto_download:
            logger.warning(f"Hy-MT2 模型不存在且禁用下载: {self.model_path}")
            return False
        manifest = HYMT.get(self._repo_id)
        if manifest is None:
            logger.warning(f"未知的 Hy-MT2 仓库 {self._repo_id}，无法下载")
            return False
        try:
            manifest.fetch(
                self._model_dir,
                label=f"Hy-MT2 翻译 · {self._filename}",
                key=f"mt:{self._repo_id}/{self._filename}",
            )
        except Exception as exc:
            logger.warning(f"Hy-MT2 下载失败: {exc}")
            return False
        return self.model_path.is_file()

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        text = (text or "").strip()
        if not text or not self._started:
            return None
        if _same_language(source_lang, target_lang):
            return text
        if not self._ready:
            self.start_loading()
            return None
        try:
            with self._lock:
                result = self._llm.create_chat_completion(
                    messages=[{"role": "user", "content": hymt_prompt(text, target_lang)}],
                    temperature=0.7,
                    top_p=0.6,
                    top_k=20,
                    repeat_penalty=1.05,
                    max_tokens=256,
                )
            content = result["choices"][0]["message"]["content"]
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"Hy-MT2 翻译失败: {exc}")
            return None
        return clean_llm_output(str(content or "")) or None
