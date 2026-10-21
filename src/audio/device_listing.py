"""Enumerate audio devices with full-length names and smart host-API preference.

sounddevice / PortAudio on Windows reports the same physical endpoint under
multiple host APIs (MME / DirectSound / WASAPI / WDM-KS) with *different*
indices, and MME truncates endpoint names to 31 bytes so users can't recognise
them.  This module collapses duplicates by (endpoint-id, direction) and keeps
only the best variant, preferring longer (untruncated) names and WASAPI for
playback quality.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

import sounddevice as sd

_PREFERRED_HOSTAPI_ORDER = (
    "Windows WASAPI",
    "Windows DirectSound",
    "MME",
    "Windows WDM-KS",
)


def rescan_portaudio() -> bool:
    """Re-initialise PortAudio so hot-plugged devices appear.

    PortAudio snapshots devices at init, and re-initialising invalidates every
    open stream, so callers must only do this while nothing is capturing/playing.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        return False
    return True


def _hostapi_rank(name: str) -> int:
    name = name or ""
    try:
        return _PREFERRED_HOSTAPI_ORDER.index(name)
    except ValueError:
        return len(_PREFERRED_HOSTAPI_ORDER)


def _hostapi_name(api_idx: int) -> str:
    if api_idx < 0:
        return ""
    try:
        return sd.query_hostapis(api_idx).get("name", "") or ""
    except Exception:
        return ""


# ALSA plugin PCMs that PortAudio lists on Linux but are not playback targets.
_ALSA_PSEUDO_OUTPUT = re.compile(
    r"^(lavrate|samplerate|speexrate|upmix|vdownmix|surround\d+|iec958|spdif|hdmi|dmix|a52|"
    r"dsnoop|front|rear|center_lfe|side|null|jack|oss|sysdefault|default|pulse|pipewire|"
    r"default sink)$",
    re.IGNORECASE,
)


