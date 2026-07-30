"""ASR backends for the economy pipeline."""

from __future__ import annotations

import os
import tempfile
import time
import wave
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from src.engines.pipeline.sentence_split import is_cjk_majority
from src.utils.logger import logger

_STUB_MSG = "经济模式尚未配置 ASR（需要 DashScope API Key）"
_INSTALL_HINT = "未安装 dashscope，请执行: pip install 'dashscope>=1.20'"
_LOG_INTERVAL_SEC = 30.0

_LANGUAGE_HINTS = {
    "zh": ["zh", "en"],
    "en": ["en", "zh"],
    "ja": ["ja", "en"],
    "ko": ["ko", "en"],
}


class AsrBackend(Protocol):
    configured: bool

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def recognize(self, pcm: bytes, *, language: str) -> str | None: ...


class UnconfiguredAsr:
    """Placeholder ASR that cannot recognize."""

    def __init__(self) -> None:
        self._last_log_at = 0.0
        self._started = False

    @property
    def configured(self) -> bool:
        return False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        del language
        if not pcm or not self._started:
            return None
        now = time.time()
        if now - self._last_log_at >= _LOG_INTERVAL_SEC:
            self._last_log_at = now
            logger.warning(_STUB_MSG)
        return None


class DashScopeAsr:
    """Cloud ASR via DashScope Fun-ASR / Paraformer realtime Recognition.

    Prefer model ``fun-asr-realtime``; if recognition fails with that model,
    callers may retry with ``paraformer-realtime-v2`` via ``economy_asr_model``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "fun-asr-realtime",
        hotwords: list[str] | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._model = (model or "fun-asr-realtime").strip() or "fun-asr-realtime"
        self._hotwords = [h.strip() for h in (hotwords or []) if h and str(h).strip()]
        self._started = False
        self._dashscope = None
        self._Recognition = None
        self._import_warned = False
        self._hotword_warned = False
        self._last_err_at = 0.0
        self._configured = False
        self._try_import()

    def _try_import(self) -> None:
        if not self._api_key:
            self._configured = False
            return
        try:
            import dashscope
            from dashscope.audio.asr import Recognition

            self._dashscope = dashscope
            self._Recognition = Recognition
            self._configured = True
        except ImportError:
            self._configured = False
            if not self._import_warned:
                self._import_warned = True
                logger.warning(_INSTALL_HINT)

    @property
    def configured(self) -> bool:
        return bool(self._configured and self._api_key and self._Recognition)

    def start(self) -> None:
        self._started = True
        if self._dashscope is not None and self._api_key:
            self._dashscope.api_key = self._api_key

    def stop(self) -> None:
        self._started = False

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        if not pcm or not self._started or not self.configured:
            return None
        assert self._Recognition is not None
        assert self._dashscope is not None
        self._dashscope.api_key = self._api_key

        hints = _LANGUAGE_HINTS.get((language or "zh")[:2].lower(), ["zh", "en"])
        path: Path | None = None
        try:
            path = self._write_temp_wav(pcm)
            text = self._call_recognition(path, hints)
            if text:
                return text
            # Soft fallback to paraformer if fun-asr was selected and returned empty.
            if self._model.startswith("fun-asr"):
                text = self._call_recognition(
                    path, hints, model_override="paraformer-realtime-v2"
                )
            return text
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"DashScope ASR failed: {exc}")
            return None
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _write_temp_wav(self, pcm: bytes) -> Path:
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        path = Path(name)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm)
        return path

    def _recognition_kwargs(
        self,
        *,
        model: str,
        fmt: str,
        language_hints: list[str],
        callback=None,  # noqa: ANN001
    ) -> dict:
        kwargs: dict = {
            "model": model,
            "format": fmt,
            "sample_rate": 16000,
            "callback": callback,
            "language_hints": language_hints,
        }
        # Semantic punctuation improves sentence boundaries for Fun-ASR / Paraformer.
        for key, value in (
            ("semantic_punctuation_enabled", True),
            ("semantic_punctuation", True),
        ):
            kwargs[key] = value

        if self._hotwords:
            # Try common DashScope vocabulary / phrase kwargs; drop unsupported later.
            phrase = " ".join(self._hotwords)
            for key, value in (
                ("vocabulary_id", None),
                ("phrase_list", self._hotwords),
                ("hotwords", phrase),
                ("hotword", phrase),
            ):
                if value is not None:
                    kwargs[key] = value
        return kwargs

    def _build_recognition(self, kwargs: dict):
        assert self._Recognition is not None
        # Drop unknown kwargs one-by-one if the SDK rejects them.
        attempt = dict(kwargs)
        optional_keys = (
            "semantic_punctuation_enabled",
            "semantic_punctuation",
            "phrase_list",
            "hotwords",
            "hotword",
            "vocabulary_id",
        )
        last_exc: TypeError | None = None
        for _ in range(len(optional_keys) + 1):
            try:
                return self._Recognition(**attempt)
            except TypeError as exc:
                last_exc = exc
                msg = str(exc)
                dropped = False
                for key in optional_keys:
                    if key in attempt and (key in msg or "unexpected" in msg.lower()):
                        if key in ("phrase_list", "hotwords", "hotword", "vocabulary_id"):
                            if self._hotwords and not self._hotword_warned:
                                self._hotword_warned = True
                                logger.info(
                                    "DashScope Recognition 不支持热词参数，已忽略 hotwords"
                                )
                        attempt.pop(key, None)
                        dropped = True
                        break
                if not dropped:
                    for key in optional_keys:
                        attempt.pop(key, None)
                    try:
                        return self._Recognition(**attempt)
                    except TypeError as exc2:
                        raise exc2 from last_exc
        assert last_exc is not None
        raise last_exc

    def _call_recognition(
        self,
        path: Path,
        language_hints: list[str],
        *,
        model_override: str | None = None,
    ) -> str | None:
        model = model_override or self._model
        kwargs = self._recognition_kwargs(
            model=model, fmt="wav", language_hints=language_hints, callback=None
        )
        recognition = self._build_recognition(kwargs)

        if hasattr(recognition, "call"):
            result = recognition.call(str(path))
            return self._parse_result(result)

        # Streaming fallback: start / send_audio_frame / stop
        sentences: list[str] = []

        class _Cb:
            def on_open(self) -> None:
                return None

            def on_close(self) -> None:
                return None

            def on_error(self, result) -> None:  # noqa: ANN001
                logger.warning(f"DashScope ASR stream error: {result}")

            def on_event(self, result) -> None:  # noqa: ANN001
                text = DashScopeAsr._extract_sentence_text(result)
                if text:
                    sentences.append(text)

            def on_complete(self) -> None:
                return None

        stream_kwargs = self._recognition_kwargs(
            model=model,
            fmt="pcm",
            language_hints=language_hints,
            callback=_Cb(),
        )
        recognition = self._build_recognition(stream_kwargs)
        recognition.start()
        try:
            with open(path, "rb") as f:
                # Skip wav header if present; send raw frames in ~100ms chunks.
                header = f.read(44)
                raw = f.read() if header[:4] == b"RIFF" else header + f.read()
            chunk = 3200
            for i in range(0, len(raw), chunk):
                recognition.send_audio_frame(raw[i : i + chunk])
            recognition.stop()
        except Exception:
            with suppress(Exception):
                recognition.stop()
            raise
        return self._join_sentences(sentences)

    @staticmethod
    def _join_sentences(parts: list[str]) -> str | None:
        cleaned = [p.strip() for p in parts if p and p.strip()]
        if not cleaned:
            return None
        blob = "".join(cleaned)
        if is_cjk_majority(blob):
            joined = "".join(cleaned).strip()
        else:
            joined = " ".join(cleaned).strip()
        return joined or None

    @staticmethod
    def _parse_result(result) -> str | None:  # noqa: ANN001
        if result is None:
            return None
        status = getattr(result, "status_code", None)
        if status is not None:
            try:
                from http import HTTPStatus

                if status != HTTPStatus.OK:
                    msg = getattr(result, "message", None) or status
                    logger.warning(f"DashScope ASR status={status}: {msg}")
                    return None
            except Exception:
                pass
        text = DashScopeAsr._extract_sentence_text(result)
        return text.strip() if text else None

    @staticmethod
    def _extract_sentence_text(result) -> str | None:  # noqa: ANN001
        # RecognitionResult.get_sentence() may return list[dict] or dict.
        get_sentence = getattr(result, "get_sentence", None)
        if callable(get_sentence):
            try:
                sentence = get_sentence()
            except Exception:
                sentence = None
            if isinstance(sentence, list):
                parts = []
                for item in sentence:
                    if isinstance(item, dict):
                        t = (item.get("text") or item.get("sentence") or "").strip()
                        if t:
                            parts.append(t)
                    elif isinstance(item, str) and item.strip():
                        parts.append(item.strip())
                if parts:
                    return DashScopeAsr._join_sentences(parts)
            if isinstance(sentence, dict):
                t = (sentence.get("text") or sentence.get("sentence") or "").strip()
                if t:
                    return t
            if isinstance(sentence, str) and sentence.strip():
                return sentence.strip()

        output = getattr(result, "output", None)
        if isinstance(output, dict):
            s = output.get("sentence") or output.get("text")
            if isinstance(s, list):
                parts = []
                for item in s:
                    if isinstance(item, dict):
                        t = (item.get("text") or "").strip()
                        if t:
                            parts.append(t)
                if parts:
                    return DashScopeAsr._join_sentences(parts)
            if isinstance(s, dict):
                return (s.get("text") or "").strip() or None
            if isinstance(s, str):
                return s.strip() or None
        return None
