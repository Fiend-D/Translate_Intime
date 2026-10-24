"""Background translation of quick phrases with the configured economy MT backend."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from src.engines.pipeline.mt import MtBackend
from src.models.config import AppConfigModel
from src.utils.logger import logger

# A local model may need to load (or download) before the first phrase.
_WARMUP_TIMEOUT_S = 120.0
_RETRY_INTERVAL_S = 0.3
# Free the model's memory when nobody has added a phrase for a while.
_IDLE_RELEASE_S = 180.0

_MT_FIELDS = (
    "economy_mt_backend",
    "economy_nllb_model",
    "economy_hymt_model",
    "economy_llm_base_url",
    "economy_llm_model",
    "economy_llm_api_key",
    "economy_mt_device",
    "device_preference",
    "model_dir",
)


@dataclass(frozen=True)
class PhraseRequest:
    phrase_id: str
    text: str
    source_lang: str
    target_lang: str


def _mt_signature(config: AppConfigModel) -> tuple[object, ...]:
    return tuple(getattr(config, name, None) for name in _MT_FIELDS)


def _default_factory(config: AppConfigModel) -> MtBackend:
    from src.engines.pipeline.engine import build_economy_mt

    return build_economy_mt(config)


class PhraseTranslator:
    """Translate phrases one at a time on a worker thread.

    ``on_done(request, translation_or_none)`` runs on the worker thread; route it
    to the GUI through a Qt signal.
    """

    def __init__(
        self,
        config_provider: Callable[[], AppConfigModel],
        on_done: Callable[[PhraseRequest, str | None], None],
        *,
        mt_factory: Callable[[AppConfigModel], MtBackend] = _default_factory,
        idle_release_s: float = _IDLE_RELEASE_S,
        warmup_timeout_s: float = _WARMUP_TIMEOUT_S,
    ) -> None:
        self._config_provider = config_provider
        self._on_done = on_done
        self._mt_factory = mt_factory
        self._idle_release_s = idle_release_s
        self._warmup_timeout_s = warmup_timeout_s
        self._queue: queue.Queue[PhraseRequest | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: set[tuple[str, str]] = set()
        self._mt: MtBackend | None = None
        self._mt_signature: tuple[object, ...] = ()

    def request(self, req: PhraseRequest) -> bool:
        """Queue ``req`` unless the same phrase/target is already waiting."""
        key = (req.phrase_id, req.target_lang)
        with self._lock:
            if self._stop.is_set() or key in self._pending:
                return False
            self._pending.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="phrase-translate", daemon=True
                )
                self._thread.start()
        self._queue.put(req)
        return True

    def is_pending(self, phrase_id: str) -> bool:
        with self._lock:
            return any(pid == phrase_id for pid, _ in self._pending)

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._release_mt()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                req = self._queue.get(timeout=self._idle_release_s)
            except queue.Empty:
                self._release_mt()
                continue
            if req is None:
                break
            result: str | None = None
            try:
                result = self._translate(req)
            except Exception as exc:
                logger.warning(f"常用语翻译失败: {exc}")
            with self._lock:
                self._pending.discard((req.phrase_id, req.target_lang))
            if not self._stop.is_set():
                self._on_done(req, result)

    def _backend(self) -> MtBackend:
        config = self._config_provider()
        signature = _mt_signature(config)
        if self._mt is None or signature != self._mt_signature:
            self._release_mt()
            mt = self._mt_factory(config)
            mt.start()
            self._mt, self._mt_signature = mt, signature
        return self._mt

    def _translate(self, req: PhraseRequest) -> str | None:
        if req.source_lang == req.target_lang:
            return req.text
        mt = self._backend()
        deadline = time.monotonic() + self._warmup_timeout_s
        while not self._stop.is_set():
            out = mt.translate(req.text, source_lang=req.source_lang, target_lang=req.target_lang)
            out = (out or "").strip()
            if out:
                return out
            if not getattr(mt, "warming_up", False) or time.monotonic() >= deadline:
                return None
            self._stop.wait(_RETRY_INTERVAL_S)
        return None

    def _release_mt(self) -> None:
        mt, self._mt = self._mt, None
        if mt is not None:
            try:
                mt.stop()
            except Exception as exc:
                logger.debug(f"释放常用语翻译模型失败: {exc}")


__all__ = ["PhraseRequest", "PhraseTranslator"]
