"""Human-readable audio device labels for the control panel."""

from __future__ import annotations

import re
from typing import Any, Literal

Role = Literal["mic", "output", "loopback"]


def _clean_raw_name(name: str) -> str:
    text = (name or "").strip()
    text = re.sub(r"^\[(?:Loopback|默认|Monitor|Null|推荐)\]\s*", "", text, flags=re.I)
    text = text.replace("wasapi_loopback:", "")
    text = text.replace("wasapi_proc_exclude:", "")
    text = text.replace("🔁 ", "").replace("🎧 ", "").replace("🔊 ", "")
    # Drop ALSA/PipeWire technical suffixes that confuse users
    text = re.sub(r"\s*\(.*?(alsa|pipewire|pulse|hw:).*?\)\s*$", "", text, flags=re.I)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or "未知设备"


def classify_device(name: str, device_id: Any = None) -> str:
    """Return a coarse device kind for labeling."""
    raw = f"{name} {device_id}".lower()
    if "wasapi_proc_exclude:" in raw or "排除本应用" in raw:
        return "system_loopback"
    if "wasapi_loopback:" in raw or "[loopback]" in raw:
        return "system_loopback"
    if ".monitor" in raw or "monitor" in raw:
        return "system_monitor"
    if "translator_virtual_sink" in raw:
        return "app_virtual"
    if "cable input" in raw or ("vb-audio" in raw and "input" in raw):
        return "vb_to_game"
    if re.search(
        r"voicemeeter\s+(aux\s+)?input\b|voicemeeter\s+vaio3\s+input|voicemeeter\s+in\s+\d",
        raw,
    ):
        return "vb_to_game"
    if "cable output" in raw or ("vb-audio" in raw and "output" in raw):
        return "vb_capture"
    if re.search(r"voicemeeter\s+out\b", raw):
        return "vb_capture"
    if "null" in raw:
        return "null_sink"
    if any(k in raw for k in ("mic", "microphone", "麦克风", "headset", "耳机麦")):
        return "microphone"
    if any(k in raw for k in ("speaker", "headphone", "耳机", "扬声器", "hdmi")):
        return "speaker"
    return "generic"


def format_device_label(role: Role, name: str, device_id: Any = None) -> str:
    """Build a user-facing label for a combo box item."""
    kind = classify_device(name, device_id)
    short = _clean_raw_name(str(name))

    if role == "mic":
        if kind in {"vb_capture", "cable_output"}:
            return f"虚拟线缆捕获 · {short}"
        if kind == "microphone":
            return f"麦克风 · {short}"
        return f"输入设备 · {short}"

    if role == "output":
        if kind in {"vb_to_game", "app_virtual"} or "cable input" in short.lower():
            return f"送进游戏麦克风 · {short}"
        if kind == "speaker":
            return f"本机扬声器/耳机 · {short}"
        return f"播放设备 · {short}"

    # loopback / game capture
    if str(device_id).startswith("wasapi_proc_exclude:") or "排除本应用" in str(name):
        return "系统正在播放的声音 · 免驱动（排除本应用）"
    if kind == "system_loopback":
        return f"系统正在播放的声音 · {short}"
    if kind == "system_monitor":
        if "translator_virtual" in str(name).lower() or "translator_virtual" in str(device_id).lower():
            return f"本应用虚拟声卡回采 · {short}"
        return f"系统声音回采 · {short}"
    if kind == "app_virtual":
        return f"推荐：游戏/系统声音捕获 · {short}"
    if kind == "vb_capture":
        return f"虚拟线缆输出捕获 · {short}"
    if kind == "null_sink":
        return f"空设备（一般不要选） · {short}"
    return f"可捕获输入 · {short}"


def role_default_label(role: Role) -> str:
    if role == "mic":
        return "系统默认麦克风"
    if role == "output":
        return "系统默认播放设备"
    return "系统默认（自动选可捕获的声音）"


def role_hint(role: Role) -> str:
    if role == "mic":
        return "选你说话用的麦克风"
    if role == "output":
        return "翻译语音从哪播出；要进游戏就选虚拟声卡（如 CABLE Input）"
    return "选要翻译的游戏/系统声音来源（Loopback / monitor）"
