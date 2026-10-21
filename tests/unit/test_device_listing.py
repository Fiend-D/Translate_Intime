"""Linux output-device filtering and sink routing."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.audio import device_listing
from src.audio.pulse_env import temporary_pulse_sink

_PACTL = """Sink #55
\tState: RUNNING
\tName: alsa_output.usb-Generic-00.analog-stereo
\tDescription: USB2.0 Device Analog Stereo
Sink #56
\tName: alsa_output.pci-0000_00_1f.3.hdmi-stereo
\tDescription: Built-in Audio Digital Stereo (HDMI)
"""


def _dev(index: int, name: str) -> dict[str, object]:
    return {"index": index, "name": name, "channels": 2, "sample_rate": 48000, "hostapi": "ALSA"}


def test_linux_outputs_keep_only_named_sinks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        device_listing.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=_PACTL)
    )
    alsa = [
        _dev(1, "a52"),
        _dev(2, "lavrate"),
        _dev(3, "HDA Intel PCH: HDMI 0 (hw:0,3)"),
        _dev(4, "Default Sink"),
        _dev(5, "alsa_output.pci-0000_00_1f.3.hdmi-stereo"),
        _dev(6, "default"),
    ]

    result = device_listing._linux_output_devices(alsa)

    assert [(d["index"], d["name"]) for d in result] == [
        ("alsa_output.usb-Generic-00.analog-stereo", "USB2.0 Device Analog Stereo"),
        ("alsa_output.pci-0000_00_1f.3.hdmi-stereo", "Built-in Audio Digital Stereo (HDMI)"),
    ]


def test_linux_outputs_without_pactl_drop_pseudo_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("pactl")

    monkeypatch.setattr(device_listing.subprocess, "run", _missing)

    result = device_listing._linux_output_devices(
        [_dev(1, "a52"), _dev(2, "HDA Intel PCH: ALC897 Analog (hw:0,0)")]
    )

    assert [d["index"] for d in result] == [2]


def test_temporary_sink_sets_pulse_and_pipewire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PULSE_SINK", raising=False)
    monkeypatch.setenv("PIPEWIRE_NODE", "previous")

    with temporary_pulse_sink("my_sink"):
        assert os.environ["PULSE_SINK"] == "my_sink"
        assert os.environ["PIPEWIRE_NODE"] == "my_sink"

    assert "PULSE_SINK" not in os.environ
    assert os.environ["PIPEWIRE_NODE"] == "previous"
