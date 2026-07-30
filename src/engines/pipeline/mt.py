"""MT backends for the economy pipeline."""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from src.engines.pipeline.nllb_mt import NllbCt2Mt
from src.utils.logger import logger

_STUB_MSG = "经济模式尚未配置 MT"
_ARGOS_HINT = (
    "可选安装 argostranslate 做本地翻译回退（体积较大）: pip install argostranslate"
)
_LOG_INTERVAL_SEC = 30.0


class MtBackend(Protocol):
    configured: bool

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def translate(
        self, text: str, *, source_lang: str, target_lang: str
    ) -> str | None: ...


class UnconfiguredMt:
    """Placeholder MT that returns nothing."""

    def __init__(self) -> None:
        self._last_log_at = 0.0
        self._started = False

    @property
    def configured(self) -> bool:
        return False

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def translate(
        self, text: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        del source_lang, target_lang
        if not text or not self._started:
            return None
        now = time.time()
        if now - self._last_log_at >= _LOG_INTERVAL_SEC:
            self._last_log_at = now
            logger.warning(_STUB_MSG)
        return None


class ArgosMt:
    """Local Argos Translate when package + language pair are available.

    ``configured`` is True when the library imports; ``translate`` may still
    return None if the language pair package is missing (fallback chain).
    """

    def __init__(self) -> None:
        self._started = False
        self._argos = None
        self._hinted = False
        try:
            import argostranslate.package  # noqa: F401
            import argostranslate.translate

            self._argos = argostranslate.translate
        except ImportError:
            self._argos = None

    @property
    def configured(self) -> bool:
        return self._argos is not None

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True
        if self._argos is None and not self._hinted:
            self._hinted = True
            logger.info(_ARGOS_HINT)

    def stop(self) -> None:
        self._started = False

    def translate(
        self, text: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        if not text or not self._started or self._argos is None:
            return None
        src = (source_lang or "")[:2].lower()
        tgt = (target_lang or "")[:2].lower()
        if not src or not tgt or src == tgt:
            return text.strip() or None
        try:
            out = self._argos.translate(text, src, tgt)
            out = (out or "").strip()
            return out or None
        except Exception as exc:
            logger.debug(f"Argos translate unavailable for {src}->{tgt}: {exc}")
            return None


class MyMemoryMt:
    """Free MyMemory HTTP API (no key), same as typed_translate."""

    def __init__(self) -> None:
        self._started = False
        self._last_err_at = 0.0

    @property
    def configured(self) -> bool:
        return True

    @property
    def warming_up(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def translate(
        self, text: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        text = (text or "").strip()
        if not text or not self._started:
            return None
        src = (source_lang or "en")[:2].lower()
        tgt = (target_lang or "zh")[:2].lower()
        if src == tgt:
            return text
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:450], "langpair": f"{src}|{tgt}"}
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            translated = (
                (data.get("responseData") or {}).get("translatedText") or ""
            ).strip()
            return translated or None
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"MyMemory MT failed: {exc}")
            return None


class AutoMt:
    """Prefer NLLB (local), then Argos, then MyMemory as optional fallbacks."""

    def __init__(
        self,
        *,
        prefer: str = "auto",
        nllb: NllbCt2Mt | None = None,
        argos: ArgosMt | None = None,
        mymemory: MyMemoryMt | None = None,
        nllb_model: str | None = None,
        device_preference: str = "auto",
    ) -> None:
        self._prefer = (prefer or "auto").lower()
        self._nllb = nllb or NllbCt2Mt(
            model_id=nllb_model or "", device_preference=device_preference
        )
        self._argos = argos or ArgosMt()
        self._mymemory = mymemory or MyMemoryMt()
        self._started = False
        self._fallback_logged = False

    @property
    def configured(self) -> bool:
        if self._prefer == "nllb":
            return self._nllb.configured
        if self._prefer == "argos":
            return self._argos.configured
        if self._prefer == "mymemory":
            return self._mymemory.configured
        return (
            self._nllb.configured
            or self._argos.configured
            or self._mymemory.configured
        )

    @property
    def warming_up(self) -> bool:
        if self._prefer in ("auto", "nllb"):
            return bool(getattr(self._nllb, "warming_up", False))
        return False

    def start(self) -> None:
        self._started = True
        if self._prefer in ("auto", "nllb"):
            self._nllb.start()
        if self._prefer in ("auto", "argos"):
            self._argos.start()
        if self._prefer in ("auto", "mymemory"):
            self._mymemory.start()
        # Always prepare fallbacks for auto/nllb when primary not ready later.
        if self._prefer == "nllb":
            self._argos.start()
            self._mymemory.start()

    def stop(self) -> None:
        self._started = False
        self._nllb.stop()
        self._argos.stop()
        self._mymemory.stop()

    def translate(
        self, text: str, *, source_lang: str, target_lang: str
    ) -> str | None:
        if not text or not self._started:
            return None
        if self._prefer == "argos":
            return self._argos.translate(
                text, source_lang=source_lang, target_lang=target_lang
            )
        if self._prefer == "mymemory":
            return self._mymemory.translate(
                text, source_lang=source_lang, target_lang=target_lang
            )

        # auto / nllb: NLLB first
        out = self._nllb.translate(
            text, source_lang=source_lang, target_lang=target_lang
        )
        if out:
            return out
        # While NLLB is still downloading/loading, do not silently fall back.
        if getattr(self._nllb, "warming_up", False):
            return None

        for backend, name in (
            (self._argos, "Argos"),
            (self._mymemory, "MyMemory"),
        ):
            out = backend.translate(
                text, source_lang=source_lang, target_lang=target_lang
            )
            if out:
                if not self._fallback_logged:
                    self._fallback_logged = True
                    logger.warning(f"NLLB 不可用，已回退到 {name} 翻译")
                return out
        return None
