"""Serialize temporary PulseAudio / PipeWire sink selection during stream creation."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_PULSE_ENV_LOCK = threading.RLock()
# PulseAudio's ALSA plugin reads PULSE_SINK; pipewire-alsa ignores it and reads PIPEWIRE_NODE.
_SINK_ENV_VARS = ("PULSE_SINK", "PIPEWIRE_NODE")


@contextmanager
def temporary_pulse_sink(sink: str | None) -> Iterator[None]:
    """Route the next default-device stream to ``sink`` while it is being opened."""
    if not sink:
        yield
        return
    with _PULSE_ENV_LOCK:
        previous = {name: os.environ.get(name) for name in _SINK_ENV_VARS}
        for name in _SINK_ENV_VARS:
            os.environ[name] = sink
        try:
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
