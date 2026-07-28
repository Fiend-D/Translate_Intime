"""Core exceptions used across the realtime translation pipeline."""


class EngineLoadError(Exception):
    """Raised when an engine (ASR, translator, TTS) fails to load."""


class EngineRuntimeError(Exception):
    """Raised when an engine fails during inference or API call."""


class EngineNotLoadedError(Exception):
    """Raised when engine methods are called before load()."""


class ConfigValidationError(Exception):
    """Raised when user configuration fails validation."""
