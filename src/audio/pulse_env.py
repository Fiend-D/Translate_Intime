"""Serialize temporary PulseAudio sink selection during stream creation."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_PULSE_ENV_LOCK = threading.RLock()


@contextmanager
def temporary_pulse_sink(sink: str | None) -> Iterator[None]:
    """Set ``PULSE_SINK`` only while a backend resolves its default output."""
    if not sink:
        yield
        return
    with _PULSE_ENV_LOCK:
        previous = os.environ.get("PULSE_SINK")
        os.environ["PULSE_SINK"] = sink
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("PULSE_SINK", None)
            else:
                os.environ["PULSE_SINK"] = previous