def list_pulse_sinks() -> list[dict[str, str]]:
    """PulseAudio / PipeWire sinks as ``{"name", "description"}`` (empty without pactl)."""
    try:
        out = subprocess.run(
            ["pactl", "list", "sinks"],
            capture_output=True,
            text=True,
            timeout=3,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    sinks: list[dict[str, str]] = []
    for line in out.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Name":
            sinks.append({"name": value.strip(), "description": value.strip()})
        elif key == "Description" and sinks:
            sinks[-1]["description"] = value.strip() or sinks[-1]["name"]
    return sinks


def _linux_output_devices(alsa: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace ALSA pseudo devices with named sinks; sink names route via pulse_env."""
    sinks = list_pulse_sinks()
    kept = [d for d in alsa if not _ALSA_PSEUDO_OUTPUT.match(str(d["name"]).strip())]
    if not sinks:
        return kept
    # Raw hw: devices bypass the sound server and fail while it holds the card;
    # PortAudio's own PulseAudio entries duplicate the sinks with unstable indices.
    sink_names = {sink["name"] for sink in sinks}
    kept = [d for d in kept if "(hw:" not in str(d["name"]) and str(d["name"]) not in sink_names]
    kept.extend(
        {
            "index": sink["name"],
            "name": sink["description"],
            "channels": 2,
            "sample_rate": 48000,
            "hostapi": "PipeWire/PulseAudio",
        }
        for sink in sinks
    )
    return kept


def list_output_devices() -> list[dict[str, Any]]:
    """De-duplicated playback devices, with full names and preferred host APIs.

    Returns a list of dicts with keys:
      index (int)        – sounddevice index suitable for sd.play() / OutputStream
      name (str)         – longest (untruncated) name found across host APIs
      channels (int)     – max_output_channels of the chosen variant
      sample_rate (int)  – default_samplerate of the chosen variant
      hostapi (str)      – chosen host-API human-readable name
    """
    all_devs = list(sd.query_devices())
    # group by (normalised-name-prefix, direction) so truncation variants collapse
    # key: longest name we've seen so far acts as the canonical id via startswith-match
    buckets: list[dict[str, Any]] = []
    # each bucket: {canonical_name, best_idx, best_sr, best_ch, best_hostapi_rank, best_hostapi_name}

    for idx, d in enumerate(all_devs):
        ch = int(d.get("max_output_channels", 0))
        if ch <= 0:
            continue
        name = d.get("name", "") or ""
        api_idx = int(d.get("hostapi", -1))
        api_name = _hostapi_name(api_idx)
        rank = _hostapi_rank(api_name)
        sr = int(d.get("default_samplerate", 44100))

        # find bucket by startswith (both directions; truncation is asymmetric)
        matched: dict[str, Any] | None = None
        for b in buckets:
            cn = b["canonical_name"]
            if cn and name and (cn.startswith(name) or name.startswith(cn)):
                matched = b
                break

        if matched is None:
            buckets.append(
                {
                    "canonical_name": name,
                    "best_idx": idx,
                    "best_sr": sr,
                    "best_ch": ch,
                    "best_rank": rank,
                    "best_hostapi": api_name,
                }
            )
            continue

        # extend canonical name if this variant has a longer (untruncated) form
        if len(name) > len(matched["canonical_name"]):
            matched["canonical_name"] = name

        # pick the best index: lower hostapi rank = better; tie-break on ch==2 then higher sr
        def _score(curr: dict[str, Any]) -> tuple[int, int, int]:
            ch_ok = 0 if curr["best_ch"] == 2 else 1
            return (curr["best_rank"], ch_ok, -curr["best_sr"])

        cand = {
            "best_idx": idx,
            "best_sr": sr,
            "best_ch": ch,
            "best_rank": rank,
            "best_hostapi": api_name,
            "canonical_name": matched["canonical_name"],
        }
        if _score(cand) < _score(matched):
            matched.update(
                best_idx=cand["best_idx"],
                best_sr=cand["best_sr"],
                best_ch=cand["best_ch"],
                best_rank=cand["best_rank"],
                best_hostapi=cand["best_hostapi"],
            )

    result = []
    for b in buckets:
        result.append(
            {
                "index": b["best_idx"],
                "name": b["canonical_name"],
                "channels": b["best_ch"],
                "sample_rate": b["best_sr"],
                "hostapi": b["best_hostapi"],
            }
        )
    result.sort(key=lambda d: d["name"].lower())
    if sys.platform.startswith("linux"):
        return _linux_output_devices(result)
    return result


def list_input_devices() -> list[dict[str, Any]]:
    """Same deduplication for capture devices."""
    all_devs = list(sd.query_devices())
    buckets: list[dict[str, Any]] = []

    for idx, d in enumerate(all_devs):
        ch = int(d.get("max_input_channels", 0))
        if ch <= 0:
            continue
        name = d.get("name", "") or ""
        api_idx = int(d.get("hostapi", -1))
        api_name = _hostapi_name(api_idx)
        rank = _hostapi_rank(api_name)
        sr = int(d.get("default_samplerate", 44100))

        matched: dict[str, Any] | None = None
        for b in buckets:
            cn = b["canonical_name"]
            if cn and name and (cn.startswith(name) or name.startswith(cn)):
                matched = b
                break

        if matched is None:
            buckets.append(
                {
                    "canonical_name": name,
                    "best_idx": idx,
                    "best_sr": sr,
                    "best_ch": ch,
                    "best_rank": rank,
                    "best_hostapi": api_name,
                }
            )
            continue

        if len(name) > len(matched["canonical_name"]):
            matched["canonical_name"] = name

        def _score(curr: dict[str, Any]) -> tuple[int, int, int]:
            ch_ok = 0 if curr["best_ch"] == 2 else 1
            return (curr["best_rank"], ch_ok, -curr["best_sr"])

        cand = {
            "best_idx": idx,
            "best_sr": sr,
            "best_ch": ch,
            "best_rank": rank,
            "best_hostapi": api_name,
            "canonical_name": matched["canonical_name"],
        }
        if _score(cand) < _score(matched):
            matched.update(
                best_idx=cand["best_idx"],
                best_sr=cand["best_sr"],
                best_ch=cand["best_ch"],
                best_rank=cand["best_rank"],
                best_hostapi=cand["best_hostapi"],
            )

    result = []
    for b in buckets:
        result.append(
            {
                "index": b["best_idx"],
                "name": b["canonical_name"],
                "channels": b["best_ch"],
                "sample_rate": b["best_sr"],
                "hostapi": b["best_hostapi"],
            }
        )
    result.sort(key=lambda d: d["name"].lower())
    return result
