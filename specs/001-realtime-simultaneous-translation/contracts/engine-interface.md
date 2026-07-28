# Contract: Engine Interface

## Purpose

Defines the uniform interface that all ASR, Translator, and TTS engines MUST implement. This contract guarantees that the `TranslationSession` orchestrator can swap engines at runtime without code changes.

---

## BaseASREngine

### Lifecycle

```python
class BaseASREngine(ABC):
    @abstractmethod
    def load(self, config: dict) -> None:
        """Prepare the model / auth. Raises EngineLoadError on failure."""

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return True if the engine is ready to process audio."""

    @abstractmethod
    def transcribe(self, audio_chunk: AudioChunk) -> str:
        """
        Synchronously transcribe a single audio chunk.
        Returns the transcribed text (may be empty string for silence).
        Raises EngineRuntimeError on failure.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release memory / close connections. Safe to call multiple times."""

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """LOCAL or CLOUD."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Unique identifier, e.g. 'qwen-asr', 'whisper-cloud'."""
```

### Guarantees

- `load()` MAY be called on a background thread; it MUST NOT touch Qt GUI objects.
- `transcribe()` MUST be thread-safe (no shared mutable state without locks).
- `unload()` MUST be idempotent.

---

## BaseTranslator

### Lifecycle

```python
class BaseTranslator(ABC):
    @abstractmethod
    def load(self, config: dict) -> None:
        """Prepare the model / auth. Raises EngineLoadError on failure."""

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return True if the engine is ready to translate."""

    @abstractmethod
    def translate(self, text: str, source_lang: LanguageCode, target_lang: LanguageCode) -> str:
        """
        Synchronously translate a single text string.
        Returns the translated text.
        Raises EngineRuntimeError on failure.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release memory / close connections. Safe to call multiple times."""

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """LOCAL or CLOUD."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Unique identifier, e.g. 'hunyuan', 'openai'."""
```

### Guarantees

- `translate()` MUST handle text up to 500 characters (matching `SubtitleEntry` constraints).
- If `source_lang == target_lang`, the engine MAY return the input unchanged rather than erroring.

---

## BaseTTSEngine

### Lifecycle

```python
class BaseTTSEngine(ABC):
    @abstractmethod
    def load(self, config: dict) -> None:
        """Prepare the model / auth. Raises EngineLoadError on failure."""

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return True if the engine is ready to synthesize."""

    @abstractmethod
    def synthesize(self, text: str, language: LanguageCode) -> bytes:
        """
        Synchronously synthesize text into PCM audio bytes.
        Returns 16-bit PCM mono audio at 22050 Hz (standardized output).
        Raises EngineRuntimeError on failure.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release memory / close connections. Safe to call multiple times."""

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """LOCAL or CLOUD."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Unique identifier, e.g. 'chattts', 'openai-tts'."""
```

### Guarantees

- Output audio format: 16-bit signed PCM, mono, 22050 Hz. The caller (`audio_player.py`) is responsible for resampling if the target device requires a different rate.
- `synthesize()` MUST handle empty strings gracefully (return empty bytes).

---

## EngineRegistry

A lightweight registry that maps `engine_id` strings to concrete classes.

```python
class EngineRegistry:
    def register_asr(self, engine_id: str, cls: type[BaseASREngine]) -> None: ...
    def register_translator(self, engine_id: str, cls: type[BaseTranslator]) -> None: ...
    def register_tts(self, engine_id: str, cls: type[BaseTTSEngine]) -> None: ...

    def get_asr(self, engine_id: str) -> type[BaseASREngine]: ...
    def get_translator(self, engine_id: str) -> type[BaseTranslator]: ...
    def get_tts(self, engine_id: str) -> type[BaseTTSEngine]: ...
```

### Guarantees

- `get_*` raises `KeyError` for unknown IDs, which the caller must handle and surface to the UI.
- Registration is performed once at application startup in `main.py`.

---

## Error Contract

All engine methods raise these exceptions (defined in `src/core/exceptions.py`):

| Exception | Raised When | Caller Action |
|-----------|-------------|---------------|
| `EngineLoadError` | Model file missing, invalid config, OOM | Show error dialog, allow user to reconfigure |
| `EngineRuntimeError` | Inference failure, API timeout, network error | Retry with backoff (cloud) or pause (local) |
| `EngineNotLoadedError` | Method called before `load()` | Assert / log bug; should never reach user |
